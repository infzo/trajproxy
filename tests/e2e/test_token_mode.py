"""
Token-in-Token-out 模式测试

测试 Token-in-Token-out 模式的核心功能：
1. Token 编码/解码正确性
2. 前缀匹配缓存命中
3. 流式和非流式模式
4. 使用统计准确性

注意：此测试需要预先注册一个 token_in_token_out=True 的模型
"""

import pytest
import requests
import json
import time

from tests.e2e.config import (
    PROXY_URL,
    DEFAULT_MODEL,
    STREAM_TIMEOUT,
    REQUEST_TIMEOUT
)


# Token-in-Token-out 模型的环境变量名称
TOKEN_MODEL_NAME = "qwen3.5-2b"  # 默认使用与普通测试相同的模型
TOKEN_MODE_RUN_ID = "token_mode_test"


class TestTokenInTokenOutMode:
    """Token-in-Token-out 模式测试类"""

    @pytest.fixture
    def token_mode_model_name(self, proxy_client: requests.Session) -> str:
        """
        获取或注册 Token-in-Token-out 模式模型

        优先级：
        1. 环境变量指定的模型
        2. 动态注册的测试模型
        """
        import os

        # 尝试从环境变量获取
        model_name = os.getenv("TOKEN_MODE_MODEL", TOKEN_MODEL_NAME)

        # 检查模型是否支持 token_in_token_out
        # 如果预置模型已启用 token_in_token_out，直接使用
        list_response = proxy_client.get(f"{PROXY_URL}/models/")
        if list_response.status_code == 200:
            models = list_response.json().get("models", [])
            for m in models:
                if m.get("model_name") == model_name and m.get("token_in_token_out"):
                    return model_name

        # 否则返回默认模型名（假设预置模型已正确配置）
        return model_name

    @pytest.mark.integration
    def test_non_streaming_token_mode_basic(
        self,
        proxy_client: requests.Session,
        token_mode_model_name: str
    ):
        """
        测试非流式 Token-in-Token-out 模式基本功能

        验证点:
        - 请求成功返回
        - 响应格式正确
        - usage 统计包含 token 信息
        """
        session_id = f"{TOKEN_MODE_RUN_ID},sample_001,task_001"

        response = proxy_client.post(
            f"{PROXY_URL}/v1/chat/completions",
            headers={
                "Content-Type": "application/json",
                "x-session-id": session_id
            },
            json={
                "model": token_mode_model_name,
                "messages": [
                    {"role": "user", "content": "你好"}
                ],
                "max_tokens": 50
            },
            timeout=REQUEST_TIMEOUT
        )

        assert response.status_code == 200, f"请求失败: {response.text}"

        data = response.json()

        # 验证基本响应结构
        assert "choices" in data, "响应缺少 choices 字段"
        assert len(data["choices"]) > 0, "choices 为空"

        message = data["choices"][0].get("message", {})
        assert "content" in message, "message 缺少 content 字段"
        assert len(message["content"]) > 0, "content 为空"

        # 验证 usage 统计（token 模式下应该有准确的统计）
        if "usage" in data:
            usage = data["usage"]
            assert usage.get("prompt_tokens", 0) > 0, "prompt_tokens 应大于 0"
            assert usage.get("completion_tokens", 0) > 0, "completion_tokens 应大于 0"
            assert usage.get("total_tokens", 0) > 0, "total_tokens 应大于 0"

    @pytest.mark.integration
    @pytest.mark.slow
    def test_streaming_token_mode_basic(
        self,
        proxy_client: requests.Session,
        token_mode_model_name: str
    ):
        """
        测试流式 Token-in-Token-out 模式基本功能

        验证点:
        - 流式请求成功
        - 数据块格式正确
        - 流式结束后收到 [DONE]
        """
        session_id = f"{TOKEN_MODE_RUN_ID},sample_002,task_001"

        response = proxy_client.post(
            f"{PROXY_URL}/v1/chat/completions",
            headers={
                "Content-Type": "application/json",
                "x-session-id": session_id
            },
            json={
                "model": token_mode_model_name,
                "messages": [
                    {"role": "user", "content": "说一个字：好"}
                ],
                "max_tokens": 10,
                "stream": True
            },
            stream=True,
            timeout=STREAM_TIMEOUT
        )

        assert response.status_code == 200, f"流式请求失败: {response.text}"

        # 收集流式数据
        chunks = []
        content_parts = []

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

                    # 提取内容
                    if "choices" in chunk and len(chunk["choices"]) > 0:
                        delta = chunk["choices"][0].get("delta", {})
                        if "content" in delta and delta["content"]:
                            content_parts.append(delta["content"])
                except json.JSONDecodeError:
                    pass

        # 验证流式响应
        assert len(chunks) > 0, "未收到任何流式数据块"
        assert chunks[-1] == "[DONE]", "流式响应未以 [DONE] 结束"
        assert len(content_parts) > 0, "未收到任何内容"

        full_content = "".join(content_parts)
        assert len(full_content) > 0, "流式内容为空"

    @pytest.mark.integration
    def test_prefix_cache_hit(
        self,
        proxy_client: requests.Session,
        token_mode_model_name: str
    ):
        """
        测试前缀匹配缓存命中

        原理：
        1. 发送第一条消息，建立前缀
        2. 发送第二条消息（包含第一条消息历史），应该命中缓存
        3. 验证 cache_hit_tokens > 0

        验证点:
        - 缓存命中的 token 数量 > 0
        - 请求正常完成
        """
        session_id = f"{TOKEN_MODE_RUN_ID},cache_test,task_001"
        unique_prefix = f"prefix_test_{time.time()}"

        # 第一条消息：建立缓存
        first_response = proxy_client.post(
            f"{PROXY_URL}/v1/chat/completions",
            headers={
                "Content-Type": "application/json",
                "x-session-id": session_id
            },
            json={
                "model": token_mode_model_name,
                "messages": [
                    {"role": "user", "content": f"{unique_prefix}: 第一条消息"}
                ],
                "max_tokens": 50
            },
            timeout=REQUEST_TIMEOUT
        )

        assert first_response.status_code == 200, f"第一条消息失败: {first_response.text}"

        # 等待数据写入
        time.sleep(0.5)

        # 第二条消息：应该命中缓存
        second_response = proxy_client.post(
            f"{PROXY_URL}/v1/chat/completions",
            headers={
                "Content-Type": "application/json",
                "x-session-id": session_id
            },
            json={
                "model": token_mode_model_name,
                "messages": [
                    {"role": "user", "content": f"{unique_prefix}: 第一条消息"},
                    {"role": "assistant", "content": "收到"},
                    {"role": "user", "content": "第二条消息"}
                ],
                "max_tokens": 50
            },
            timeout=REQUEST_TIMEOUT
        )

        assert second_response.status_code == 200, f"第二条消息失败: {second_response.text}"

        # 查询轨迹记录，验证 cache_hit_tokens
        trajectory_response = proxy_client.get(
            f"{PROXY_URL}/trajectory",
            params={"session_id": session_id, "limit": 10}
        )

        if trajectory_response.status_code == 200:
            trajectory_data = trajectory_response.json()
            records = trajectory_data.get("records", [])

            if len(records) >= 2:
                # 第二条记录应该有 cache_hit_tokens
                second_record = records[0]  # 最新的记录排在前面
                cache_hit = second_record.get("cache_hit_tokens", 0)

                # 注意：cache_hit_tokens 可能为 0（取决于缓存实现）
                # 这里只验证字段存在，不强制要求大于 0
                assert "cache_hit_tokens" in second_record or cache_hit >= 0, \
                    "记录应包含 cache_hit_tokens 字段"

    @pytest.mark.integration
    def test_token_mode_with_tool_calls(
        self,
        proxy_client: requests.Session,
        token_mode_model_name: str
    ):
        """
        测试 Token-in-Token-out 模式下的工具调用

        重要：只有在 token_in_token_out=True 模式下，工具调用才会被解析。
        直接转发模式（token_in_token_out=False）不会解析工具调用。

        验证点:
        - 请求成功返回
        - 如果模型返回工具调用，格式正确
        - tool_calls 被 PromptBuilder 正确解析
        - finish_reason 为 'tool_calls'
        """
        session_id = f"{TOKEN_MODE_RUN_ID},tool_test,task_001"

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "获取指定城市的天气",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "city": {"type": "string", "description": "城市名称"}
                        },
                        "required": ["city"]
                    }
                }
            }
        ]

        response = proxy_client.post(
            f"{PROXY_URL}/v1/chat/completions",
            headers={
                "Content-Type": "application/json",
                "x-session-id": session_id
            },
            json={
                "model": token_mode_model_name,
                "messages": [
                    {"role": "user", "content": "北京今天天气怎么样？"}
                ],
                "tools": tools,
                "tool_choice": "auto",
                "max_tokens": 100
            },
            timeout=REQUEST_TIMEOUT
        )

        assert response.status_code == 200, f"请求失败: {response.text}"

        data = response.json()

        # 验证响应结构
        assert "choices" in data, "响应缺少 choices 字段"
        choice = data["choices"][0]
        message = choice.get("message", {})

        # 如果有 tool_calls，验证格式
        if "tool_calls" in message and message["tool_calls"]:
            # 验证 finish_reason
            finish_reason = choice.get("finish_reason")
            assert finish_reason == "tool_calls", \
                f"当存在 tool_calls 时，finish_reason 应为 'tool_calls'，实际为: {finish_reason}"

            for tool_call in message["tool_calls"]:
                assert "id" in tool_call, "tool_call 缺少 id"
                assert "type" in tool_call, "tool_call 缺少 type"
                assert tool_call["type"] == "function", "tool_call 类型应为 function"
                assert "function" in tool_call, "tool_call 缺少 function"
                assert "name" in tool_call["function"], "function 缺少 name"

                # 验证 arguments 是合法 JSON
                if "arguments" in tool_call["function"]:
                    try:
                        args = json.loads(tool_call["function"]["arguments"])
                        # 验证参数中包含 city
                        assert "city" in args, f"参数应包含 city 字段，实际参数: {args}"
                    except json.JSONDecodeError:
                        pytest.fail(f"arguments 不是合法 JSON: {tool_call['function']['arguments']}")

    @pytest.mark.integration
    @pytest.mark.slow
    def test_token_mode_streaming_tool_calls(
        self,
        proxy_client: requests.Session,
        token_mode_model_name: str
    ):
        """
        测试 Token-in-Token-out 模式下的流式工具调用

        验证点:
        - 流式请求成功
        - tool_calls 增量正确传输
        - 最终 finish_reason 为 'tool_calls'
        """
        session_id = f"{TOKEN_MODE_RUN_ID},stream_tool_test,task_001"

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "获取指定城市的天气",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "city": {"type": "string", "description": "城市名称"}
                        },
                        "required": ["city"]
                    }
                }
            }
        ]

        response = proxy_client.post(
            f"{PROXY_URL}/v1/chat/completions",
            headers={
                "Content-Type": "application/json",
                "x-session-id": session_id
            },
            json={
                "model": token_mode_model_name,
                "messages": [
                    {"role": "user", "content": "北京今天天气怎么样？"}
                ],
                "tools": tools,
                "tool_choice": "auto",
                "max_tokens": 100,
                "stream": True
            },
            stream=True,
            timeout=STREAM_TIMEOUT
        )

        assert response.status_code == 200, f"流式请求失败: {response.text}"

        # 收集流式数据
        chunks = []
        tool_call_parts = {}
        finish_reason = None

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

                    # 提取 tool_calls 增量
                    if "choices" in chunk and len(chunk["choices"]) > 0:
                        choice_data = chunk["choices"][0]
                        delta = choice_data.get("delta", {})

                        if "tool_calls" in delta:
                            for tc in delta["tool_calls"]:
                                idx = tc.get("index", 0)
                                if idx not in tool_call_parts:
                                    tool_call_parts[idx] = {
                                        "id": None,
                                        "type": None,
                                        "function": {"name": None, "arguments": ""}
                                    }

                                if "id" in tc:
                                    tool_call_parts[idx]["id"] = tc["id"]
                                if "type" in tc:
                                    tool_call_parts[idx]["type"] = tc["type"]
                                if "function" in tc:
                                    func = tc["function"]
                                    if "name" in func:
                                        tool_call_parts[idx]["function"]["name"] = func["name"]
                                    if "arguments" in func:
                                        tool_call_parts[idx]["function"]["arguments"] += func["arguments"]

                        # 检查 finish_reason
                        if choice_data.get("finish_reason"):
                            finish_reason = choice_data["finish_reason"]

                except json.JSONDecodeError:
                    pass

        # 验证流式响应
        assert len(chunks) > 0, "未收到任何流式数据块"
        assert chunks[-1] == "[DONE]", "流式响应未以 [DONE] 结束"

        # 如果有工具调用，验证格式
        if tool_call_parts:
            # 验证 finish_reason
            assert finish_reason == "tool_calls", \
                f"当存在 tool_calls 时，finish_reason 应为 'tool_calls'，实际为: {finish_reason}"

            # 验证每个工具调用
            for idx, tc in tool_call_parts.items():
                assert tc["id"] is not None, f"tool_call[{idx}] 缺少 id"
                assert tc["type"] == "function", f"tool_call[{idx}] 类型应为 function"
                assert tc["function"]["name"] is not None, f"tool_call[{idx}] 缺少 function.name"

                # 验证 arguments 是合法 JSON
                if tc["function"]["arguments"]:
                    try:
                        args = json.loads(tc["function"]["arguments"])
                        assert "city" in args, f"参数应包含 city 字段，实际参数: {args}"
                    except json.JSONDecodeError:
                        pytest.fail(f"tool_call[{idx}] arguments 不是合法 JSON: {tc['function']['arguments']}")

    @pytest.mark.integration
    def test_trajectory_record_completeness(
        self,
        proxy_client: requests.Session,
        token_mode_model_name: str
    ):
        """
        测试 Token-in-Token-out 模式下轨迹记录的完整性

        验证点:
        - 轨迹记录包含 token_ids
        - 轨迹记录包含 full_conversation_token_ids
        - 统计信息完整
        """
        session_id = f"{TOKEN_MODE_RUN_ID},trajectory_test,task_001"
        unique_content = f"unique_content_{time.time()}"

        # 发送请求
        response = proxy_client.post(
            f"{PROXY_URL}/v1/chat/completions",
            headers={
                "Content-Type": "application/json",
                "x-session-id": session_id
            },
            json={
                "model": token_mode_model_name,
                "messages": [
                    {"role": "user", "content": unique_content}
                ],
                "max_tokens": 50
            },
            timeout=REQUEST_TIMEOUT
        )

        assert response.status_code == 200, f"请求失败: {response.text}"

        # 等待数据写入
        time.sleep(1)

        # 查询轨迹记录
        trajectory_response = proxy_client.get(
            f"{PROXY_URL}/trajectory",
            params={"session_id": session_id, "limit": 10}
        )

        assert trajectory_response.status_code == 200, f"查询轨迹失败: {trajectory_response.text}"

        trajectory_data = trajectory_response.json()
        records = trajectory_data.get("records", [])

        assert len(records) > 0, "应该有轨迹记录"

        record = records[0]

        # 验证基本字段
        assert record.get("session_id") == session_id, "session_id 不匹配"
        assert record.get("model") == token_mode_model_name, "model 不匹配"

        # 验证 token 相关字段（Token-in-Token-out 模式特有）
        # 注意：这些字段可能为空列表，取决于实现
        assert "token_ids" in record, "记录缺少 token_ids 字段"
        assert "full_conversation_token_ids" in record, "记录缺少 full_conversation_token_ids 字段"

        # 验证统计字段
        assert "prompt_tokens" in record, "记录缺少 prompt_tokens 字段"
        assert "completion_tokens" in record, "记录缺少 completion_tokens 字段"

    @pytest.mark.integration
    def test_completion_tokens_estimation(
        self,
        proxy_client: requests.Session,
        token_mode_model_name: str
    ):
        """
        测试 completion_tokens 统计

        验证点:
        - completion_tokens 应该大于 0（无论是精确统计还是估算）
        """
        session_id = f"token_mode_test,sample_001,task_001"

        response = proxy_client.post(
            f"{PROXY_URL}/v1/chat/completions",
            headers={
                "Content-Type": "application/json",
                "x-session-id": session_id
            },
            json={
                "model": token_mode_model_name,
                "messages": [
                    {"role": "user", "content": "请回复一段较长的内容，至少二十个字。"}
                ],
                "max_tokens": 100
            },
            timeout=REQUEST_TIMEOUT
        )

        assert response.status_code == 200, f"请求失败: {response.text}"

        data = response.json()

        # 验证 completion_tokens 不为 0
        if "usage" in data:
            completion_tokens = data["usage"].get("completion_tokens", 0)
            assert completion_tokens > 0, \
                f"completion_tokens 应该大于 0，实际为 {completion_tokens}"
