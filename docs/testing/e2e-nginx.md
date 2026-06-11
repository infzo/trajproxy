# Nginx 入口层测试

> 返回主文档：[E2E 测试用例总览](e2e-case-desc.md)

## Nginx 入口层

### N101 — 基础 Chat 轨迹存储（非流式冒烟）

**目的**: 验证非流式 Chat 请求经 Nginx 层后，轨迹正确存储。

**前置条件**: Nginx 服务在端口 12345 上运行。

| 步骤 | 操作 | 接口 |
|---|---|---|
| 1 | 注册模型（`run_id=run-n101`, `token_in_token_out=false`） | `POST /models/register` |
| 2 | 发送非流式 Chat 请求（"Hello"） | `POST /s/{run_id}/{session_id}/v1/chat/completions` |
| 3 | 查询轨迹 | `GET /trajectory?session_id={session_id}&limit=1` |
| 4 | 删除模型 | `DELETE /models` |

**验收标准**:

| # | 断言 | 期望值 |
|---|---|---|
| 1 | 注册 HTTP 状态 | 200 |
| 2 | 注册 `status` | `"success"` |
| 3 | 注册 `run_id` | `"run-n101"` |
| 4 | Chat HTTP 状态 | 200 |
| 5 | 响应包含 `id` | ✓ |
| 6 | 响应包含 `choices` | ✓ |
| 7 | 轨迹记录数 | >= 1 |
| 8 | `token_ids` 非空 | ✓ |
| 9 | `response_ids` 非空 | ✓ |
| 10 | `full_conversation_token_ids` 非空 | ✓ |
| 11 | 删除 HTTP 状态 | 200 |
| 12 | 删除 `deleted` | `true` |

---

### N102 — 流式 Chat 轨迹存储（流式冒烟）

**目的**: 验证流式 Chat 请求经 Nginx 层后，轨迹正确存储。

**前置条件**: Nginx 服务在端口 12345 上运行。

| 步骤 | 操作 | 接口 |
|---|---|---|
| 1 | 注册模型（`run_id=run-n102`, `token_in_token_out=false`） | `POST /models/register` |
| 2 | 发送流式 Chat 请求（`stream: true`） | `POST /s/{run_id}/{session_id}/v1/chat/completions` |
| 3 | 查询轨迹 | `GET /trajectory?session_id={session_id}&limit=1` |
| 4 | 删除模型 | `DELETE /models` |

**验收标准**:

| # | 断言 | 期望值 |
|---|---|---|
| 1 | 注册 HTTP 状态 | 200 |
| 2 | 注册 `status` | `"success"` |
| 3 | SSE 响应包含 `data:` 前缀 | ✓ |
| 4 | SSE 响应包含 `choices` | ✓ |
| 5 | SSE 响应包含 `[DONE]` 终止符 | ✓ |
| 6 | Chunk 数量 > 0 | ✓ |
| 7 | 轨迹记录数 >= 1 | ✓ |
| 8 | `token_ids` 非空 | ✓ |
| 9 | `response_ids` 非空 | ✓ |
| 10 | `full_conversation_token_ids` 非空 | ✓ |
| 11 | 删除 HTTP 状态 | 200 |
| 12 | 删除 `deleted` | `true` |