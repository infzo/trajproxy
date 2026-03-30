# TrajProxy API 接口文档

## 概述

TrajProxy 是一个分布式 LLM 请求代理系统，提供 OpenAI 兼容的聊天接口、模型管理和轨迹记录查询功能。

## 基础信息

- **Base URL**: `http://<host>:<port>`
- **端口**:
  - LiteLLM网关: `4000` (API网关)
  - ProxyWorker: `12300+` (聊天、模型管理和轨迹查询，支持多实例)
- **认证**: 部分接口需要 `Authorization: Bearer <token>` 或 `x-session-id` 请求头

---

## 健康检查接口

### ProxyWorker 健康检查

检查代理服务的运行状态。

**接口地址**: `GET /health`

**请求参数**: 无

**响应示例**:
```json
{
  "status": "ok"
}
```

---

## OpenAI Chat 接口

### 聊天补全

处理 OpenAI 格式的聊天补全请求，根据模型名路由到对应的处理器。

**接口地址**: `POST /proxy/v1/chat/completions`

**请求头**:
| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| Content-Type | string | 是 | 固定值: `application/json` |
| Authorization | string | 否 | Bearer Token 认证 |
| x-session-id | string | 否 | 会话 ID，用于请求追踪和模型路由，格式: `run_id;sample_id;task_id`。如果为空，run_id 将使用 model_name|

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
  "presence_penalty": 0,
  "frequency_penalty": 0
}
```

**请求参数说明**:
| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| model | string | 是 | - | 模型名称，必须是已注册的模型 |
| messages | array | 是 | - | 消息列表，OpenAI 格式 |
| max_tokens | integer | 否 | - | 最大生成 token 数 |
| temperature | number | 否 | - | 采样温度，0-2 之间 |
| top_p | number | 否 | - | nucleus sampling 参数 |
| presence_penalty | number | 否 | 0 | 存在惩罚系数 |
| frequency_penalty | number | 否 | 0 | 频率惩罚系数 |

**响应示例 (成功)**:
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
        "content": "你好！我是一个 AI 助手..."
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

**错误响应**:
```json
{
  "detail": "模型 'unknown-model' 未注册"
}
```

**cURL 示例**:
```bash
# 通过 LiteLLM 网关调用
curl -X POST http://localhost:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-1234" \
  -H "x-session-id: app_001;sample_001;task_001" \
  -d '{
    "model": "qwen3.5-2b",
    "messages": [
      {
        "role": "user",
        "content": "你好"
      }
    ]
  }'

# 或直接调用 ProxyWorker
curl -X POST http://localhost:12300/proxy/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "x-session-id: app_001;sample_001;task_001" \
  -d '{
    "model": "qwen3.5-2b",
    "messages": [
      {
        "role": "user",
        "content": "你好"
      }
    ]
  }'
