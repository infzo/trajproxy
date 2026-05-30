# 快速上手

> **导航**: [文档中心](../README.md) | [部署指南](deployment.md) | [配置说明](configuration.md)

本文档帮助你在 5 分钟内跑通 TrajProxy 核心流程。更完整的用例参考 E2E 测试（`tests/e2e/`）。

---

## 前置要求

- TrajProxy 服务已启动（本地或 Docker，详见 [部署指南](deployment.md)）
- LLM 推理服务已运行（如 vLLM、Ollama，默认地址 `http://localhost:8000`）
- 已有可用的 `curl` 或 OpenAI SDK 客户端

---

## Step 1：确认服务就绪

```bash
# 健康检查（Proxy 直连层，端口 12300）
curl http://localhost:12300/health

# 若使用 Docker Compose / All-in-One 部署，还需确认 Nginx 入口（端口 12345）
curl http://localhost:12345/health
```

预期响应：`{"status": "ok"}`

---

## Step 2：注册模型

向 TrajProxy 注册一个推理服务上的模型（两种模式二选一）：

### 模式 A：直接转发（轻量，推荐新手）

```bash
curl -X POST http://localhost:12300/models/register \
  -H "Content-Type: application/json" \
  -d '{
    "model_name": "qwen3.5-2b",
    "url": "http://localhost:8000/v1",
    "api_key": "sk-1234"
  }'
```

### 模式 B：Token-in-Token-out（带前缀缓存，需要 tokenizer）

```bash
curl -X POST http://localhost:12300/models/register \
  -H "Content-Type: application/json" \
  -d '{
    "model_name": "qwen3.5-2b",
    "run_id": "run-001",
    "url": "http://localhost:8000/v1",
    "api_key": "sk-1234",
    "tokenizer_path": "Qwen/Qwen3.5-2B",
    "token_in_token_out": true,
    "tool_parser": "qwen3_coder",
    "reasoning_parser": "qwen3"
  }'
```

> **说明**：`run_id` 用于多租户/实验隔离，不提供时为空。模型注册后会自动通过 LISTEN/NOTIFY 同步到所有 Worker。

---

## Step 3：查询已注册模型

```bash
# 管理格式（含 URL、tokenizer 等详细信息）
curl http://localhost:12300/models

# OpenAI 兼容格式
curl http://localhost:12300/v1/models
```

---

## Step 4：发送聊天请求

### 场景 A：标准 OpenAI 调用（通过 Header 传 run_id / session_id）

```bash
curl -X POST http://localhost:12300/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "x-run-id: run-001" \
  -H "x-session-id: $(uuidgen)" \
  -d '{
    "model": "qwen3.5-2b",
    "messages": [{"role": "user", "content": "你好"}]
  }'
```

### 场景 B：run_id 内嵌在 model 参数中

```bash
curl -X POST http://localhost:12300/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "x-session-id: task-001" \
  -d '{
    "model": "qwen3.5-2b,run-001",
    "messages": [{"role": "user", "content": "你好"}]
  }'
```

### 场景 C：通过 Nginx 入口（仅 Docker 部署可用）

Nginx 支持从 URL 路径提取 `run_id` / `session_id`：

```bash
# 两段路径：/s/{run_id}/{session_id}/v1/...
curl -X POST http://localhost:12345/s/run-001/task-001/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "qwen3.5-2b", "messages": [{"role": "user", "content": "你好"}]}'

# 一段路径：/s/{session_id}/v1/...
curl -X POST http://localhost:12345/s/task-001/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "qwen3.5-2b", "messages": [{"role": "user", "content": "你好"}]}'
```

---

## Step 5：查询对话轨迹

TrajProxy 会自动保存每次请求的完整轨迹，支持按 `run_id` 或 `session_id` 查询：

```bash
# 按 run_id 列出所有会话
curl "http://localhost:12300/trajectories?run_id=run-001"

# 查看某个 session 的所有请求详情
curl "http://localhost:12300/trajectories/task-001?limit=10"

# 旧接口（向后兼容）
curl "http://localhost:12300/trajectory?session_id=task-001&limit=10"
```

---

## Step 6：删除动态注册的模型

```bash
curl -X DELETE "http://localhost:12300/models?model_name=qwen3.5-2b&run_id=run-001"
```

> 注意：通过配置文件预置的模型不可通过 API 删除，只能删除动态注册的模型。

---

## ID 提取优先级速览

| 字段 | 优先级顺序 |
|------|-----------|
| `run_id` | 路径参数 > `x-run-id` header > `model` 逗号后部分 |
| `session_id` | 路径参数 > `x-session-id` header > `x-sandbox-traj-id` header |

> 详细规则参见 [ID 设计规范](../design/modules/identifiers.md)

---

## Docker 构建命令速查

```bash
# Docker Compose 模式
docker build -f dockers/compose/Dockerfile -t traj-proxy:latest .

# All-in-One 模式
docker build -f dockers/allinone/Dockerfile -t traj-proxy-allinone:latest .

# 启停服务
./scripts/start_docker_compose.sh start | stop | restart
./scripts/start_docker_allinone.sh start | stop | restart
```

---

## 下一步

- 查看详细部署选项 → [部署指南](deployment.md)
- 了解所有配置项 → [配置详解](configuration.md)
- 完整 API 参考 → [API 概览](../api/overview.md)
- 运行完整 E2E 测试：`cd tests/e2e && ./run_tests.sh`