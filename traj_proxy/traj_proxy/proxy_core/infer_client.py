"""
InferClient - Infer 服务客户端

调用 OpenAI /v1/completions 接口。
"""

from typing import Dict, Any, AsyncIterator
import httpx
import json
import os
import yaml

from traj_proxy.exceptions import InferServiceError


def load_llm_config() -> dict:
    """加载LLM服务配置"""
    config_path = os.path.join(os.path.dirname(__file__), "..", "workers.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
        return config.get("llm", {})


class InferClient:
    """Infer 服务客户端 - 调用 OpenAI /v1/completions 接口

    提供同步和非流式、流式两种调用方式。
    """

    def __init__(self, base_url: str = None, api_key: str = "sk-dummy"):
        """初始化 InferClient

        Args:
            base_url: Infer 服务的 base URL（如 http://localhost:8000），
                     为 None 时从 workers.yaml 读取
            api_key: API 密钥，为 "sk-dummy" 时从 workers.yaml 读取
        """
        # 从配置文件读取默认值
        if base_url is None or api_key == "sk-dummy":
            config = load_llm_config()
            if base_url is None:
                base_url = config.get("base_url", "http://host.docker.internal:1234")
            if api_key == "sk-dummy":
                api_key = config.get("api_key", "sk-1234")

        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    async def send_text_completion(
        self,
        prompt_text: str,
        model: str,
        **kwargs
    ) -> Dict[str, Any]:
        """发送文本补全请求（非流式）

        Args:
            prompt_text: 待补全的提示文本
            model: 模型名称
            **kwargs: 其他请求参数（如 max_tokens, temperature, tools 等）

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

        # 构建请求体
        request_body = {
            "model": model,
            "prompt": prompt_text,
            "max_tokens": kwargs.get("max_tokens", 100),
            "temperature": kwargs.get("temperature", 0.7),
        }

        # 支持额外参数
        if "top_p" in kwargs:
            request_body["top_p"] = kwargs["top_p"]
        if "presence_penalty" in kwargs:
            request_body["presence_penalty"] = kwargs["presence_penalty"]
        if "frequency_penalty" in kwargs:
            request_body["frequency_penalty"] = kwargs["frequency_penalty"]
        if "tools" in kwargs:
            request_body["tools"] = kwargs["tools"]

        try:
            async with httpx.AsyncClient(timeout=300) as client:
                response = await client.post(url, json=request_body, headers=headers)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            raise InferServiceError(f"Infer 服务请求失败: {e.response.status_code} - {e.response.text}")
        except Exception as e:
            raise InferServiceError(f"Infer 服务请求异常: {str(e)}")

    async def send_text_completion_stream(
        self,
        prompt_text: str,
        model: str,
        **kwargs
    ) -> AsyncIterator[Dict[str, Any]]:
        """发送流式文本补全请求

        Args:
            prompt_text: 待补全的提示文本
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

        request_body = {
            "model": model,
            "prompt": prompt_text,
            "max_tokens": kwargs.get("max_tokens", 100),
            "temperature": kwargs.get("temperature", 0.7),
            "stream": True,
        }

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