```

---

## 模型管理接口

### 列出可用模型 (OpenAI 格式)

返回已注册的模型列表，格式与 OpenAI API 兼容。

**接口地址**: `GET /proxy/v1/models`

**请求参数**: 无

**响应示例**:
```json
{
  "object": "list",
  "data": [
    {
      "id": "qwen3.5-2b",
      "object": "model",
      "created": 1677610602,
      "owned_by": "organization-owner"
    },
    {
      "id": "gpt-4",
      "object": "model",
      "created": 1677610602,
      "owned_by": "organization-owner"
    }
  ]
}
```

### 注册模型

动态注册一个新的 LLM 模型。

**接口地址**: `POST /proxy/models/register`

**请求头**:
| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| Content-Type | string | 是 | 固定值: `application/json` |

**请求体**:
```json
{
  "run_id": "",
  "model_name": "gpt-4",
  "url": "https://api.openai.com/v1",
  "api_key": "sk-xxxxx",
  "tokenizer_path": "/path/to/tokenizer",
  "token_in_token_out": false,
  "tool_parser": "",
  "reasoning_parser": ""
}
```

**请求参数说明**:
| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| run_id | string | 否 | 运行ID，空字符串表示全局模型，默认为空。如果为空，将使用 model_name 作为 run_id |
| model_name | string | 是 | 模型名称 |
| url | string | 是 | Infer 服务 URL |
| api_key | string | 是 | API 密钥 |
| tokenizer_path | string | 是 | Tokenizer 路径（本地路径或 HuggingFace 模型名称）|
| token_in_token_out | boolean | 否 | 是否使用 Token-in-Token-out 模式，默认 false |
| tool_parser | string | 否 | Tool Parser 名称，支持: deepseek_v3, deepseek_v31, deepseek_v32, qwen3_coder, qwen_xml, glm45, glm47, llama3_json |
| reasoning_parser | string | 否 | Reasoning Parser 名称，支持: deepseek_r1, deepseek_v3, deepseek, qwen3, glm45 |

**响应示例 (成功)**:
```json
{
  "status": "success",
  "model_name": "gpt-4",
  "detail": {
    "model": "gpt-4",
    "tokenizer_path": "/path/to/tokenizer",
    "token_in_token_out": false
  }
}
```

**错误响应**:
```json
{
  "detail": "模型 'gpt-4' 已存在"
}
```

### 删除模型

删除已注册的模型。

**接口地址**: `DELETE /proxy/models/{model_name}`

**路径参数**:
| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| model_name | string | 是 | 要删除的模型名称 |

**查询参数**:
| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| run_id | string | 否 | "" | 运行ID，空字符串表示全局模型 |

**响应示例 (成功)**:
```json
{
  "status": "success",
  "model_name": "gpt-4",
  "deleted": true
}
```

**错误响应**:
```json
{
  "detail": "模型 'gpt-4' 不存在"
}
```

### 列出模型详情

返回所有已注册模型的详细信息。

**接口地址**: `GET /proxy/models`

**请求参数**: 无

**响应示例**:
```json
{
  "status": "success",
  "count": 2,
  "models": [
    {
      "model_name": "qwen3.5-2b",
      "tokenizer_path": "/path/to/tokenizer",
      "token_in_token_out": false,
      "infer_client_url": "https://api.qwen.com"
    },
    {
      "model_name": "gpt-4",
      "tokenizer_path": null,
      "token_in_token_out": false,
      "infer_client_url": "https://api.openai.com/v1"
    }
  ]
}
```

---

## 轨迹记录接口

### 查询轨迹记录

根据 session_id 查询完整的请求轨迹记录。

**接口地址**: `GET /transcript/trajectory`

**查询参数**:
| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| session_id | string | 是 | - | 会话 ID，格式为 `run_id;sample_id;task_id` |
| limit | integer | 否 | 10000 | 最多返回的记录数 |

**响应示例 (成功)**:
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
      "full_conversation_text": null,
      "full_conversation_token_ids": null,
      "start_time": "2024-01-01T00:00:00Z"
    },
    {
      "unique_id": "app_001;sample_001;task_001;req-002",
      "request_id": "req-002",
      "session_id": "app_001;sample_001;task_001",
      "model": "qwen3.5-2b",
      "tokenizer_path": "/path/to/tokenizer",
      "prompt_text": "介绍一下自己",
      "token_ids": [321, 654, 987],
      "full_conversation_text": "你好",
      "full_conversation_token_ids": [123, 456, 789],
      "start_time": "2024-01-01T00:01:00Z"
    }
  ]
}
```

**记录字段说明**:
| 字段名 | 类型 | 说明 |
|--------|------|------|
| unique_id | string | 唯一标识，格式为 `{session_id};{request_id}` |
| request_id | string | 请求 ID |
| session_id | string | 会话 ID |
| model | string | 使用的模型名称 |
| tokenizer_path | string | Tokenizer 路径 |
| prompt_text | string | 提示文本 |
| token_ids | array | Token ID 数组 |
| full_conversation_text | string/null | 完整对话文本（包含历史对话） |
| full_conversation_token_ids | array/null | 完整对话 Token ID 数组 |
| start_time | string | 请求开始时间，ISO 8601 格式 |

**错误响应**:
```json
{
  "detail": "查询轨迹记录失败"
}
```

**cURL 示例**:
```bash
curl "http://localhost:12300/transcript/trajectory?session_id=app_001;sample_001;task_001&limit=100"
```

---

## 状态码说明

| 状态码 | 说明 |
|--------|------|
| 200 | 请求成功 |
| 400 | 请求参数错误 |
| 404 | 资源不存在 (如未注册的模型) |
| 500 | 服务器内部错误 |

