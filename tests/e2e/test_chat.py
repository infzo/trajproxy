"""
聊天补全 API 测试

测试 OpenAI 兼容的聊天补全接口，包括流式和非流式模式
"""

import pytest
import requests
import json
import re

from tests.e2e.config import (
    PROXY_URL,
    DEFAULT_MODEL,
    TEST_MESSAGE,
    STREAM_TIMEOUT,
    get_session_id
)


class TestChatCompletion:
    """聊天补全测试类"""

    @pytest.mark.integration
    def test_chat_completion_non_stream(
        self,
        proxy_client: requests.Session,
        default_headers: dict,
        registered_model_name: str
    ):
        """
        测试非流式聊天补全

        验证点:
        - 返回状态码 200
        - 响应格式符合 OpenAI API 规范
        - 包含有效的响应内容
        """
        response = proxy_client.post(
            f"{PROXY_URL}/proxy/v1/chat/completions",
            headers=default_headers,
            json={
                "model": registered_model_name,
                "messages": [
                    {"role": "user", "content": TEST_MESSAGE}
                ],
                "max_tokens": 100,
                "temperature": 0.7
            }
        )

        assert response.status_code == 200, f"聊天请求失败: {response.text}"

        data = response.json()

        # 验证响应结构
        assert "id" in data, "响应缺少 id 字段"
        assert "choices" in data, "响应缺少 choices 字段"
        assert len(data["choices"]) > 0, "choices 为空"

        # 验证 choice 结构
        choice = data["choices"][0]
        assert "message" in choice, "choice 缺少 message 字段"
        assert "finish_reason" in choice, "choice 缺少 finish_reason 字段"

        # 验证消息内容
        message = choice["message"]
        assert message.get("role") == "assistant", f"角色错误: {message.get('role')}"
        assert "content" in message, "message 缺少 content 字段"
        assert len(message["content"]) > 0, "content 为空"

        # 验证 usage 字段
        if "usage" in data:
            usage = data["usage"]
            assert "prompt_tokens" in usage, "usage 缺少 prompt_tokens"
            assert "completion_tokens" in usage, "usage 缺少 completion_tokens"
            assert "total_tokens" in usage, "usage 缺少 total_tokens"

    @pytest.mark.integration
    @pytest.mark.slow
    def test_chat_completion_stream(
        self,
        proxy_client: requests.Session,
        default_headers: dict,
        registered_model_name: str
    ):
        """
        测试流式聊天补全

        验证点:
        - 返回状态码 200
        - 响应格式为 SSE (Server-Sent Events)
        - 流式数据块格式正确
        - 最后收到 [DONE] 标记
        """
        response = proxy_client.post(
            f"{PROXY_URL}/proxy/v1/chat/completions",
            headers=default_headers,
            json={
                "model": registered_model_name,
                "messages": [
                    {"role": "user", "content": "说一个字：好"}
                ],
                "max_tokens": 10,
                "temperature": 0,
                "stream": True
            },
            stream=True,
            timeout=STREAM_TIMEOUT
        )

        assert response.status_code == 200, f"流式请求失败: {response.text}"

        # 收集所有数据块
        chunks = []
        content_chunks = []

        for line in response.iter_lines(decode_unicode=True):
            if not line:
                continue

            if line.startswith("data: "):
                data_str = line[6:]  # 去掉 "data: " 前缀

                if data_str == "[DONE]":
                    chunks.append("[DONE]")
                    break

                try:
                    chunk = json.loads(data_str)
                    chunks.append(chunk)

                    # 提取内容
                    if "choices" in chunk and len(chunk["choices"]) > 0:
                        delta = chunk["choices"][0].get("delta", {})
                        if "content" in delta:
                            content_chunks.append(delta["content"])
                except json.JSONDecodeError:
                    # 忽略无法解析的行
                    pass

        # 验证收到了数据块
        assert len(chunks) > 0, "未收到任何流式数据块"
        assert chunks[-1] == "[DONE]", "流式响应未以 [DONE] 结束"

        # 验证至少收到了一些内容
        assert len(content_chunks) > 0, "未收到任何内容"

        # 验证内容拼接后不为空
        full_content = "".join(content_chunks)
        assert len(full_content) > 0, "流式内容为空"

    def test_chat_without_session_id(
        self,
        proxy_client: requests.Session,
        registered_model_name: str
    ):
        """
        测试没有 session_id 的请求

        验证点:
        - 返回状态码 200（session_id 可以为空，run_id 将使用 model_name）
        - 响应格式符合 OpenAI API 规范
        """
        response = proxy_client.post(
            f"{PROXY_URL}/proxy/v1/chat/completions",
            headers={"Content-Type": "application/json"},
            json={
                "model": registered_model_name,
                "messages": [
                    {"role": "user", "content": "test"}
                ]
            }
        )

        # session_id 可以为空，run_id 将使用 model_name
        assert response.status_code == 200, f"预期返回 200，实际返回 {response.status_code}: {response.text}"

        data = response.json()
        assert "choices" in data, f"响应缺少 choices 字段: {data}"

    def test_chat_with_invalid_model(
        self,
        proxy_client: requests.Session,
        default_headers: dict
    ):
        """
        测试使用无效模型的请求

        验证点:
        - 返回状态码 404
        - 错误信息提示模型未注册
        """
        response = proxy_client.post(
            f"{PROXY_URL}/proxy/v1/chat/completions",
            headers=default_headers,
            json={
                "model": "non_existent_model_xyz",
                "messages": [
                    {"role": "user", "content": "test"}
                ]
            }
        )

        assert response.status_code == 404, f"预期返回 404，实际返回 {response.status_code}"

        data = response.json()
        assert "未注册" in data.get("detail", "") or "不存在" in data.get("detail", ""), \
            f"错误信息未提示模型未注册: {data}"
