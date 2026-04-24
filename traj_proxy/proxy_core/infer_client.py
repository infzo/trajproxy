import json
import asyncio
import traceback
import requests
import logging
from typing import Dict, Any, AsyncIterator, List, Union, Optional
from concurrent.futures import ThreadPoolExecutor
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from traj_proxy.exceptions import InferServiceError, InferTimeoutError
from traj_proxy.utils.logger import get_logger

logger = get_logger(__name__)

# prompt 参数类型：可以是 string 或 List[int] (token ids)
PromptInput = Union[str, List[int]]


class InferClient:
    """Infer 服务客户端 - 使用 requests 线程池实现 (兼容异步接口)

    融合了最新的接口规范，并针对 vLLM/Ascend NPU 环境下的网络不稳定性
    进行了优化。通过 requests 的 Retry 机制处理临时故障，使用线程池保持异步兼容。
    """

    # Chat Completions 参数映射到 Completions 时的不兼容参数
    _CHAT_TO_COMPLETION_INCOMPATIBLE = frozenset({
        # 后端兼容性问题
        "response_format",       # 后端可能不支持
        "logit_bias",            # token_id 映射不同
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
        "bad_words",             # 屏蔽词列表
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
        max_connections: int = 1000,
        **kwargs
    ):
        """初始化 InferClient"""
        if base_url is None or api_key is None:
            raise ValueError("InferClient base_url 和 api_key 必须提供")

        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

        # requests 专用配置 (连接超时, 读取超时)
        self._timeout_config = (connect_timeout, timeout)
        self._pool_size = max_connections
        
        self._session: Optional[requests.Session] = None
        self._client_lock = asyncio.Lock()
        
        # 使用线程池执行同步调用，避免阻塞异步事件循环
        self._executor = ThreadPoolExecutor(max_workers=max_connections)

    async def _get_session(self) -> requests.Session:
        """初始化并配置带重试机制的 Session (针对 vLLM 稳定性优化)"""
        # 无锁快速路径：已初始化时直接返回，避免高并发下的锁竞争
        if self._session is not None:
            return self._session

        # 需要初始化时才获取锁
        async with self._client_lock:
            if self._session is None:
                self._session = requests.Session()
                # 鲁棒重试策略：针对 502/503/504 进行自动重试
                retry_strategy = Retry(
                    total=2,
                    backoff_factor=1,
                    status_forcelist=[502, 503, 504],
                    allowed_methods=["POST"]
                )
                adapter = HTTPAdapter(
                    pool_connections=self._pool_size,
                    pool_maxsize=self._pool_size,
                    max_retries=retry_strategy
                )
                self._session.mount("http://", adapter)
                self._session.mount("https://", adapter)
                logger.debug(f"[{self.base_url}] InferClient: 已初始化 requests Session")
        return self._session

    def _build_common_headers(self):
        """构建通用请求头"""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

    def _transform_chat_params_to_completion(self, kwargs: dict, request_id: str = None) -> dict:
        """将 Chat Completions 参数转换为 Completions 兼容格式

        Args:
            kwargs: 原始请求参数
            request_id: 请求 ID（用于日志）

        Returns:
            转换后的参数
        """
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

        # 记录日志
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
        if self._session:
            self._session.close()
            self._session = None
        self._executor.shutdown(wait=False)

    async def __aenter__(self) -> "InferClient":
        """异步上下文管理器入口"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口，自动释放资源"""
        await self.close()
        return False

    # --- 异常处理 ---

    def _wrap_request_error(self, e: Exception) -> None:
        """统一的异常包装，将 requests 异常转换为业务异常

        消除 _handle_request / _handle_stream_request 中的重复异常处理。
        使用 raise ... from e 保持异常链可追溯。
        """
        if isinstance(e, requests.exceptions.ConnectTimeout):
            raise InferTimeoutError(f"推理服务连接超时: {self.base_url}") from e
        if isinstance(e, requests.exceptions.ReadTimeout):
            raise InferTimeoutError(f"推理服务读取超时: {self.base_url}") from e
        if isinstance(e, requests.exceptions.RequestException):
            status_code = getattr(e.response, 'status_code', 'N/A')
            error_text = getattr(e.response, 'text', str(e))
            raise InferServiceError(f"Infer 请求失败 [{status_code}]: {error_text}") from e
        raise InferServiceError(f"Infer 内部异常: {str(e)}\n{traceback.format_exc()}") from e

    # --- 核心请求逻辑 ---

    async def _handle_request(self, url: str, request_body: dict, extra_headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """非流式请求处理，将 requests 同步调用转为异步执行"""
        session = await self._get_session()
        loop = asyncio.get_event_loop()

        headers = self._build_common_headers()
        if extra_headers:
            headers.update(extra_headers)

        try:
            logger.debug(f"Infer 请求: {url}")
            response = await loop.run_in_executor(
                self._executor,
                lambda: session.post(
                    url,
                    json=request_body,
                    headers=headers,
                    timeout=self._timeout_config,
                    stream=False
                )
            )
            response.raise_for_status()
            return response.json()

        except Exception as e:
            self._wrap_request_error(e)

    async def _handle_stream_request(self, url: str, request_body: dict, extra_headers: Optional[Dict[str, str]] = None) -> AsyncIterator[Dict[str, Any]]:
        """流式请求处理，返回异步生成器"""
        session = await self._get_session()
        loop = asyncio.get_event_loop()

        headers = self._build_common_headers()
        if extra_headers:
            headers.update(extra_headers)

        try:
            logger.debug(f"Infer 流式请求: {url}")
            response = await loop.run_in_executor(
                self._executor,
                lambda: session.post(
                    url,
                    json=request_body,
                    headers=headers,
                    timeout=self._timeout_config,
                    stream=True
                )
            )
            response.raise_for_status()

            async for chunk in self._async_stream_gen(response):
                yield chunk

        except Exception as e:
            self._wrap_request_error(e)

    async def _async_stream_gen(self, response: requests.Response) -> AsyncIterator[Dict[str, Any]]:
        """异步生成器：解析 SSE 流数据"""
        loop = asyncio.get_event_loop()
        try:
            iterator = response.iter_lines()
            while True:
                # 在线程池中执行迭代，避免阻塞事件循环
                line = await loop.run_in_executor(self._executor, next, iterator, None)
                if line is None:
                    break
                decoded_line = line.decode('utf-8').strip()
                if decoded_line.startswith("data: "):
                    content = decoded_line[6:]
                    if content == "[DONE]":
                        break
                    try:
                        yield json.loads(content)
                    except json.JSONDecodeError:
                        continue
        finally:
            response.close()

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
        """构建 Completion 请求体（黑名单透传模式）

        已显式处理的参数不再通过 kwargs 透传，避免重复。
        Chat Completions 特有参数会被自动过滤/映射。
        """
        # 参数转换（Chat -> Completion）
        transformed = self._transform_chat_params_to_completion(kwargs, request_id)

        # 已在 body 中显式处理的参数
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

        # 透传所有未显式处理的参数
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
        """构建 Chat 请求体（黑名单透传模式）

        已在 body 中显式处理的参数不再通过 kwargs 透传，避免重复。
        其余参数全部透传到推理服务。
        """
        # 已在 body 中显式处理的参数
        handled_params = {"model", "messages", "stream"}

        body = {
            "model": model,
            "messages": messages,
            "stream": stream
        }

        # 透传所有未显式处理的参数
        for k, v in kwargs.items():
            if k not in handled_params and v is not None:
                body[k] = v

        return body

    def __repr__(self) -> str:
        return f"InferClient(requests_stabilized)(base_url={self.base_url})"