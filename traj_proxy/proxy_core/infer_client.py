"""
Infer 服务客户端

使用进程级共享的 httpx.AsyncClient 实现纯协程 HTTP 调用。
所有 InferClient 实例共享同一个 httpx.AsyncClient（TCP 连接池），
httpx 内部按 (scheme, host, port) 自动管理连接，天然支持多后端复用。

模块级函数：
  - get_shared_client(): 获取共享的 httpx.AsyncClient（懒初始化）
  - close_shared_client(): 关闭共享 client（仅 Worker shutdown 时调用）
"""

import json
import asyncio
import traceback
import httpx
from typing import Dict, Any, AsyncIterator, List, Union, Optional

from traj_proxy.exceptions import InferServiceError, InferTimeoutError
from traj_proxy.utils.logger import get_logger
from traj_proxy.utils.config import get_infer_client_config
from traj_proxy.observability.decorators import observe_inference, observe_inference_stream

logger = get_logger(__name__)

# prompt 参数类型：可以是 string 或 List[int] (token ids)
PromptInput = Union[str, List[int]]

# 需要重试的 HTTP 状态码
_RETRY_STATUS_CODES = {502, 503, 504}

# ========== 进程级共享 httpx.AsyncClient ==========

_shared_client: Optional[httpx.AsyncClient] = None
_shared_client_lock: Optional[asyncio.Lock] = None


async def get_shared_client() -> httpx.AsyncClient:
    """获取进程级共享的 httpx.AsyncClient（懒初始化，线程安全）

    所有 InferClient 实例共享同一个 client，httpx 内部按
    (scheme, host, port) 自动管理连接池，天然支持多后端复用。

    Returns:
        共享的 httpx.AsyncClient 实例
    """
    global _shared_client, _shared_client_lock

    if _shared_client is not None:
        return _shared_client

    # 懒创建锁（asyncio.Lock 不能在事件循环外创建）
    if _shared_client_lock is None:
        _shared_client_lock = asyncio.Lock()

    async with _shared_client_lock:
        if _shared_client is None:
            config = get_infer_client_config()
            effective_max = config.get("max_connections", 1000)
            effective_max = effective_max if effective_max > 0 else None

            _shared_client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=config.get("connect_timeout", 60),
                    read=config.get("read_timeout", 600),
                    write=60.0,
                    pool=60.0,
                ),
                limits=httpx.Limits(
                    max_connections=effective_max,
                    max_keepalive_connections=(
                        max(effective_max // 2, 4) if effective_max else 16
                    ),
                ),
            )
            limits_str = f"max_connections={effective_max}" if effective_max else "unlimited"
            logger.info(f"共享 httpx.AsyncClient 已初始化 ({limits_str})")

    return _shared_client


async def close_shared_client() -> None:
    """关闭进程级共享 httpx.AsyncClient（仅 Worker shutdown 时调用）"""
    global _shared_client, _shared_client_lock

    if _shared_client is not None:
        await _shared_client.aclose()
        _shared_client = None
        _shared_client_lock = None
        logger.info("共享 httpx.AsyncClient 已关闭")


# ========== InferClient ==========


