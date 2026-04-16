# Nginx 入口 API

> **导航**: [文档中心](../README.md) | [TrajProxy API](api_proxy.md) | [ID 设计规范](../design/identifier_design.md)

Nginx 作为统一入口，根据请求路径分发到 LiteLLM 网关或 TrajProxy 服务。

---

## 服务架构

```
┌─────────────────────────────────────────────────────────────┐
│                     Nginx (端口 12345)                       │
│                      统一入口，路由分发                        │
└─────────────────────────────────────────────────────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          │                   │                   │
          ▼                   ▼                   ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│ LiteLLM (4000)  │  │ LiteLLM (4000)  │  │ TrajProxy       │
│ 推理请求         │  │ 推理请求         │  │ (12300+)        │
│ /v1/chat/...    │  │ /v1/messages    │  │ 管理接口         │
└─────────────────┘  └─────────────────┘  └─────────────────┘
```

---

## 端口说明

| 服务 | 端口 | 说明 |
|------|------|------|
| Nginx | 12345 | 对外统一入口 |
| LiteLLM | 4000 | 模型路由网关 |
| TrajProxy | 12300+ | ProxyWorker 实例 |

---

## 路由规则

### 推理请求 → LiteLLM

| 路径 | 后端 | 说明 |
|------|------|------|
| `/s/{run_id}/{session_id}/v1/chat/completions` | LiteLLM | 带 run_id 和 session_id |
| `/s/{session_id}/v1/chat/completions` | LiteLLM | 只带 session_id |
| `/v1/chat/completions` | LiteLLM | 标准推理请求 |
| `/v1/messages` | LiteLLM | Anthropic/Claude 格式 |
| `/*` (其他) | LiteLLM | 兜底路由 |

### 管理请求 → TrajProxy

| 路径 | 后端 | 说明 |
|------|------|------|
| `/models` | TrajProxy | 模型管理 |
| `/trajectory` | TrajProxy | 轨迹查询（旧接口） |
| `/trajectories` | TrajProxy | 轨迹查询（新接口） |
| `/health` | TrajProxy | 健康检查 |

---

## 路径参数提取

Nginx 会从 URL 路径中提取参数并注入到请求头：

### 两段路径格式

```
/s/{run_id}/{session_id}/v1/chat/completions
```

注入请求头：
- `x-run-id: {run_id}`
- `x-session-id: {session_id}`

**示例**：
```bash
POST /s/run_001/task-123/v1/chat/completions
# 后端收到：x-run-id=run_001, x-session-id=task-123
```

### 一段路径格式

```
/s/{session_id}/v1/chat/completions
```

注入请求头：
- `x-session-id: {session_id}`

**示例**：
```bash
POST /s/task-123/v1/chat/completions
# 后端收到：x-session-id=task-123
```

---

## ID 提取优先级

Nginx 从 URL 路径提取 ID 并注入到请求头，LiteLLM 转发时保留这些 header。

### run_id 提取优先级（TrajProxy 层面）

```
优先级1（最高）: 路径参数 /s/{run_id}/{session_id}/v1/... → Nginx 注入 x-run-id header
优先级2: x-run-id Header（Nginx 注入或客户端直接传递）
优先级3: model 参数逗号后：model: "gpt-4,run_001"
优先级4（最低）: 空（不设默认值）
```

### session_id 提取优先级（TrajProxy 层面）

```
优先级1（最高）: 路径参数 /s/{run_id}/{session_id}/v1/... 或 /s/{session_id}/v1/... → Nginx 注入 x-session-id header
优先级2: x-session-id Header（Nginx 注入或客户端直接传递）
优先级3: x-sandbox-traj-id Header
优先级4（最低）: 空
```

> **详细设计**：参见 [ID 设计规范](../design/identifier_design.md)

---

## 流式响应配置

Nginx 对推理请求禁用缓冲，确保流式响应实时传输：

```nginx
proxy_buffering off;
proxy_cache off;
```

---

## 客户端示例

### 带 run_id 和 session_id 的请求

```bash
# OpenAI 格式
curl -X POST http://localhost:12345/s/run_001/task-123/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "qwen3.5-2b", "messages": [{"role": "user", "content": "你好"}]}'

# Anthropic 格式
curl -X POST http://localhost:12345/s/run_001/task-123/v1/messages \
  -H "Content-Type: application/json" \
  -H "anthropic-version: 2023-06-01" \
  -d '{"model": "claude-3-sonnet", "max_tokens": 1024, "messages": [...]}'
```

### 只带 session_id 的请求

```bash
curl -X POST http://localhost:12345/s/task-123/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "qwen3.5-2b,run_001", "messages": [{"role": "user", "content": "你好"}]}'
```

### 标准请求（无路径参数）

```bash
curl -X POST http://localhost:12345/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "x-run-id: run_001" \
  -H "x-session-id: task-123" \
  -d '{"model": "qwen3.5-2b", "messages": [{"role": "user", "content": "你好"}]}'
```

### 管理接口

```bash
# 模型管理
curl http://localhost:12345/models
curl -X POST http://localhost:12345/models/register -d '{...}'
curl -X DELETE "http://localhost:12345/models?model_name=gpt-4&run_id=run_001"

# 轨迹查询（旧接口）
curl "http://localhost:12345/trajectory?session_id=task-123"

# 轨迹查询（新接口）
curl "http://localhost:12345/trajectories?run_id=run_001"
curl "http://localhost:12345/trajectories/task-123"

# 健康检查
curl http://localhost:12345/health
```

---

## Nginx 配置参考

配置文件位置：`dockers/compose/configs/nginx.conf`（Docker Compose 部署）或 `dockers/allinone/configs/nginx.conf`（All-in-One 部署）

```nginx
# 两段路径：run_id + session_id
location ~ ^/s/([^/]+)/([^/]+)/(v1/.*)$ {
    proxy_pass http://litellm/$3;
    proxy_set_header x-run-id $1;
    proxy_set_header x-session-id $2;
    proxy_buffering off;
    proxy_cache off;
}

# 一段路径：session_id
location ~ ^/s/([^/]+)/(v1/.*)$ {
    proxy_pass http://litellm/$2;
    proxy_set_header x-session-id $1;
    proxy_buffering off;
    proxy_cache off;
}

# 模型管理 → TrajProxy
location /models {
    proxy_pass http://traj_proxy_backend;
}

# 轨迹查询 → TrajProxy
location = /trajectory {
    proxy_pass http://traj_proxy_backend;
}

# 轨迹查询（新接口）→ TrajProxy
location /trajectories {
    proxy_pass http://traj_proxy_backend;
}
```
