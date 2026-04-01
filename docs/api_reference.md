# API 参考文档

TrajProxy 提供 OpenAI 兼容的 API 接口，支持聊天补全、模型管理和轨迹查询。

---

## 基础信息

- **Base URL**: `http://<host>:<port>`
- **端口**:
  - Nginx 统一入口: `12345`
  - LiteLLM 网关: `4000`
  - ProxyWorker: `12300+`
- **认证**: 部分接口需要 `Authorization: Bearer <token>` 或 `x-session-id` 请求头

---

## 目录

- [健康检查](#健康检查)
- [聊天补全](#聊天补全)
- [模型管理](#模型管理)
- [轨迹查询](#轨迹查询)
- [错误响应](#错误响应)
- [客户端示例](#客户端示例)

---

## 健康检查

### GET /health

检查服务运行状态。

**请求**: 无

**响应**:
```json
{
  "status": "ok"
}
```

**示例**:
```bash
curl http://localhost:12300/health
```

---

## 聊天补全

### POST /v1/chat/completions

处理 OpenAI 格式的聊天补全请求。

**端点**:
- `POST /v1/chat/completions` - 标准接口
- `POST /s/{session_id}/v1/chat/completions` - 带 session_id 的路径接口

**请求头**:

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| Content-Type | string | 是 | `application/json` |
| Authorization | string | 否 | Bearer Token |
| x-session-id | string | 否 | 会话 ID，格式: `run_id;sample_id;task_id` |

**Session ID 传递方式**（按优先级）:

1. **路径传递**: `/s/{session_id}/v1/chat/completions`
2. **请求头传递**: `x-session-id` 请求头
3. **模型名传递**: `model` 参数格式为 `{model_name}@{session_id}`

**请求体**:

```json
{
  "model": "qwen3.5-2b",
  "messages": [
    {
      "role": "user",
      "content": "你好，请介绍一下自己"
    }
  ],
  "max_tokens": 1024,
  "temperature": 0.7,
  "top_p": 0.9,
  "stream": false
}
```

**参数说明**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| model | string | 是 | - | 模型名称 |
| messages | array | 是 | - | 消息列表 |
| max_tokens | integer | 否 | - | 最大生成 token 数 |
| temperature | number | 否 | - | 采样温度 (0-2) |
| top_p | number | 否 | - | nucleus sampling 参数 |
| stream | boolean | 否 | false | 是否流式输出 |
| tools | array | 否 | - | 工具定义列表 |
| tool_choice | string/object | 否 | - | 工具选择策略 |
| include_reasoning | boolean | 否 | - | 是否返回推理内容 |

**响应（非流式）**:

```json
{
  "id": "chatcmpl-123",
  "object": "chat.completion",
  "created": 1677652288,
  "model": "qwen3.5-2b",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "你好！我是一个 AI 助手...",
        "reasoning": "思考过程...",
        "tool_calls": []
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 10,
    "completion_tokens": 20,
    "total_tokens": 30
  }
}
```

**响应（流式）**:

```
data: {"id":"chatcmpl-123","object":"chat.completion.chunk","choices":[{"delta":{"role":"assistant"},"index":0}]}

data: {"id":"chatcmpl-123","object":"chat.completion.chunk","choices":[{"delta":{"content":"你好"},"index":0}]}

data: [DONE]
```

**示例**:

```bash
# 方式1：通过请求头传递 session_id
curl -X POST http://localhost:12300/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "x-session-id: app_001;sample_001;task_001" \
  -d '{
    "model": "qwen3.5-2b",
    "messages": [{"role": "user", "content": "你好"}]
  }'

# 方式2：通过路径传递 session_id
curl -X POST http://localhost:12300/s/app_001;sample_001;task_001/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3.5-2b",
    "messages": [{"role": "user", "content": "你好"}]
  }'

# 方式3：通过模型名传递 session_id
curl -X POST http://localhost:12300/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3.5-2b@app_001;sample_001;task_001",
    "messages": [{"role": "user", "content": "你好"}]
  }'
```

---

## 模型管理

### GET /models

列出所有已注册的模型。

**请求**: 无

**响应**:

```json
{
  "object": "list",
  "data": [
    {
      "id": "qwen3.5-2b",
      "object": "model",
      "created": 1677610602,
      "owned_by": "traj_proxy"
    }
  ]
}
```

**示例**:
```bash
curl http://localhost:12300/models
```

### POST /models/register

动态注册新模型。

**请求体**:

```json
{
  "run_id": "",
  "model_name": "gpt-4",
  "url": "https://api.openai.com/v1",
  "api_key": "sk-xxxxx",
  "tokenizer_path": "/path/to/tokenizer",
  "token_in_token_out": false,
  "tool_parser": "deepseek_v3",
  "reasoning_parser": "deepseek_r1"
}
```

**参数说明**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| run_id | string | 否 | 运行ID，空字符串表示全局模型 |
| model_name | string | 是 | 模型名称 |
| url | string | 是 | 推理服务 URL |
| api_key | string | 是 | API 密钥 |
| tokenizer_path | string | 否 | Tokenizer 路径 |
| token_in_token_out | boolean | 否 | 是否启用 Token 模式，默认 false |
| tool_parser | string | 否 | 工具解析器名称 |
| reasoning_parser | string | 否 | 推理解析器名称 |

**支持的 Tool Parser**:

| 名称 | 说明 |
|------|------|
| deepseek_v3 | DeepSeek V3 工具调用格式 |
| deepseek_v31 | DeepSeek V3.1 工具调用格式 |
| deepseek_v32 | DeepSeek V3.2 (DSML) 格式 |
| qwen3_coder | Qwen3 Coder 工具调用格式 |
| qwen_xml | Qwen XML 工具调用格式 |
| glm45 | GLM-4-5 工具调用格式 |
| glm47 | GLM-4-7 工具调用格式 |
| llama3_json | Llama3 JSON 工具调用格式 |

**支持的 Reasoning Parser**:

| 名称 | 说明 |
|------|------|
| deepseek_r1 | DeepSeek R1 思维链格式 |
| deepseek_v3 | DeepSeek V3 思维链格式 |
| deepseek | DeepSeek 通用思维链格式 |
| qwen3 | Qwen3 思维链格式 |
| glm45 | GLM-4-5 思维链格式 |

**响应**:

```json
{
  "status": "success",
  "run_id": "",
  "model_name": "gpt-4",
  "detail": {
    "run_id": "",
    "model": "gpt-4",
    "tokenizer_path": "/path/to/tokenizer",
    "token_in_token_out": false,
    "tool_parser": "deepseek_v3",
    "reasoning_parser": "deepseek_r1",
    "sync_info": "模型已持久化到数据库，其他 Worker 将自动同步"
  }
}
```

**示例**:
```bash
curl -X POST http://localhost:12300/models/register \
  -H "Content-Type: application/json" \
  -d '{
    "model_name": "gpt-4",
    "url": "https://api.openai.com/v1",
    "api_key": "sk-xxxxx",
    "tokenizer_path": "",
    "token_in_token_out": false
  }'
```

### DELETE /models

删除已注册的模型。

**查询参数**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| model_name | string | 是 | - | 模型名称 |
| run_id | string | 否 | "" | 运行ID |

**响应**:

```json
{
  "status": "success",
  "run_id": "",
  "model_name": "gpt-4",
  "deleted": true
}
```

**示例**:
```bash
curl -X DELETE "http://localhost:12300/models?model_name=gpt-4"
```

---

## 轨迹查询

### GET /trajectory

根据 session_id 查询请求轨迹记录。

**查询参数**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| session_id | string | 是 | - | 会话 ID |
| limit | integer | 否 | 10000 | 最大返回记录数 |

**响应**:

```json
{
  "session_id": "app_001;sample_001;task_001",
  "count": 2,
  "records": [
    {
      "unique_id": "app_001;sample_001;task_001;req-001",
      "request_id": "req-001",
      "session_id": "app_001;sample_001;task_001",
      "model": "qwen3.5-2b",
      "tokenizer_path": "/path/to/tokenizer",
      "prompt_text": "你好",
      "token_ids": [123, 456, 789],
      "response_text": "你好！",
      "full_conversation_text": null,
      "full_conversation_token_ids": null,
      "prompt_tokens": 10,
      "completion_tokens": 20,
      "cache_hit_tokens": 0,
      "start_time": "2024-01-01T00:00:00Z"
    }
  ]
}
```

**示例**:
```bash
curl "http://localhost:12300/trajectory?session_id=app_001;sample_001;task_001&limit=100"
```

---

## 错误响应

所有错误响应格式：

```json
{
  "detail": "错误描述信息"
}
```

### 常见错误码

| 状态码 | 说明 |
|--------|------|
| 400 | 请求参数错误 |
| 404 | 资源不存在（如模型未注册） |
| 500 | 服务器内部错误 |

### 常见错误示例

**模型未注册**:
```json
{
  "detail": "模型 'unknown-model' 未注册"
}
```

**模型已存在**:
```json
{
  "detail": "模型 'gpt-4' 已存在"
}
```

**模型不存在**:
```json
{
  "detail": "模型 'gpt-4' 不存在 (run_id=)"
}
```

---

## 客户端示例

### Python (requests)

```python
import requests

BASE_URL = "http://localhost:12300"

# 聊天补全
response = requests.post(
    f"{BASE_URL}/v1/chat/completions",
    headers={
        "Content-Type": "application/json",
        "x-session-id": "app_001;sample_001;task_001"
    },
    json={
        "model": "qwen3.5-2b",
        "messages": [{"role": "user", "content": "你好"}]
    }
)
print(response.json())

# 注册模型
response = requests.post(
    f"{BASE_URL}/models/register",
    json={
        "model_name": "gpt-4",
        "url": "https://api.openai.com/v1",
        "api_key": "sk-xxxxx",
        "token_in_token_out": False
    }
)
print(response.json())

# 查询轨迹
response = requests.get(
    f"{BASE_URL}/trajectory",
    params={"session_id": "app_001;sample_001;task_001"}
)
print(response.json())
```

### Python (OpenAI SDK)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:12300/v1",
    api_key="any-key"
)

response = client.chat.completions.create(
    model="qwen3.5-2b",
    messages=[{"role": "user", "content": "你好"}],
    extra_headers={"x-session-id": "app_001;sample_001;task_001"}
)
print(response.choices[0].message.content)
```

### cURL

```bash
# 聊天补全
curl -X POST http://localhost:12300/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "x-session-id: app_001;sample_001;task_001" \
  -d '{"model": "qwen3.5-2b", "messages": [{"role": "user", "content": "你好"}]}'

# 流式聊天
curl -X POST http://localhost:12300/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "qwen3.5-2b", "messages": [{"role": "user", "content": "你好"}], "stream": true}'

# 查看模型列表
curl http://localhost:12300/models

# 注册模型
curl -X POST http://localhost:12300/models/register \
  -H "Content-Type: application/json" \
  -d '{"model_name": "gpt-4", "url": "https://api.openai.com/v1", "api_key": "sk-xxx"}'

# 删除模型
curl -X DELETE "http://localhost:12300/models?model_name=gpt-4"

# 查询轨迹
curl "http://localhost:12300/trajectory?session_id=app_001;sample_001;task_001"
```