---

## Python 客户端示例

```python
import requests

# LiteLLM 网关地址
LITELLM_URL = "http://localhost:4000"
# ProxyWorker 直接访问地址
WORKER_BASE_URL = "http://localhost:12300"

# 1. 聊天补全（通过 LiteLLM 网关）
response = requests.post(
    f"{LITELLM_URL}/v1/chat/completions",
    headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer sk-1234",
        "x-session-id": "app_001;sample_001;task_001"
    },
    json={
        "model": "qwen3.5-2b",
        "messages": [{"role": "user", "content": "你好"}]
    }
)
print(response.json())

# 2. 聊天补全（直接调用 ProxyWorker）
response = requests.post(
    f"{WORKER_BASE_URL}/proxy/v1/chat/completions",
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

# 3. 注册模型
response = requests.post(
    f"{WORKER_BASE_URL}/proxy/models/register",
    json={
        "model_name": "gpt-4",
        "url": "https://api.openai.com",
        "api_key": "sk-xxxxx",
        "tokenizer": "/path/to/tokenizer"
    }
)
print(response.json())

# 4. 查询轨迹
response = requests.get(
    f"{WORKER_BASE_URL}/transcript/trajectory",
    params={
        "session_id": "app_001;sample_001;task_001",
        "limit": 100
    }
)
print(response.json())
```

---

## OpenAI 兼容性说明

本系统提供 OpenAI 兼容的 API，可以直接使用 OpenAI 官方 SDK 进行调用：

### 通过 LiteLLM 网关调用

```python
from openai import OpenAI

# LiteLLM 网关地址
client = OpenAI(
    base_url="http://localhost:4000/v1",
    api_key="sk-1234"  # LiteLLM 认证密钥
)

response = client.chat.completions.create(
    model="qwen3.5-2b",
    messages=[{"role": "user", "content": "你好"}],
    extra_headers={"x-session-id": "app_001;sample_001;task_001"}
)
print(response.choices[0].message.content)
```

### 直接调用 ProxyWorker

```python
from openai import OpenAI

# ProxyWorker 直接访问地址
client = OpenAI(
    base_url="http://localhost:12300/proxy/v1",
    api_key="any-key"  # ProxyWorker 不验证 API Key
)

response = client.chat.completions.create(
    model="qwen3.5-2b",
    messages=[{"role": "user", "content": "你好"}],
    extra_headers={"x-session-id": "app_001;sample_001;task_001"}
)
print(response.choices[0].message.content)
```

---

## 数据库表结构

### request_records 表

存储所有请求轨迹记录。

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | SERIAL | 主键 |
| unique_id | TEXT | 唯一标识 |
| request_id | TEXT | 请求 ID |
| session_id | TEXT | 会话 ID |
| model | TEXT | 模型名称 |
| tokenizer_path | TEXT | Tokenizer 路径 |
| messages | JSONB | 消息列表 |
| prompt_text | TEXT | 提示文本 |
| token_ids | INTEGER[] | Token ID 数组 |
| full_conversation_text | TEXT | 完整对话文本 |
| full_conversation_token_ids | INTEGER[] | 完整对话 Token ID 数组 |
| response | JSONB | 响应数据 |
| response_text | TEXT | 响应文本 |
| response_ids | INTEGER[] | 响应 Token ID 数组 |
| start_time | TIMESTAMP | 请求开始时间 |
| end_time | TIMESTAMP | 请求结束时间 |
| processing_duration_ms | FLOAT | 处理时长（毫秒） |
| prompt_tokens | INTEGER | 输入 Token 数 |
| completion_tokens | INTEGER | 输出 Token 数 |
| total_tokens | INTEGER | 总 Token 数 |
| cache_hit_tokens | INTEGER | 缓存命中 Token 数 |
| error | TEXT | 错误信息 |
| error_traceback | TEXT | 错误堆栈 |
| created_at | TIMESTAMP | 创建时间 |

**索引**:
- `request_records_session_id_idx`: session_id 索引，用于前缀匹配查询
- `request_records_request_id_idx`: request_id 索引
- `request_records_unique_id_idx`: unique_id 索引
- `request_records_start_time_idx`: start_time 索引，倒序排列
