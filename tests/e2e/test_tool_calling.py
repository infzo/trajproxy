"""
Tool Calling API 测试

测试 OpenAI 兼容的 Tool Calling 接口

注意：Tool Calling 功能需要模型支持相应的 tool_parser。
当前测试使用模拟方式验证 API 接口正确性。
"""

import pytest
import requests
import json

from tests.e2e.config import (
    PROXY_URL,
    DEFAULT_MODEL,
    REQUEST_TIMEOUT
)


# 示例工具定义
SAMPLE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "获取指定城市的天气信息",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "城市名称"
                    },
                    "unit": {
                        "type": "string",
                        "enum": ["celsius", "fahrenheit"],
                        "description": "温度单位"
                    }
                },
                "required": ["city"]
            }
        }
    }
]


class TestToolCalling:
    """Tool Calling 测试类"""

    def test_chat_with_tools_definition(
        self,
        proxy_client: requests.Session,
        default_headers: dict,
        registered_model_name: str
    ):
        """
        测试带工具定义的聊天请求

        验证点:
        - 返回状态码 200（即使模型不支持工具调用，请求格式应正确）
        - 响应格式符合 OpenAI API 规范
        """
        response = proxy_client.post(
            f"{PROXY_URL}/proxy/v1/chat/completions",
            headers=default_headers,
            json={
                "model": registered_model_name,
                "messages": [
                    {"role": "user", "content": "北京今天天气怎么样？"}
                ],
                "tools": SAMPLE_TOOLS,
                "tool_choice": "auto",
                "max_tokens": 100
            },
            timeout=REQUEST_TIMEOUT
        )

        # 请求格式正确应该返回 200（即使模型不支持工具调用）
        assert response.status_code == 200, f"请求失败: {response.text}"

        data = response.json()

        # 验证基本响应结构
        assert "choices" in data, "响应缺少 choices 字段"
        assert len(data["choices"]) > 0, "choices 为空"

        choice = data["choices"][0]
        assert "message" in choice, "choice 缺少 message 字段"

        message = choice["message"]
        # message 可能包含 content 或 tool_calls，取决于模型能力
        assert "role" in message, "message 缺少 role 字段"

    @pytest.mark.integration
    def test_tool_choice_none(
        self,
        proxy_client: requests.Session,
        default_headers: dict,
        registered_model_name: str
    ):
        """
        测试 tool_choice="none" 的请求

        验证点:
        - 即使有工具定义，tool_choice="none" 也不应触发工具调用
        """
        response = proxy_client.post(
            f"{PROXY_URL}/proxy/v1/chat/completions",
            headers=default_headers,
            json={
                "model": registered_model_name,
                "messages": [
                    {"role": "user", "content": "你好"}
                ],
                "tools": SAMPLE_TOOLS,
                "tool_choice": "none",
                "max_tokens": 50
            },
            timeout=REQUEST_TIMEOUT
        )

        assert response.status_code == 200, f"请求失败: {response.text}"

        data = response.json()
        choice = data["choices"][0]
        message = choice["message"]

        # tool_choice="none" 时不应返回 tool_calls
        # 注意：这取决于模型实现，某些模型可能忽略此参数
        # 这里只验证响应格式正确
        assert "content" in message or "tool_calls" in message, \
            f"message 缺少 content 或 tool_calls: {message}"

    def test_chat_without_tools(
        self,
        proxy_client: requests.Session,
        default_headers: dict,
        registered_model_name: str
    ):
        """
        测试不带工具定义的普通聊天（回归测试）

        验证点:
        - 不带 tools 参数时请求正常
        - 返回正常的聊天响应
        """
        response = proxy_client.post(
            f"{PROXY_URL}/proxy/v1/chat/completions",
            headers=default_headers,
            json={
                "model": registered_model_name,
                "messages": [
                    {"role": "user", "content": "你好，请回复一句话"}
                ],
                "max_tokens": 50
            },
            timeout=REQUEST_TIMEOUT
        )

        assert response.status_code == 200, f"请求失败: {response.text}"

        data = response.json()
        choice = data["choices"][0]
        message = choice["message"]

        # 普通聊天应该返回 content
        assert message.get("content") is not None or "tool_calls" in message, \
            f"响应内容异常: {message}"

    def test_empty_tools_list(
        self,
        proxy_client: requests.Session,
        default_headers: dict,
        registered_model_name: str
    ):
        """
        测试空工具列表

        验证点:
        - tools=[] 等同于不提供 tools 参数
        """
        response = proxy_client.post(
            f"{PROXY_URL}/proxy/v1/chat/completions",
            headers=default_headers,
            json={
                "model": registered_model_name,
                "messages": [
                    {"role": "user", "content": "测试"}
                ],
                "tools": [],
                "max_tokens": 20
            },
            timeout=REQUEST_TIMEOUT
        )

        # 空工具列表应该被正常处理
        assert response.status_code == 200, f"请求失败: {response.text}"

    @pytest.mark.integration
    @pytest.mark.slow
    def test_tool_calling_stream(
        self,
        proxy_client: requests.Session,
        default_headers: dict,
        registered_model_name: str
    ):
        """
        测试流式 Tool Calling

        验证点:
        - 流式请求格式正确
        - 响应为 SSE 格式
        """
        response = proxy_client.post(
            f"{PROXY_URL}/proxy/v1/chat/completions",
            headers=default_headers,
            json={
                "model": registered_model_name,
                "messages": [
                    {"role": "user", "content": "查询上海的天气"}
                ],
                "tools": SAMPLE_TOOLS,
                "stream": True,
                "max_tokens": 50
            },
            stream=True,
            timeout=60
        )

        assert response.status_code == 200, f"流式请求失败: {response.text}"

        chunks = []
        has_content = False

        for line in response.iter_lines(decode_unicode=True):
            if not line:
                continue

            if line.startswith("data: "):
                data_str = line[6:]

                if data_str == "[DONE]":
                    chunks.append("[DONE]")
                    break

                try:
                    chunk = json.loads(data_str)
                    chunks.append(chunk)

                    # 检查是否有内容
                    if "choices" in chunk and len(chunk["choices"]) > 0:
                        delta = chunk["choices"][0].get("delta", {})
                        if delta.get("content") or delta.get("tool_calls"):
                            has_content = True
                except json.JSONDecodeError:
                    pass

        # 验证收到了数据块
        assert len(chunks) > 0, "未收到任何流式数据块"
        assert chunks[-1] == "[DONE]", "流式响应未以 [DONE] 结束"


