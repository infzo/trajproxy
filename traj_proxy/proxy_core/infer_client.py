import json
import asyncio
import traceback
import time
import requests
import logging
from typing import Dict, Any, AsyncIterator, List, Union, Optional
from concurrent.futures import ThreadPoolExecutor
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from traj_proxy.exceptions import InferServiceError
from traj_proxy.utils.logger import get_logger

logger = get_logger(__name__)

# prompt 参数类型：可以是 string 或 List[int] (token ids)
PromptInput = Union[str, List[int]]

class InferClient:
    """Infer 服务客户端 - 使用 requests 线程池实现 (兼容异步接口)

    融合了最新的接口规范，并针对 vLLM/Ascend NPU 环境下的网络不稳定性
    进行了优化。通过 requests 的 Retry 机制处理临时故障，使用线程池保持异步兼容。
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 300.0,
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
        self._timeout_config = 300 #(connect_timeout, timeout)
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

    async def close(self):
        """释放资源"""
        if self._session:
            self._session.close()
            self._session = None
        self._executor.shutdown(wait=False)

    # --- 核心请求逻辑 ---

    async def _handle_request(self, url: str, request_body: dict) -> Dict[str, Any]:
        """非流式请求处理，将 requests 同步调用转为异步执行"""
        session = await self._get_session()
        loop = asyncio.get_event_loop()

        try:
            t0 = time.monotonic()
            logger.info(f"handle_request: {url=}, {request_body=}.")
            response = await loop.run_in_executor(
                self._executor,
                lambda: session.post(
                    url,
                    json=request_body,
                    headers=self._build_common_headers(),
                    timeout=self._timeout_config,
                    stream=False
                )
            )
            response.raise_for_status()
            result = response.json()
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.info(f"handle_request_done: elapsed={elapsed_ms:.2f}ms")
            return result

        except requests.exceptions.RequestException as e:
            status_code = getattr(e.response, 'status_code', 'N/A')
            error_text = getattr(e.response, 'text', str(e))
            raise InferServiceError(f"Infer 请求失败 [{status_code}]: {error_text}")
        except Exception as e:
            raise InferServiceError(f"Infer 内部异常: {str(e)}\n{traceback.format_exc()}")

    async def _handle_stream_request(self, url: str, request_body: dict) -> AsyncIterator[Dict[str, Any]]:
        """流式请求处理，返回异步生成器"""
        session = await self._get_session()
        loop = asyncio.get_event_loop()

        try:
            t0 = time.monotonic()
            logger.info(f"handle_stream_request: {url=}, {request_body=}.")
            response = await loop.run_in_executor(
                self._executor,
                lambda: session.post(
                    url,
                    json=request_body,
                    headers=self._build_common_headers(),
                    timeout=self._timeout_config,
                    stream=True
                )
            )
            response.raise_for_status()
            connect_ms = (time.monotonic() - t0) * 1000
            logger.info(f"stream_connected: connect_ms={connect_ms:.2f}ms")

            # 直接 yield from 异步生成器
            async for chunk in self._async_stream_gen(response):
                yield chunk

        except requests.exceptions.RequestException as e:
            status_code = getattr(e.response, 'status_code', 'N/A')
            error_text = getattr(e.response, 'text', str(e))
            raise InferServiceError(f"Infer 请求失败 [{status_code}]: {error_text}")
        except Exception as e:
            raise InferServiceError(f"Infer 内部异常: {str(e)}\n{traceback.format_exc()}")

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

    async def send_completion(self, prompt: PromptInput, model: str, **kwargs) -> Dict[str, Any]:
        url = f"{self.base_url}/completions"
        request_body = self._build_completion_body(prompt, model, stream=False, **kwargs)
        return await self._handle_request(url, request_body)

    async def send_completion_stream(self, prompt: PromptInput, model: str, **kwargs) -> AsyncIterator[Dict[str, Any]]:
        url = f"{self.base_url}/completions"
        request_body = self._build_completion_body(prompt, model, stream=True, **kwargs)
        async for chunk in self._handle_stream_request(url, request_body):
            yield chunk

    def _build_completion_body(self, prompt, model, stream, **kwargs):
        """合并最新版本的 Completion 参数逻辑"""
        body = {
            "model": model,
            "prompt": prompt,
            "max_tokens": kwargs.get("max_tokens") or 100,
            "temperature": kwargs.get("temperature") if kwargs.get("temperature") is not None else 0.7,
            "stream": stream,
            "return_token_ids": True,
            "logprobs": 1
        }
        # 补充最新接口中支持的额外参数
        extra_params = ["top_p", "presence_penalty", "frequency_penalty", "n", "seed", "stop", "echo", "best_of"]
        for p in extra_params:
            if p in kwargs and kwargs[p] is not None:
                body[p] = kwargs[p]
        return body

    # --- OpenAI Chat Completions 接口 ---

    async def send_chat_completion(self, messages: List[Dict[str, Any]], model: str, **kwargs) -> Dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        request_body = self._build_chat_body(messages, model, stream=False, **kwargs)
        return await self._handle_request(url, request_body)

    async def send_chat_completion_stream(self, messages: List[Dict[str, Any]], model: str, **kwargs) -> AsyncIterator[Dict[str, Any]]:
        url = f"{self.base_url}/chat/completions"
        request_body = self._build_chat_body(messages, model, stream=True, **kwargs)
        async for chunk in self._handle_stream_request(url, request_body):
            yield chunk

    def _build_chat_body(self, messages, model, stream, **kwargs):
        """合并最新版本的 Chat 参数逻辑"""
        body = {
            "model": model, 
            "messages": messages, 
            "stream": stream
        }
        # 补充最新接口中更完整的 Chat 参数
        optional_params = [
            "max_tokens", "max_completion_tokens", "temperature", "top_p", 
            "presence_penalty", "frequency_penalty", "tools", "tool_choice", 
            "parallel_tool_calls", "stop", "n", "seed"
        ]
        for p in optional_params:
            if p in kwargs and kwargs[p] is not None:
                body[p] = kwargs[p]
        return body

    def __repr__(self) -> str:
        return f"InferClient(requests_stabilized)(base_url={self.base_url})"