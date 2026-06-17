# TrajProxy API

> **导航**: [文档中心](../README.md) | [Nginx 入口 API](nginx.md) | [ID 设计规范](../design/modules/identifiers.md)

TrajProxy 提供模型管理、轨迹查询和聊天补全接口。直接访问 TrajProxy Worker 端口 (12300+)。

---

## 基础信息

- **端口**: 12300+（ProxyWorker 实例）
- **认证**: 部分接口需要 `Authorization: Bearer <token>` 或 `x-session-id` 请求头

---

## 目录

- [健康检查](#健康检查)
- [聊天补全](#聊天补全)
  - [ID 提取优先级](#id-提取优先级)
  - [推荐使用模式](#推荐使用模式)
  - [请求参数](#请求参数)
  - [响应格式](#响应格式)
  - [流式响应](#流式响应)
- [模型管理](#模型管理)
  - [注册模型](#注册模型)
  - [删除模型](#删除模型)
  - [查询模型](#查询模型)
- [轨迹查询](#轨迹查询)
- [错误响应](#错误响应)
- [客户端示例](#客户端示例)
- [数据库查询示例](#数据库查询示例)

---

## 健康检查

### GET /health

检查服务运行状态。

**响应**:
```json
{
  "status": "ok"
}
```

---

## 聊天补全

### POST /v1/chat/completions

处理 OpenAI 格式的聊天补全请求。

> **并发限流**: 当并发请求数达到上限时，返回 `429` 状态码，`Retry-After: 3`。

### ID 提取优先级

> **完整规则**（含路径/Header/model 参数优先级）参见 [ID 设计规范](../design/modules/identifiers.md)。

| 字段 | 来源优先级（从高到低） |
|------|----------------------|
| `run_id` | 路径参数 > `x-run-id` header > `model` 逗号后部分 |
| `session_id` | 路径参数 > `x-session-id` header > `x-sandbox-traj-id` header |

### 推荐使用模式

#### 场景一（推荐）：model 参数携带 run-id

```bash
POST /v1/chat/completions
Headers:
  Content-Type: application/json
  x-session-id: task-uuid    # 可选
Body:
  {"model": "qwen3.5-2b,run_001", "messages": [...]}
```

**特点**：
- URL 干净：`/v1/chat/completions`
- run_id 和 model_name 在一起，直观
- session_id 通过 header 传入（可选）

#### 场景二：header 传递 run-id

```bash
POST /v1/chat/completions
Headers:
  Content-Type: application/json
  x-run-id: run_001          # 可选
  x-session-id: task-uuid    # 可选
Body:
  {"model": "qwen3.5-2b", "messages": [...]}
```

### 请求头

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| Content-Type | string | 是 | `application/json` |
| Authorization | string | 否 | Bearer Token |
| x-run-id | string | 否 | 运行 ID（用于模型路由） |
| x-session-id | string | 否 | 会话 ID |
| x-sandbox-traj-id | string | 否 | 会话 ID（优先级低于 x-session-id） |

### 请求体

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

### 参数说明

> 完整参数规范（含 Direct/TITO 两种模式的支持情况和详细说明）参见 [OpenAI 兼容接口规范](openai-compat.md)。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| model | string | 是 | - | 模型名称。可带 run_id：`model_name,run_id` |
| messages | array | 是 | - | 消息列表 |
| max_tokens | integer | 否 | - | 最大生成 token 数 |
| temperature | float | 否 | - | 采样温度 [0, 2) |
| top_p | float | 否 | - | 核采样概率阈值 (0, 1) |
| seed | integer | 否 | - | 随机数种子 |
| presence_penalty | float | 否 | - | 重复度控制 [-2.0, 2.0] |
| stop | string/array | 否 | - | 停止生成条件 |
| stream | boolean | 否 | false | 是否流式输出 |
| stream_options | object | 否 | - | 流式输出配置 |
| tools | array | 否 | - | 工具定义列表 |
| tool_choice | string/object | 否 | - | 工具选择策略 |
| ~~n~~ | integer | 否 | - | ❌ 不支持（固定返回 1 条响应） |
| ~~enable_search~~ | boolean | 否 | - | ❌ 不支持（非标准扩展参数） |

### 响应（非流式）

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

---

## 模型管理

### GET /models

列出所有已注册模型（管理格式，包含详细信息）。

**响应**:
```json
{
  "status": "success",
  "count": 1,
  "models": [
    {
      "run_id": "run_001",
      "model_name": "qwen3.5-2b",
      "tokenizer_path": "/models/qwen",
      "token_in_token_out": true,
      "infer_client_url": "http://infer:8000"
    }
  ]
}
```

### POST /models/register

动态注册新模型（模型会自动同步到所有 Worker）。

**请求体**:

```json
{
  "run_id": "run_001",
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
| run_id | string | 否 | 运行ID，可选 |
| model_name | string | 是 | 模型名称 |
| url | string | 是 | 推理服务 URL |
| api_key | string | 是 | API 密钥 |
| tokenizer_path | string | 否 | Tokenizer 路径 |
| token_in_token_out | boolean | 否 | 是否启用 Token 模式，默认 false |
| tool_parser | string | 否 | 工具解析器名称 |
| reasoning_parser | string | 否 | 推理解析器名称 |

**响应**:

```json
{
  "status": "success",
  "run_id": "run_001",
  "model_name": "gpt-4",
  "detail": {
    "run_id": "run_001",
    "model": "gpt-4",
    "tokenizer_path": "/path/to/tokenizer",
    "token_in_token_out": false,
    "tool_parser": "deepseek_v3",
    "reasoning_parser": "deepseek_r1",
    "sync_info": "模型已持久化到数据库，其他 Worker 已通过 LISTEN/NOTIFY 即时通知"
  }
}
```

### DELETE /models

删除已注册的模型。

**查询参数**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| model_name | string | 是 | 模型名称 |
| run_id | string | 否 | 运行ID，可选 |

**响应**:

```json
{
  "status": "success",
  "run_id": "run_001",
  "model_name": "gpt-4",
  "deleted": true
}
```

### GET /v1/models

列出所有已注册模型（OpenAI 兼容格式）。

**响应**:
```json
{
  "object": "list",
  "data": [
    {
      "id": "qwen3.5-2b,run_001",
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

---

## 轨迹查询

### GET /trajectory（旧接口）

> ⚠️ **注意**：建议使用 `/trajectories` 接口

根据 session_id 查询请求轨迹记录（旧版接口，保持向后兼容）。

**查询参数**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| session_id | string | 是 | - | 会话 ID（原始值，不含 run_id 前缀） |
| limit | integer | 否 | 10000 | 最大返回记录数 |
| fields | string | 否 | - | 字段过滤，逗号分隔。支持 `field_name`（包含）和 `-field_name`（排除） |

**fields 参数示例**:

```bash
# 只返回指定字段
curl "http://localhost:12300/trajectory?session_id=task-123&fields=id,model,messages"

# 排除大字段以加速查询
curl "http://localhost:12300/trajectory?session_id=task-123&fields=-raw_request,-raw_response,-token_ids"
```

**响应**:

```json
{
  "session_id": "task-123",
  "count": 2,
  "records": [
    {
      "id": 1,
      "unique_id": "task-123,req-uuid",
      "request_id": "req-uuid",
      "session_id": "task-123",
      "run_id": "run_001",
      "model": "qwen3.5-2b",
      "prompt_tokens": 10,
      "completion_tokens": 20,
      "total_tokens": 30,
      "cache_hit_tokens": 0,
      "processing_duration_ms": 1500.5,
      "start_time": "2024-01-01T00:00:00Z",
      "end_time": "2024-01-01T00:00:01.5Z",
      "created_at": "2024-01-01T00:00:00Z",
      "error": null,
      "tokenizer_path": "Qwen/Qwen3.5-2B",
      "messages": [...],
      "raw_request": {...},
      "raw_response": {...},
      "text_request": "...",
      "text_response": "...",
      "prompt_text": "...",
      "token_ids": [1, 2, 3],
      "token_request": [1, 2, 3],
      "token_response": [4, 5, 6],
      "response_text": "...",
      "response_ids": [4, 5, 6],
      "full_conversation_text": "...",
      "full_conversation_token_ids": [1, 2, 3, 4, 5, 6],
      "error_traceback": null
    }
  ]
}
```

---

### GET /trajectories（新接口）

查询轨迹列表（可按 run_id 过滤）。

**查询参数**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| run_id | string | 是 | 运行 ID |

**响应**:

```json
{
  "run_id": "run_001",
  "trajectories": [
    {
      "session_id": "task-123",
      "run_id": "run_001",
      "record_count": 5,
      "first_request_time": "2024-01-01T00:00:00Z",
      "last_request_time": "2024-01-01T01:30:00Z"
    },
    {
      "session_id": "task-456",
      "run_id": "run_001",
      "record_count": 3,
      "first_request_time": "2024-01-01T02:00:00Z",
      "last_request_time": "2024-01-01T02:15:00Z"
    }
  ]
}
```

---

### GET /trajectories/{session_id}（新接口）

查询指定轨迹的完整数据（详情）。

**路径参数**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| session_id | string | 是 | 会话 ID |

**查询参数**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| limit | integer | 否 | 10000 | 最大返回记录数 |
| fields | string | 否 | - | 字段过滤，逗号分隔。支持 `field_name`（包含）和 `-field_name`（排除） |

**fields 参数示例**:

```bash
# 只返回指定字段
curl "http://localhost:12300/trajectories/task-123?fields=id,model,messages"

# 排除大字段以加速查询
curl "http://localhost:12300/trajectories/task-123?fields=-raw_request,-raw_response,-token_ids"
```

**响应**:

```json
{
  "session_id": "task-123",
  "records": [
    {
      "id": 1,
      "unique_id": "task-123,req-uuid",
      "request_id": "req-uuid",
      "session_id": "task-123",
      "run_id": "run_001",
      "model": "qwen3.5-2b",
      "prompt_tokens": 10,
      "completion_tokens": 20,
      "total_tokens": 30,
      "cache_hit_tokens": 0,
      "processing_duration_ms": 1500.5,
      "start_time": "2024-01-01T00:00:00Z",
      "end_time": "2024-01-01T00:00:01.5Z",
      "created_at": "2024-01-01T00:00:00Z",
      "error": null,
      "tokenizer_path": "Qwen/Qwen3.5-2B",
      "messages": [...],
      "raw_request": {...},
      "raw_response": {...},
      "prompt_text": "...",
      "token_ids": [1, 2, 3],
      "response_text": "...",
      "response_ids": [4, 5, 6],
      "full_conversation_text": "...",
      "full_conversation_token_ids": [1, 2, 3, 4, 5, 6],
      "error_traceback": null
    }
  ]
}
```

**说明**:
- 只返回未归档的记录（`archive_location IS NULL`）
- 返回完整的详情字段，包括 `messages`、`raw_request`、`raw_response` 等

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
| 422 | 参数验证失败 |
| 429 | 并发限流，服务繁忙（Retry-After: 3） |
| 500 | 服务器内部错误 |
| 504 | 轨迹查询超时（DB 查询或序列化阶段超时） |

### 常见错误示例

**模型未注册**:
```json
{
  "detail": "模型 'unknown-model' 未注册 (run_id=run_001)"
}
```

---

## 客户端示例

### Python (requests)

```python
import requests

BASE_URL = "http://localhost:12300"

# 场景一：model 参数携带 run_id（推荐）
response = requests.post(
    f"{BASE_URL}/v1/chat/completions",
    headers={
        "Content-Type": "application/json",
        "x-session-id": "task-123"
    },
    json={
        "model": "qwen3.5-2b,run_001",
        "messages": [{"role": "user", "content": "你好"}]
    }
)

# 场景二：header 传递 run_id
response = requests.post(
    f"{BASE_URL}/v1/chat/completions",
    headers={
        "Content-Type": "application/json",
        "x-run-id": "run_001",
        "x-session-id": "task-123"
    },
    json={
        "model": "qwen3.5-2b",
        "messages": [{"role": "user", "content": "你好"}]
    }
)

# 查询轨迹
response = requests.get(
    f"{BASE_URL}/trajectory",
    params={"session_id": "task-123"}
)

# 列出模型（管理格式）
response = requests.get(f"{BASE_URL}/models")

# 列出模型（OpenAI 兼容格式）
response = requests.get(f"{BASE_URL}/v1/models")
```

### Python (OpenAI SDK)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:12300/v1",
    api_key="any-key"
)

# 场景一：model 参数携带 run_id
response = client.chat.completions.create(
    model="qwen3.5-2b,run_001",
    messages=[{"role": "user", "content": "你好"}],
    extra_headers={"x-session-id": "task-123"}
)

# 场景二：header 传递 run_id
response = client.chat.completions.create(
    model="qwen3.5-2b",
    messages=[{"role": "user", "content": "你好"}],
    extra_headers={
        "x-run-id": "run_001",
        "x-session-id": "task-123"
    }
)

# 列出模型（OpenAI 兼容格式）
models = client.models.list()
print(models.data)
```

### cURL

```bash
# 聊天补全 - 场景一（推荐）
curl -X POST http://localhost:12300/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "x-session-id: task-123" \
  -d '{"model": "qwen3.5-2b,run_001", "messages": [{"role": "user", "content": "你好"}]}'

# 聊天补全 - 场景二
curl -X POST http://localhost:12300/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "x-run-id: run_001" \
  -H "x-session-id: task-123" \
  -d '{"model": "qwen3.5-2b", "messages": [{"role": "user", "content": "你好"}]}'

# 查询轨迹
curl "http://localhost:12300/trajectory?session_id=task-123"

# 模型管理
curl http://localhost:12300/models
curl http://localhost:12300/v1/models
curl -X POST http://localhost:12300/models/register -d '{"model_name": "gpt-4", ...}'
curl -X DELETE "http://localhost:12300/models?model_name=gpt-4&run_id=run_001"
```

---

## 数据库查询示例

```sql
-- 查询特定 run_id 的所有轨迹
SELECT * FROM request_metadata
WHERE run_id = 'run_001';

-- 查询特定会话的轨迹
SELECT * FROM request_metadata
WHERE session_id = 'task-123';

-- 统计各 run_id 的请求数
SELECT
  run_id,
  COUNT(*) as request_count
FROM request_metadata
GROUP BY run_id;
```

---

↑ [返回顶部](#trajproxy-api)