class InferClient:
    """Infer 服务客户端 - 轻量级适配器

    引用进程级共享的 httpx.AsyncClient，自身不持有也不管理 HTTP 连接。
    close() 为 no-op，因此可被 ProcessorManager 安全淘汰而不影响
    其他正在进行的请求。

    针对 502/503/504 错误实现手动重试。
    """

    # Chat Completions 参数映射到 Completions 时的不兼容参数
    _CHAT_TO_COMPLETION_INCOMPATIBLE = frozenset({
        # 后端兼容性问题
        "response_format",       # 后端可能不支持
        "logit_bias",            # token_id 映射不同
        "echo",                 # 语义不同
        # OpenAI 标准参数 - completions 不支持
        "tools",                 # 工具调用
        "tool_choice",           # 工具选择
        "parallel_tool_calls",   # 并行工具调用
        "reasoning_effort",      # 推理模型专用
        # vLLM 扩展参数 - completions 不支持
        "include_reasoning",     # 推理解析
        "add_generation_prompt", # chat template
        "continue_final_message",# chat template
        "documents",             # RAG 文档
        "chat_template",         # 自定义模板
        "chat_template_kwargs",  # 模板参数
        "mm_processor_kwargs",   # 多模态处理器
    })

    # 参数名映射（Chat -> Completion）
    _CHAT_TO_COMPLETION_MAPPINGS = {
        "max_completion_tokens": "max_tokens",
    }

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        max_retries: Optional[int] = None,
        **kwargs
    ):
        """初始化 InferClient

        Args:
            base_url: 推理服务基地址
            api_key: API 密钥
            max_retries: 最大重试次数，默认从配置读取
            **kwargs: 保留向后兼容（timeout/connect_timeout/max_connections 已移至共享 client）
        """
        if base_url is None or api_key is None:
            raise ValueError("InferClient base_url 和 api_key 必须提供")

        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

        if max_retries is None:
            config = get_infer_client_config()
            self._max_retries = config.get("max_retries", 2)
        else:
            self._max_retries = max_retries

        self._last_retry_count = 0

    def _build_headers(self, extra_headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """构建请求 header（每次请求携带 Authorization）"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)
        return headers

    def _transform_chat_params_to_completion(self, kwargs: dict, request_id: str = None) -> dict:
        """将 Chat Completions 参数转换为 Completions 兼容格式
        
        映射优先级高于直接传入：例如同时传入 max_completion_tokens=100 和 max_tokens=200 时，
        映射后的 max_tokens=100 优先（max_completion_tokens 是 OpenAI 新标准参数名）。
        """
        result = {}
        dropped = []
        mapped = []

        # 1. 先处理映射参数（映射值优先写入 result）
        for key, value in kwargs.items():
            if key in self._CHAT_TO_COMPLETION_MAPPINGS:
                new_key = self._CHAT_TO_COMPLETION_MAPPINGS[key]
                result[new_key] = value
                mapped.append(f"{key}->{new_key}")

        # 2. 再处理其余参数（不兼容的丢弃，其余写入 result）
        for key, value in kwargs.items():
            if key in self._CHAT_TO_COMPLETION_INCOMPATIBLE:
                dropped.append(key)
            elif key in self._CHAT_TO_COMPLETION_MAPPINGS:
                continue  # 已在步骤 1 处理
            elif key not in result:
                # 映射已占位的 key 不再覆盖（保证映射优先）
                result[key] = value

        if dropped:
            logger.warning(
                f"Chat->Completion 参数不兼容，已丢弃: {dropped}"
            )
        if mapped:
            logger.info(
                f"Chat->Completion 参数已映射: {mapped}"
            )

        return result

    async def close(self) -> None:
        """No-op: httpx.AsyncClient 由进程级共享 client 管理"""
        pass

    async def __aenter__(self) -> "InferClient":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
        return False

    # --- 异常处理 ---

    def _wrap_request_error(self, e: Exception) -> None:
        """将 httpx 异常转换为业务异常"""
        if isinstance(e, httpx.ConnectTimeout):
            raise InferTimeoutError(f"推理服务连接超时: {self.base_url}") from e
        if isinstance(e, httpx.ReadTimeout):
            raise InferTimeoutError(f"推理服务读取超时: {self.base_url}") from e
        if isinstance(e, httpx.HTTPStatusError):
            raise InferServiceError(
                f"Infer 请求失败 [{e.response.status_code}]: {e.response.text}"
            ) from e
        if isinstance(e, httpx.RequestError):
            raise InferServiceError(f"Infer 请求失败: {str(e)}") from e
        raise InferServiceError(f"Infer 内部异常: {str(e)}\n{traceback.format_exc()}") from e

    # --- 核心请求逻辑 ---

    async def _handle_request(
        self, url: str, request_body: dict,
        extra_headers: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """非流式请求处理，带重试"""
        client = await get_shared_client()
        headers = self._build_headers(extra_headers)

        last_error = None
        for attempt in range(self._max_retries + 1):
            try:
                logger.debug(f"Infer 请求: {url}")
                response = await client.post(url, json=request_body, headers=headers)

                if response.status_code in _RETRY_STATUS_CODES and attempt < self._max_retries:
                    wait = 2 ** attempt
                    logger.warning(
                        f"Infer 请求返回 {response.status_code}，"
                        f"第 {attempt + 1}/{self._max_retries} 次重试，等待 {wait}s"
                    )
                    await asyncio.sleep(wait)
                    continue

                response.raise_for_status()
                self._last_retry_count = attempt
                return response.json()

            except httpx.HTTPStatusError as e:
                last_error = e
                if e.response.status_code in _RETRY_STATUS_CODES and attempt < self._max_retries:
                    wait = 2 ** attempt
                    logger.warning(
                        f"Infer 请求返回 {e.response.status_code}，"
                        f"第 {attempt + 1}/{self._max_retries} 次重试，等待 {wait}s"
                    )
                    await asyncio.sleep(wait)
                    continue
                self._last_retry_count = attempt
                raise self._wrap_request_error(e)
            except httpx.RequestError as e:
                last_error = e
                if attempt < self._max_retries:
                    wait = 2 ** attempt
                    logger.warning(
                        f"Infer 请求异常: {e}，"
                        f"第 {attempt + 1}/{self._max_retries} 次重试，等待 {wait}s"
                    )
                    await asyncio.sleep(wait)
                    continue
                self._last_retry_count = attempt
                raise self._wrap_request_error(e)

        # 不应到达此处，但保险起见
        if last_error:
            raise self._wrap_request_error(last_error)

    async def _handle_stream_request(
        self, url: str, request_body: dict,
        extra_headers: Optional[Dict[str, str]] = None
    ) -> AsyncIterator[Dict[str, Any]]:
        """流式请求处理，返回异步生成器"""
        client = await get_shared_client()
        headers = self._build_headers(extra_headers)

        # 流式请求的重试：仅在建立连接阶段（未 yield 数据时）重试
        has_yielded = False  # 跟踪是否已 yield 过数据，避免重试导致数据重复
        last_error = None
        for attempt in range(self._max_retries + 1):
            try:
                logger.debug(f"Infer 流式请求: {url}")
                async with client.stream("POST", url, json=request_body, headers=headers) as response:
                    if response.status_code in _RETRY_STATUS_CODES:
                        if not has_yielded and attempt < self._max_retries:
                            wait = 2 ** attempt
                            logger.warning(
                                f"Infer 流式请求返回 {response.status_code}，"
                                f"第 {attempt + 1}/{self._max_retries} 次重试，等待 {wait}s"
                            )
                            # 消费响应体以便复用连接
                            await response.aread()
                            await asyncio.sleep(wait)
                            continue
                        # 已 yield 数据或重试次数耗尽：不重试，直接抛出
                        response.raise_for_status()

                    response.raise_for_status()

                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        if line.startswith("data: "):
                            content = line[6:]
                            if content == "[DONE]":
                                return
                            try:
                                parsed = json.loads(content)
                                has_yielded = True  # 标记已 yield，后续出错不再重试
                                yield parsed
                            except json.JSONDecodeError:
                                continue
                    return

            except httpx.HTTPStatusError as e:
                # 非重试状态码（400/500 等）：不重试，直接抛出业务异常
                self._last_retry_count = attempt
                raise self._wrap_request_error(e)
            except httpx.RequestError as e:
                last_error = e
                if not has_yielded and attempt < self._max_retries:
                    wait = 2 ** attempt
                    logger.warning(
                        f"Infer 流式请求异常: {e}，"
                        f"第 {attempt + 1}/{self._max_retries} 次重试，等待 {wait}s"
                    )
                    await asyncio.sleep(wait)
                    continue
                # 已 yield 数据则不重试，避免数据重复
                if has_yielded:
                    logger.error(
                        f"Infer 流式请求中途异常且已部分传输数据，"
                        f"放弃重试以避免数据重复: {e}"
                    )
                self._last_retry_count = attempt
                raise self._wrap_request_error(e)

        if last_error:
            raise self._wrap_request_error(last_error)

    # --- OpenAI Completions 接口 ---

    @observe_inference
    async def send_completion(self, prompt: PromptInput, model: str, extra_headers: Optional[Dict[str, str]] = None, request_id: str = None, **kwargs) -> Dict[str, Any]:
        url = f"{self.base_url}/completions"
        request_body = self._build_completion_body(prompt, model, stream=False, request_id=request_id, **kwargs)
        return await self._handle_request(url, request_body, extra_headers)

    @observe_inference_stream
    async def send_completion_stream(self, prompt: PromptInput, model: str, extra_headers: Optional[Dict[str, str]] = None, request_id: str = None, **kwargs) -> AsyncIterator[Dict[str, Any]]:
        url = f"{self.base_url}/completions"
        request_body = self._build_completion_body(prompt, model, stream=True, request_id=request_id, **kwargs)
        async for chunk in self._handle_stream_request(url, request_body, extra_headers):
            yield chunk

    def _build_completion_body(self, prompt, model, stream, request_id=None, **kwargs):
        """构建 Completion 请求体（黑名单透传模式）

        仅过滤由函数参数直接赋值的字段（model/prompt/stream）
        和固定值字段（logprobs），其余参数一律透传。
        """
        transformed = self._transform_chat_params_to_completion(kwargs, request_id)

        body = {
            "model": model,
            "prompt": prompt,
            "stream": stream,
            "logprobs": 1,
            "return_token_ids": True
        }

        # 已由函数参数或固定值处理的字段，避免重复赋值
        handled_params = {"model", "prompt", "stream", "logprobs"}

        # 其余参数一律透传（包括 max_tokens、temperature 等）
        for k, v in transformed.items():
            if k not in handled_params and v is not None:
                body[k] = v

        return body

    # --- OpenAI Chat Completions 接口 ---

    @observe_inference
    async def send_chat_completion(self, messages: List[Dict[str, Any]], model: str, extra_headers: Optional[Dict[str, str]] = None, **kwargs) -> Dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        request_body = self._build_chat_body(messages, model, stream=False, **kwargs)
        return await self._handle_request(url, request_body, extra_headers)

    @observe_inference_stream
    async def send_chat_completion_stream(self, messages: List[Dict[str, Any]], model: str, extra_headers: Optional[Dict[str, str]] = None, **kwargs) -> AsyncIterator[Dict[str, Any]]:
        url = f"{self.base_url}/chat/completions"
        request_body = self._build_chat_body(messages, model, stream=True, **kwargs)
        async for chunk in self._handle_stream_request(url, request_body, extra_headers):
            yield chunk

    def _build_chat_body(self, messages, model, stream, **kwargs):
        """构建 Chat 请求体（黑名单透传模式）

        仅过滤由函数参数直接赋值的字段（model/messages/stream）
        和固定值字段（logprobs/return_token_ids），其余参数一律透传。
        """
        handled_params = {"model", "messages", "stream", "logprobs", "return_token_ids"}

        body = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "logprobs": 1,
            "return_token_ids": True
        }

        # 其余参数一律透传
        for k, v in kwargs.items():
            if k not in handled_params and v is not None:
                body[k] = v

        return body

    def __repr__(self) -> str:
        return f"InferClient(httpx)(base_url={self.base_url})"
