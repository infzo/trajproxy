# TrajProxy API

> **导航**: [文档中心](../README.md) | [Nginx 入口 API](api_nginx.md) | [ID 设计规范](../design/identifier_design.md)

TrajProxy 提供模型管理、轨迹查询和聊天补全接口。直接访问 TrajProxy Worker 端口 (12300+)。

---

## 基础信息

- **端口**: 12300+（ProxyWorker 实例）
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

### ID 提取优先级

#### run_id 提取优先级

```
优先级1（最高）: 路径参数（FastAPI query 参数）
优先级2: x-run-id Header（Nginx 注入或客户端传递）
优先级3: model 参数逗号后：model: "gpt-4,run_001"
优先级4（最低）: 空（不设默认值）
```

#### session_id 提取优先级

```
优先级1（最高）: 路径参数（FastAPI query 参数）
优先级2: x-session-id Header（Nginx 注入或客户端传递）
优先级3: x-sandbox-traj-id Header
优先级4（最低）: 空
```

> **详细设计**：参见 [ID 设计规范](../design/identifier_design.md)

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

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| model | string | 是 | - | 模型名称。可带 run_id：`model_name,run_id` |
| messages | array | 是 | - | 消息列表 |
| max_tokens | integer | 否 | - | 最大生成 token 数 |
| temperature | number | 否 | - | 采样温度 (0-2) |
| top_p | number | 否 | - | nucleus sampling 参数 |
| stream | boolean | 否 | false | 是否流式输出 |
| tools | array | 否 | - | 工具定义列表 |
| tool_choice | string/object | 否 | - | 工具选择策略 |

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
| 500 | 服务器内部错误 |

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