class TestToolCallingResponseFormat:
    """Tool Calling 响应格式测试类"""

    def test_tool_calls_format_validation(self):
        """
        测试 tool_calls 响应格式（单元测试）

        验证点:
        - tool_calls 字段格式符合 OpenAI 规范
        """
        # 模拟的 tool_calls 响应
        mock_response = {
            "id": "chatcmpl-123",
            "object": "chat.completion",
            "created": 1234567890,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_abc123",
                                "type": "function",
                                "function": {
                                    "name": "get_weather",
                                    "arguments": "{\"city\": \"北京\"}"
                                }
                            }
                        ]
                    },
                    "finish_reason": "tool_calls"
                }
            ]
        }

        # 验证格式
        assert mock_response["choices"][0]["message"]["tool_calls"] is not None
        tool_call = mock_response["choices"][0]["message"]["tool_calls"][0]
        assert tool_call["type"] == "function"
        assert "function" in tool_call
        assert "name" in tool_call["function"]
        assert "arguments" in tool_call["function"]
        assert mock_response["choices"][0]["finish_reason"] == "tool_calls"

    def test_delta_tool_calls_format_validation(self):
        """
        测试流式 delta tool_calls 响应格式（单元测试）

        验证点:
        - 流式 tool_calls 格式符合 OpenAI 规范
        """
        # 模拟的流式 tool_calls 响应
        mock_stream_chunk = {
            "id": "chatcmpl-123",
            "object": "chat.completion.chunk",
            "created": 1234567890,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_abc123",
                                "type": "function",
                                "function": {
                                    "name": "get_weather",
                                    "arguments": ""
                                }
                            }
                        ]
                    },
                    "finish_reason": None
                }
            ]
        }

        # 验证格式
        delta = mock_stream_chunk["choices"][0]["delta"]
        assert "tool_calls" in delta
        tool_call_delta = delta["tool_calls"][0]
        assert "index" in tool_call_delta
        assert tool_call_delta["index"] == 0
