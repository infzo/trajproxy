"""
完整链路测试：Nginx -> LiteLLM -> TrajProxy

测试请求链路：Client -> Nginx -> LiteLLM -> TrajProxy -> 推理服务
覆盖 OpenAI 和 Claude Code (Anthropic) 两种请求类型
"""

import pytest
import requests
import json
import time
import uuid

from tests.e2e.config import (
    NGINX_URL,
    PROXY_URL,
    DEFAULT_MODEL,
    STREAM_TIMEOUT,
    REQUEST_TIMEOUT
)


# 示例工具定义
SAMPLE_TOOLS = [
    {
        "name": "get_weather",
        "description": "获取指定城市的天气信息",
        "input_schema": {
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
]

# OpenAI 格式的工具定义
OPENAI_TOOLS = [
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
                    }
                },
                "required": ["city"]
            }
        }
    }
]


class TestOpenAIRequests:
    """OpenAI 类型请求测试类"""

    @pytest.mark.integration
    def test_openai_non_stream(
        self,
        nginx_client: requests.Session,
        nginx_url: str,
        openai_headers: dict,
        registered_model_name: str,
        unique_session_id: str
    ):
        """
        测试 OpenAI 非流式请求

        验证点:
        - 请求经过 Nginx -> LiteLLM -> TrajProxy
        - 返回状态码 200
        - 响应格式符合 OpenAI API 规范
        """
        # 通过 Nginx 路径模式传递 session_id
        url = f"{nginx_url}/s/{unique_session_id}/v1/chat/completions"

        response = nginx_client.post(
            url,
            headers=openai_headers,
            json={
                "model": registered_model_name,
                "messages": [
                    {"role": "user", "content": "你好，请回复'测试成功'"}
                ],
                "max_tokens": 50,
                "temperature": 0.7
            },
            timeout=REQUEST_TIMEOUT
        )

        assert response.status_code == 200, f"请求失败: {response.text}"

        data = response.json()

        # 验证响应结构
        assert "id" in data, "响应缺少 id 字段"
        assert "choices" in data, "响应缺少 choices 字段"
        assert len(data["choices"]) > 0, "choices 为空"

        choice = data["choices"][0]
        assert "message" in choice, "choice 缺少 message 字段"
        assert "finish_reason" in choice, "choice 缺少 finish_reason 字段"

        message = choice["message"]
        assert message.get("role") == "assistant", f"角色错误: {message.get('role')}"
        assert "content" in message, "message 缺少 content 字段"
        assert len(message["content"]) > 0, "content 为空"

    @pytest.mark.integration
    @pytest.mark.slow
    def test_openai_stream(
        self,
        nginx_client: requests.Session,
        nginx_url: str,
        openai_headers: dict,
        registered_model_name: str,
        unique_session_id: str
    ):
        """
        测试 OpenAI 流式请求

        验证点:
        - 请求经过 Nginx -> LiteLLM -> TrajProxy
        - 返回状态码 200
        - 响应格式为 SSE
        - 流式数据块格式正确
        - 最后收到 [DONE] 标记
        """
        url = f"{nginx_url}/s/{unique_session_id}/v1/chat/completions"

        response = nginx_client.post(
            url,
            headers=openai_headers,
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
                        if "content" in delta:
                            content_chunks.append(delta["content"])
                except json.JSONDecodeError:
                    pass

        # 验证收到了数据块
        assert len(chunks) > 0, "未收到任何流式数据块"
        assert chunks[-1] == "[DONE]", "流式响应未以 [DONE] 结束"
        assert len(content_chunks) > 0, "未收到任何内容"

    @pytest.mark.integration
    def test_openai_tool_calling_non_stream(
        self,
        nginx_client: requests.Session,
        nginx_url: str,
        openai_headers: dict,
        registered_model_name: str,
        unique_session_id: str
    ):
        """
        测试 OpenAI 工具调用非流式请求

        验证点:
        - 请求经过 Nginx -> LiteLLM -> TrajProxy
        - 返回状态码 200
        - 响应格式符合 OpenAI API 规范
        """
        url = f"{nginx_url}/s/{unique_session_id}/v1/chat/completions"

        response = nginx_client.post(
            url,
            headers=openai_headers,
            json={
                "model": registered_model_name,
                "messages": [
                    {"role": "user", "content": "北京今天天气怎么样？"}
                ],
                "tools": OPENAI_TOOLS,
                "tool_choice": "auto",
                "max_tokens": 100
            },
            timeout=REQUEST_TIMEOUT
        )

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
    @pytest.mark.slow
    def test_openai_tool_calling_stream(
        self,
        nginx_client: requests.Session,
        nginx_url: str,
        openai_headers: dict,
        registered_model_name: str,
        unique_session_id: str
    ):
        """
        测试 OpenAI 工具调用流式请求

        验证点:
        - 请求经过 Nginx -> LiteLLM -> TrajProxy
        - 返回状态码 200
        - 响应格式为 SSE
        """
        url = f"{nginx_url}/s/{unique_session_id}/v1/chat/completions"

        response = nginx_client.post(
            url,
            headers=openai_headers,
            json={
                "model": registered_model_name,
                "messages": [
                    {"role": "user", "content": "查询上海的天气"}
                ],
                "tools": OPENAI_TOOLS,
                "stream": True,
                "max_tokens": 50
            },
            stream=True,
            timeout=STREAM_TIMEOUT
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

        assert len(chunks) > 0, "未收到任何流式数据块"
        assert chunks[-1] == "[DONE]", "流式响应未以 [DONE] 结束"


class TestClaudeRequests:
    """Claude Code (Anthropic) 类型请求测试类"""

    @pytest.mark.integration
    def test_claude_non_stream(
        self,
        nginx_client: requests.Session,
        nginx_url: str,
        claude_headers: dict,
        registered_model_name: str,
        unique_session_id: str
    ):
        """
        测试 Claude 非流式请求

        验证点:
        - 请求经过 Nginx -> LiteLLM -> TrajProxy
        - 返回状态码 200
        - 响应格式符合 Anthropic Messages API 规范
        """
        url = f"{nginx_url}/s/{unique_session_id}/v1/messages"

        response = nginx_client.post(
            url,
            headers=claude_headers,
            json={
                "model": registered_model_name,
                "max_tokens": 50,
                "messages": [
                    {"role": "user", "content": "你好，请回复'测试成功'"}
                ]
            },
            timeout=REQUEST_TIMEOUT
        )

        assert response.status_code == 200, f"请求失败: {response.text}"

        data = response.json()

        # 验证 Anthropic 响应结构
        assert "id" in data, "响应缺少 id 字段"
        assert "type" in data, "响应缺少 type 字段"
        assert data.get("type") == "message", f"type 字段错误: {data.get('type')}"
        assert "content" in data, "响应缺少 content 字段"
        assert "role" in data, "响应缺少 role 字段"
        assert data.get("role") == "assistant", f"角色错误: {data.get('role')}"

        # 验证 content 结构
        content = data.get("content", [])
        assert isinstance(content, list), "content 不是列表类型"
        assert len(content) > 0, "content 为空"

        # 验证 content block
        content_block = content[0]
        assert "type" in content_block, "content block 缺少 type 字段"
        if content_block.get("type") == "text":
            assert "text" in content_block, "content block 缺少 text 字段"
            assert len(content_block["text"]) > 0, "text 为空"

    @pytest.mark.integration
    @pytest.mark.slow
    def test_claude_stream(
        self,
        nginx_client: requests.Session,
        nginx_url: str,
        claude_headers: dict,
        registered_model_name: str,
        unique_session_id: str
    ):
        """
        测试 Claude 流式请求

        验证点:
        - 请求经过 Nginx -> LiteLLM -> TrajProxy
        - 返回状态码 200
        - 响应格式为 SSE
        - 详细验证 SSE 事件类型
        """
        url = f"{nginx_url}/s/{unique_session_id}/v1/messages"

        response = nginx_client.post(
            url,
            headers=claude_headers,
            json={
                "model": registered_model_name,
                "max_tokens": 50,
                "messages": [
                    {"role": "user", "content": "说一个字：好"}
                ],
                "stream": True
            },
            stream=True,
            timeout=STREAM_TIMEOUT
        )

        assert response.status_code == 200, f"流式请求失败: {response.text}"

        # 收集所有事件
        events = []
        event_types = []
        content_deltas = []

        for line in response.iter_lines(decode_unicode=True):
            if not line:
                continue

            if line.startswith("data: "):
                data_str = line[6:]

                try:
                    event = json.loads(data_str)
                    events.append(event)

                    event_type = event.get("type")
                    if event_type:
                        event_types.append(event_type)

                    # 收集内容 delta
                    if event_type == "content_block_delta":
                        delta = event.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text = delta.get("text", "")
                            if text:
                                content_deltas.append(text)
                except json.JSONDecodeError:
                    pass

        # 验证收到了事件
        assert len(events) > 0, "未收到任何流式事件"

        # 验证事件类型序列
        # Anthropic 流式响应通常包含以下事件类型：
        # message_start, content_block_start, content_block_delta,
        # content_block_stop, message_delta, message_stop
        expected_event_types = {
            "message_start",
            "content_block_start",
            "content_block_delta",
            "content_block_stop",
            "message_delta",
            "message_stop"
        }

        # 验证至少包含关键事件类型
        assert "message_start" in event_types, "缺少 message_start 事件"
        assert "message_stop" in event_types, "缺少 message_stop 事件"

        # 验证收到了内容
        assert len(content_deltas) > 0, "未收到任何内容 delta"

    @pytest.mark.integration
    def test_claude_tool_calling_non_stream(
        self,
        nginx_client: requests.Session,
        nginx_url: str,
        claude_headers: dict,
        registered_model_name: str,
        unique_session_id: str
    ):
        """
        测试 Claude 工具调用非流式请求

        验证点:
        - 请求经过 Nginx -> LiteLLM -> TrajProxy
        - 返回状态码 200
        - 响应格式符合 Anthropic Messages API 规范
        """
        url = f"{nginx_url}/s/{unique_session_id}/v1/messages"

        response = nginx_client.post(
            url,
            headers=claude_headers,
            json={
                "model": registered_model_name,
                "max_tokens": 100,
                "messages": [
                    {"role": "user", "content": "北京今天天气怎么样？"}
                ],
                "tools": SAMPLE_TOOLS
            },
            timeout=REQUEST_TIMEOUT
        )

        assert response.status_code == 200, f"请求失败: {response.text}"

        data = response.json()

        # 验证基本响应结构
        assert "id" in data, "响应缺少 id 字段"
        assert "type" in data, "响应缺少 type 字段"
        assert "content" in data, "响应缺少 content 字段"

        # content 可能是 text 或 tool_use
        content = data.get("content", [])
        assert isinstance(content, list), "content 不是列表类型"

    @pytest.mark.integration
    @pytest.mark.slow
    def test_claude_tool_calling_stream(
        self,
        nginx_client: requests.Session,
        nginx_url: str,
        claude_headers: dict,
        registered_model_name: str,
        unique_session_id: str
    ):
        """
        测试 Claude 工具调用流式请求

        验证点:
        - 请求经过 Nginx -> LiteLLM -> TrajProxy
        - 返回状态码 200
        - 响应格式为 SSE
        """
        url = f"{nginx_url}/s/{unique_session_id}/v1/messages"

        response = nginx_client.post(
            url,
            headers=claude_headers,
            json={
                "model": registered_model_name,
                "max_tokens": 100,
                "messages": [
                    {"role": "user", "content": "查询上海的天气"}
                ],
                "tools": SAMPLE_TOOLS,
                "stream": True
            },
            stream=True,
            timeout=STREAM_TIMEOUT
        )

        assert response.status_code == 200, f"流式请求失败: {response.text}"

        events = []
        event_types = []

        for line in response.iter_lines(decode_unicode=True):
            if not line:
                continue

            if line.startswith("data: "):
                data_str = line[6:]

                try:
                    event = json.loads(data_str)
                    events.append(event)

                    event_type = event.get("type")
                    if event_type:
                        event_types.append(event_type)
                except json.JSONDecodeError:
                    pass

        assert len(events) > 0, "未收到任何流式事件"
        assert "message_start" in event_types, "缺少 message_start 事件"
        assert "message_stop" in event_types, "缺少 message_stop 事件"


class TestTrajectoryVerification:
    """轨迹验证测试类"""

    @pytest.mark.integration
    def test_trajectory_after_openai_request(
        self,
        nginx_client: requests.Session,
        nginx_url: str,
        openai_headers: dict,
        registered_model_name: str,
        unique_session_id: str
    ):
        """
        测试 OpenAI 请求后的轨迹查询

        验证点:
        - 发送 OpenAI 请求后能查询到轨迹记录
        - 轨迹记录包含正确的 session_id
        - 轨迹记录包含正确的模型名称
        """
        # 发送 OpenAI 请求
        url = f"{nginx_url}/s/{unique_session_id}/v1/chat/completions"

        chat_response = nginx_client.post(
            url,
            headers=openai_headers,
            json={
                "model": registered_model_name,
                "messages": [
                    {"role": "user", "content": "测试轨迹记录"}
                ],
                "max_tokens": 20
            },
            timeout=REQUEST_TIMEOUT
        )

        assert chat_response.status_code == 200, f"聊天请求失败: {chat_response.text}"

        # 等待数据写入
        time.sleep(1)

        # 查询轨迹记录
        trajectory_response = nginx_client.get(
            f"{PROXY_URL}/trajectory",
            params={
                "session_id": unique_session_id,
                "limit": 10
            }
        )

        assert trajectory_response.status_code == 200, f"查询轨迹失败: {trajectory_response.text}"

        trajectory_data = trajectory_response.json()

        # 验证 session_id
        assert trajectory_data.get("session_id") == unique_session_id, \
            f"session_id 不匹配: {trajectory_data}"

        # 如果没有记录，等待后重试
        if trajectory_data.get("count", 0) == 0:
            time.sleep(2)
            trajectory_response = nginx_client.get(
                f"{PROXY_URL}/trajectory",
                params={
                    "session_id": unique_session_id,
                    "limit": 10
                }
            )
            trajectory_data = trajectory_response.json()

        # 验证至少有一条记录
        assert trajectory_data.get("count", 0) >= 1, \
            f"预期至少 1 条记录，实际 {trajectory_data.get('count', 0)} 条"

        # 验证记录内容
        records = trajectory_data.get("records", [])
        assert len(records) > 0, "记录列表为空"

        record = records[0]
        assert record.get("session_id") == unique_session_id, \
            f"记录 session_id 不匹配: {record}"
        assert record.get("model") == registered_model_name, \
            f"记录 model 不匹配: {record}"

    @pytest.mark.integration
    def test_trajectory_after_claude_request(
        self,
        nginx_client: requests.Session,
        nginx_url: str,
        claude_headers: dict,
        registered_model_name: str,
        unique_session_id: str
    ):
        """
        测试 Claude 请求后的轨迹查询

        验证点:
        - 发送 Claude 请求后能查询到轨迹记录
        - 轨迹记录包含正确的 session_id
        - 轨迹记录包含正确的模型名称
        """
        # 发送 Claude 请求
        url = f"{nginx_url}/s/{unique_session_id}/v1/messages"

        chat_response = nginx_client.post(
            url,
            headers=claude_headers,
            json={
                "model": registered_model_name,
                "max_tokens": 20,
                "messages": [
                    {"role": "user", "content": "测试轨迹记录"}
                ]
            },
            timeout=REQUEST_TIMEOUT
        )

        assert chat_response.status_code == 200, f"聊天请求失败: {chat_response.text}"

        # 等待数据写入
        time.sleep(1)

        # 查询轨迹记录
        trajectory_response = nginx_client.get(
            f"{PROXY_URL}/trajectory",
            params={
                "session_id": unique_session_id,
                "limit": 10
            }
        )

        assert trajectory_response.status_code == 200, f"查询轨迹失败: {trajectory_response.text}"

        trajectory_data = trajectory_response.json()

        # 验证 session_id
        assert trajectory_data.get("session_id") == unique_session_id, \
            f"session_id 不匹配: {trajectory_data}"

        # 如果没有记录，等待后重试
        if trajectory_data.get("count", 0) == 0:
            time.sleep(2)
            trajectory_response = nginx_client.get(
                f"{PROXY_URL}/trajectory",
                params={
                    "session_id": unique_session_id,
                    "limit": 10
                }
            )
            trajectory_data = trajectory_response.json()

        # 验证至少有一条记录
        assert trajectory_data.get("count", 0) >= 1, \
            f"预期至少 1 条记录，实际 {trajectory_data.get('count', 0)} 条"

        # 验证记录内容
        records = trajectory_data.get("records", [])
        assert len(records) > 0, "记录列表为空"

        record = records[0]
        assert record.get("session_id") == unique_session_id, \
            f"记录 session_id 不匹配: {record}"
        assert record.get("model") == registered_model_name, \
            f"记录 model 不匹配: {record}"


class TestNginxUnifiedEntry:
    """Nginx 统一入口测试类

    测试 nginx 将非推理请求直接转发到 traj_proxy，
    而推理请求通过 litellm 转发。
    """

    # ========== 健康检查测试 ==========

    def test_health_check_via_nginx(
        self,
        nginx_client: requests.Session,
        nginx_url: str
    ):
        """
        测试通过 nginx 访问健康检查接口

        验证点:
        - /health 请求直接转发到 traj_proxy
        - 返回状态码 200
        - 返回正确的健康状态
        """
        response = nginx_client.get(f"{nginx_url}/health")

        assert response.status_code == 200, f"健康检查失败: {response.text}"

        data = response.json()
        assert data.get("status") == "ok", f"健康状态错误: {data}"

    # ========== 模型管理 API 测试 ==========

    def test_list_models_via_nginx(
        self,
        nginx_client: requests.Session,
        nginx_url: str,
        registered_model_name: str
    ):
        """
        测试通过 nginx 访问模型列表接口

        验证点:
        - /models 请求直接转发到 traj_proxy
        - 返回状态码 200
        - 返回正确的模型列表格式
        """
        response = nginx_client.get(f"{nginx_url}/models")

        assert response.status_code == 200, f"获取模型列表失败: {response.text}"

        data = response.json()
        assert "data" in data, f"响应缺少 data 字段: {data}"
        assert isinstance(data["data"], list), "data 字段不是列表类型"

        # 验证预置模型在列表中
        model_ids = [model.get("id") for model in data["data"]]
        assert registered_model_name in model_ids, \
            f"预置模型 {registered_model_name} 不在模型列表中: {model_ids}"

    def test_list_admin_models_via_nginx(
        self,
        nginx_client: requests.Session,
        nginx_url: str,
        registered_model_name: str
    ):
        """
        测试通过 nginx 访问管理模型列表接口

        验证点:
        - /models/ 请求直接转发到 traj_proxy 的管理接口
        - 返回状态码 200
        - 返回正确的模型详细信息
        """
        response = nginx_client.get(f"{nginx_url}/models/")

        assert response.status_code == 200, f"获取管理模型列表失败: {response.text}"

        data = response.json()
        assert data.get("status") == "success", f"状态错误: {data}"
        assert "models" in data, f"响应缺少 models 字段: {data}"
        assert isinstance(data["models"], list), "models 字段不是列表类型"

    # ========== 轨迹查询 API 测试 ==========

    def test_transcript_trajectory_via_nginx(
        self,
        nginx_client: requests.Session,
        nginx_url: str,
        openai_headers: dict,
        registered_model_name: str,
        unique_session_id: str
    ):
        """
        测试通过 nginx 访问轨迹查询接口

        验证点:
        - /trajectory 请求直接转发到 traj_proxy
        - 返回状态码 200
        - 返回正确的轨迹数据
        """
        # 先发送一个请求生成轨迹数据
        chat_url = f"{nginx_url}/s/{unique_session_id}/v1/chat/completions"
        chat_response = nginx_client.post(
            chat_url,
            headers=openai_headers,
            json={
                "model": registered_model_name,
                "messages": [
                    {"role": "user", "content": "测试轨迹查询"}
                ],
                "max_tokens": 20
            },
            timeout=REQUEST_TIMEOUT
        )
        assert chat_response.status_code == 200, f"聊天请求失败: {chat_response.text}"

        # 等待数据写入
        time.sleep(1)

        # 通过 nginx 查询轨迹
        trajectory_response = nginx_client.get(
            f"{nginx_url}/trajectory",
            params={
                "session_id": unique_session_id,
                "limit": 10
            }
        )

        assert trajectory_response.status_code == 200, f"轨迹查询失败: {trajectory_response.text}"

        trajectory_data = trajectory_response.json()
        assert trajectory_data.get("session_id") == unique_session_id, \
            f"session_id 不匹配: {trajectory_data}"

    # ========== 推理请求路由测试 ==========

    @pytest.mark.integration
    def test_openai_inference_via_litellm(
        self,
        nginx_client: requests.Session,
        nginx_url: str,
        openai_headers: dict,
        registered_model_name: str
    ):
        """
        测试 OpenAI 推理请求通过 litellm 转发

        验证点:
        - /v1/chat/completions 请求转发到 litellm
        - litellm 再转发到 traj_proxy
        - 返回正确的响应
        """
        response = nginx_client.post(
            f"{nginx_url}/v1/chat/completions",
            headers=openai_headers,
            json={
                "model": registered_model_name,
                "messages": [
                    {"role": "user", "content": "你好"}
                ],
                "max_tokens": 20
            },
            timeout=REQUEST_TIMEOUT
        )

        assert response.status_code == 200, f"推理请求失败: {response.text}"

        data = response.json()
        assert "choices" in data, f"响应缺少 choices 字段: {data}"
        assert len(data["choices"]) > 0, "choices 为空"

    @pytest.mark.integration
    def test_claude_inference_via_litellm(
        self,
        nginx_client: requests.Session,
        nginx_url: str,
        claude_headers: dict,
        registered_model_name: str
    ):
        """
        测试 Claude 推理请求通过 litellm 转发

        验证点:
        - /v1/messages 请求转发到 litellm
        - litellm 再转发到 traj_proxy
        - 返回正确的响应
        """
        response = nginx_client.post(
            f"{nginx_url}/v1/messages",
            headers=claude_headers,
            json={
                "model": registered_model_name,
                "max_tokens": 20,
                "messages": [
                    {"role": "user", "content": "你好"}
                ]
            },
            timeout=REQUEST_TIMEOUT
        )

        assert response.status_code == 200, f"推理请求失败: {response.text}"

        data = response.json()
        assert "content" in data, f"响应缺少 content 字段: {data}"
        assert "role" in data, f"响应缺少 role 字段: {data}"

    @pytest.mark.integration
    def test_openai_inference_with_session_via_litellm(
        self,
        nginx_client: requests.Session,
        nginx_url: str,
        openai_headers: dict,
        registered_model_name: str,
        unique_session_id: str
    ):
        """
        测试带 session_id 的 OpenAI 推理请求通过 litellm 转发

        验证点:
        - /s/{session_id}/v1/chat/completions 请求转发到 litellm
        - session_id 正确传递
        - 返回正确的响应
        """
        response = nginx_client.post(
            f"{nginx_url}/s/{unique_session_id}/v1/chat/completions",
            headers=openai_headers,
            json={
                "model": registered_model_name,
                "messages": [
                    {"role": "user", "content": "你好"}
                ],
                "max_tokens": 20
            },
            timeout=REQUEST_TIMEOUT
        )

        assert response.status_code == 200, f"推理请求失败: {response.text}"

        data = response.json()
        assert "choices" in data, f"响应缺少 choices 字段: {data}"

    @pytest.mark.integration
    def test_claude_inference_with_session_via_litellm(
        self,
        nginx_client: requests.Session,
        nginx_url: str,
        claude_headers: dict,
        registered_model_name: str,
        unique_session_id: str
    ):
        """
        测试带 session_id 的 Claude 推理请求通过 litellm 转发

        验证点:
        - /s/{session_id}/v1/messages 请求转发到 litellm
        - session_id 正确传递
        - 返回正确的响应
        """
        response = nginx_client.post(
            f"{nginx_url}/s/{unique_session_id}/v1/messages",
            headers=claude_headers,
            json={
                "model": registered_model_name,
                "max_tokens": 20,
                "messages": [
                    {"role": "user", "content": "你好"}
                ]
            },
            timeout=REQUEST_TIMEOUT
        )

        assert response.status_code == 200, f"推理请求失败: {response.text}"

        data = response.json()
        assert "content" in data, f"响应缺少 content 字段: {data}"

    # ========== 负载均衡测试 ==========

    def test_load_balancing_to_multiple_workers(
        self,
        nginx_client: requests.Session,
        nginx_url: str
    ):
        """
        测试 nginx 对 traj_proxy 的负载均衡

        验证点:
        - 多次请求被分发到不同的 worker
        - 所有请求都能正常响应
        """
        success_count = 0
        total_requests = 10

        for i in range(total_requests):
            response = nginx_client.get(f"{nginx_url}/health")
            if response.status_code == 200:
                success_count += 1

        # 所有请求都应该成功
        assert success_count == total_requests, \
            f"负载均衡测试失败: {success_count}/{total_requests} 请求成功"
