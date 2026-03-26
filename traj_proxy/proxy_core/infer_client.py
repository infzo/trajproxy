"""
InferClient - Infer 服务客户端

调用 OpenAI /v1/completions 接口。
"""

from typing import Dict, Any, AsyncIterator, List, Union, Optional
import httpx
import json

from traj_proxy.exceptions import InferServiceError
from traj_proxy.utils.logger import get_logger

logger = get_logger(__name__)


# prompt 参数类型：可以是 string 或 List[int] (token ids)
PromptInput = Union[str, List[int]]


class InferClient:
    """Infer 服务客户端 - 调用 OpenAI /v1/completions 接口

    提供 send_completion (非流式) 和 send_completion_stream (流式) 两种调用方式。
    prompt 参数支持 string 或 List[int] (token ids)，兼容 OpenAI 标准。
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None
    ):
        """初始化 InferClient

        Args:
            base_url: Infer 服务的 base URL（如 http://localhost:8000）
            api_key: API 密钥
        """
        if base_url is None:
            raise ValueError("InferClient base_url 必须提供")
        if api_key is None:
            raise ValueError("InferClient api_key 必须提供")

        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _build_request_body(
        self,
        prompt: PromptInput,
        model: str,
        stream: bool,
        **kwargs
    ) -> Dict[str, Any]:
        """构建请求体

        Args:
            prompt: 待补全的提示（string 或 List[int]）
            model: 模型名称
            stream: 是否流式
            **kwargs: 其他请求参数

        Returns:
            请求体字典
        """
        # 获取参数并确保非 None 值
        max_tokens = kwargs.get("max_tokens", 100)
        if max_tokens is None:
            max_tokens = 100

        temperature = kwargs.get("temperature", 0.7)
        if temperature is None:
            temperature = 0.7

        request_body = {
            "model": model,
            "prompt": prompt,  # OpenAI 标准：支持 string 或 List[int]
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        if stream:
            request_body["stream"] = True

        # 支持额外参数（只添加非 None 的值）
        extra_params = ["top_p", "presence_penalty", "frequency_penalty", "logprobs", "n", "seed", "stop", "echo", "best_of"]
        for param in extra_params:
            if param in kwargs and kwargs[param] is not None:
                request_body[param] = kwargs[param]

        return request_body

    async def send_completion(
        self,
        prompt: PromptInput,
        model: str,
        **kwargs
    ) -> Dict[str, Any]:
        """发送补全请求（非流式）

        Args:
            prompt: 待补全的提示，支持：
                - string: 文本模式
                - List[int]: token ids 模式
            model: 模型名称
            **kwargs: 其他请求参数（如 max_tokens, temperature, logprobs, tools 等）

        Returns:
            Infer 服务返回的 JSON 响应

        Raises:
            InferServiceError: 当请求失败时抛出
        """
        url = f"{self.base_url}/v1/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        request_body = self._build_request_body(prompt, model, stream=False, **kwargs)

        logger.debug(f"发送 Infer 请求: url={url}, model={model}, prompt_type={type(prompt).__name__}")
        try:
            async with httpx.AsyncClient(timeout=300) as client:
                response = await client.post(url, json=request_body, headers=headers)
                response.raise_for_status()
                logger.debug(f"Infer 响应: status={response.status_code}")
                return response.json()
        except httpx.HTTPStatusError as e:
            raise InferServiceError(f"Infer 服务请求失败: {e.response.status_code} - {e.response.text}")
        except Exception as e:
            raise InferServiceError(f"Infer 服务请求异常: {str(e)}")

    async def send_completion_stream(
        self,
        prompt: PromptInput,
        model: str,
        **kwargs
    ) -> AsyncIterator[Dict[str, Any]]:
        """发送补全请求（流式）

        Args:
            prompt: 待补全的提示，支持：
                - string: 文本模式
                - List[int]: token ids 模式
            model: 模型名称
            **kwargs: 其他请求参数

        Yields:
            每个流式响应的 JSON 数据

        Raises:
            InferServiceError: 当请求失败时抛出
        """
        url = f"{self.base_url}/v1/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        request_body = self._build_request_body(prompt, model, stream=True, **kwargs)

        try:
            async with httpx.AsyncClient(timeout=300) as client:
                async with client.stream("POST", url, json=request_body, headers=headers) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data_str = line[6:]
                            if data_str == "[DONE]":
                                break
                            try:
                                yield json.loads(data_str)
                            except json.JSONDecodeError:
                                continue
        except httpx.HTTPStatusError as e:
            raise InferServiceError(f"Infer 服务流式请求失败: {e.response.status_code} - {e.response.text}")
        except Exception as e:
            raise InferServiceError(f"Infer 服务流式请求异常: {str(e)}")
