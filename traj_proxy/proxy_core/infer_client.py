import json
import asyncio
import traceback
import httpx
import logging
from typing import Dict, Any, AsyncIterator, List, Union, Optional

from traj_proxy.exceptions import InferServiceError, InferTimeoutError
from traj_proxy.utils.logger import get_logger
from traj_proxy.utils.config import get_infer_client_config

logger = get_logger(__name__)

# prompt 参数类型：可以是 string 或 List[int] (token ids)
PromptInput = Union[str, List[int]]

# 需要重试的 HTTP 状态码
_RETRY_STATUS_CODES = {502, 503, 504}


class InferClient:
    """Infer 服务客户端 - 使用 httpx 异步 HTTP 实现

    通过 httpx.AsyncClient 实现纯协程 HTTP 调用，
    无需线程池，直接在事件循环中处理 TCP I/O。
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
        timeout: float = 600.0,
        connect_timeout: float = 60.0,
        max_connections: int = 32,
        max_retries: Optional[int] = None,
        **kwargs
    ):
        if base_url is None or api_key is None:
            raise ValueError("InferClient base_url 和 api_key 必须提供")

        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

        self._timeout = httpx.Timeout(
            connect=connect_timeout,
            read=timeout,
            write=60.0,
            pool=60.0
        )
        # max_connections=0 时不限制连接数，由后端推理服务控制并发
        effective_max = max_connections if max_connections > 0 else None
        self._limits = httpx.Limits(
            max_connections=effective_max,
            max_keepalive_connections=max(effective_max // 2, 4) if effective_max else 16
        )

        if max_retries is None:
            config = get_infer_client_config()
            self._max_retries = config.get("max_retries", 2)
        else:
            self._max_retries = max_retries

        self._client: Optional[httpx.AsyncClient] = None
        self._client_lock = asyncio.Lock()

    async def _get_client(self) -> httpx.AsyncClient:
        """懒初始化 httpx.AsyncClient"""
        if self._client is not None:
            return self._client

        async with self._client_lock:
            if self._client is None:
                self._client = httpx.AsyncClient(
                    timeout=self._timeout,
                    limits=self._limits,
                    headers=self._build_common_headers()
                )
                logger.debug(f"[{self.base_url}] InferClient: 已初始化 httpx.AsyncClient")
        return self._client

    def _build_common_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

    def _transform_chat_params_to_completion(self, kwargs: dict, request_id: str = None) -> dict:
        """将 Chat Completions 参数转换为 Completions 兼容格式"""
        result = {}
        dropped = []
        mapped = []

        for key, value in kwargs.items():
            if key in self._CHAT_TO_COMPLETION_INCOMPATIBLE:
                dropped.append(key)
            elif key in self._CHAT_TO_COMPLETION_MAPPINGS:
                new_key = self._CHAT_TO_COMPLETION_MAPPINGS[key]
                result[new_key] = value
                mapped.append(f"{key}->{new_key}")
            else:
                result[key] = value

        if dropped:
            logger.warning(
                f"[{request_id or 'unknown'}] Chat->Completion 参数不兼容，已丢弃: {dropped}"
            )
        if mapped:
            logger.info(
                f"[{request_id or 'unknown'}] Chat->Completion 参数已映射: {mapped}"
            )

        return result

    async def close(self):
        """释放资源"""
        if self._client:
            await self._client.aclose()
            self._client = None

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
        client = await self._get_client()
        headers = extra_headers or {}

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
                raise self._wrap_request_error(e)

        # 不应到达此处，但保险起见
        if last_error:
            raise self._wrap_request_error(last_error)

    async def _handle_stream_request(
        self, url: str, request_body: dict,
        extra_headers: Optional[Dict[str, str]] = None
    ) -> AsyncIterator[Dict[str, Any]]:
        """流式请求处理，返回异步生成器"""
        client = await self._get_client()
        headers = extra_headers or {}

        # 流式请求的重试：仅在建立连接阶段重试
        last_error = None
        for attempt in range(self._max_retries + 1):
            try:
                logger.debug(f"Infer 流式请求: {url}")
                async with client.stream("POST", url, json=request_body, headers=headers) as response:
                    if response.status_code in _RETRY_STATUS_CODES:
                        if attempt < self._max_retries:
                            wait = 2 ** attempt
                            logger.warning(
                                f"Infer 流式请求返回 {response.status_code}，"
                                f"第 {attempt + 1}/{self._max_retries} 次重试，等待 {wait}s"
                            )
                            # 消费响应体以便复用连接
                            await response.aread()
                            await asyncio.sleep(wait)
                            continue

                    response.raise_for_status()

                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        if line.startswith("data: "):
                            content = line[6:]
                            if content == "[DONE]":
                                return
                            try:
                                yield json.loads(content)
                            except json.JSONDecodeError:
                                continue
                    return

            except httpx.HTTPStatusError as e:
                # 非重试状态码（400/500 等）：不重试，直接抛出业务异常
                raise self._wrap_request_error(e)
            except httpx.RequestError as e:
                last_error = e
                if attempt < self._max_retries:
                    wait = 2 ** attempt
                    logger.warning(
                        f"Infer 流式请求异常: {e}，"
                        f"第 {attempt + 1}/{self._max_retries} 次重试，等待 {wait}s"
                    )
                    await asyncio.sleep(wait)
                    continue
                raise self._wrap_request_error(e)

        if last_error:
            raise self._wrap_request_error(last_error)

    # --- OpenAI Completions 接口 ---

    async def send_completion(self, prompt: PromptInput, model: str, extra_headers: Optional[Dict[str, str]] = None, request_id: str = None, **kwargs) -> Dict[str, Any]:
        url = f"{self.base_url}/completions"
        request_body = self._build_completion_body(prompt, model, stream=False, request_id=request_id, **kwargs)
        return await self._handle_request(url, request_body, extra_headers)

    async def send_completion_stream(self, prompt: PromptInput, model: str, extra_headers: Optional[Dict[str, str]] = None, request_id: str = None, **kwargs) -> AsyncIterator[Dict[str, Any]]:
        url = f"{self.base_url}/completions"
        request_body = self._build_completion_body(prompt, model, stream=True, request_id=request_id, **kwargs)
        async for chunk in self._handle_stream_request(url, request_body, extra_headers):
            yield chunk

    def _build_completion_body(self, prompt, model, stream, request_id=None, **kwargs):
        """构建 Completion 请求体（黑名单透传模式）"""
        transformed = self._transform_chat_params_to_completion(kwargs, request_id)
        handled_params = {"model", "prompt", "stream", "max_tokens", "temperature", "logprobs", "extra_body"}

        body = {
            "model": model,
            "prompt": prompt,
            "max_tokens": transformed.get("max_tokens") if transformed.get("max_tokens") is not None else 100,
            "temperature": transformed.get("temperature") if transformed.get("temperature") is not None else 0.7,
            "stream": stream,
            "logprobs": 1,
            "return_token_ids": True
        }

        for k, v in transformed.items():
            if k not in handled_params and v is not None:
                body[k] = v

        return body

    # --- OpenAI Chat Completions 接口 ---

    async def send_chat_completion(self, messages: List[Dict[str, Any]], model: str, extra_headers: Optional[Dict[str, str]] = None, **kwargs) -> Dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        request_body = self._build_chat_body(messages, model, stream=False, **kwargs)
        return await self._handle_request(url, request_body, extra_headers)

    async def send_chat_completion_stream(self, messages: List[Dict[str, Any]], model: str, extra_headers: Optional[Dict[str, str]] = None, **kwargs) -> AsyncIterator[Dict[str, Any]]:
        url = f"{self.base_url}/chat/completions"
        request_body = self._build_chat_body(messages, model, stream=True, **kwargs)
        async for chunk in self._handle_stream_request(url, request_body, extra_headers):
            yield chunk

    def _build_chat_body(self, messages, model, stream, **kwargs):
        """构建 Chat 请求体（黑名单透传模式）"""
        handled_params = {"model", "messages", "stream"}

        body = {
            "model": model,
            "messages": messages,
            "stream": stream
        }

        for k, v in kwargs.items():
            if k not in handled_params and v is not None:
                body[k] = v

        return body

    def __repr__(self) -> str:
        return f"InferClient(httpx)(base_url={self.base_url})"
