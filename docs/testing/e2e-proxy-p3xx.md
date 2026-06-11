# Proxy 直连层：轨迹与数据一致性（P3xx）

> 返回主文档：[E2E 测试用例总览](e2e-case-desc.md)

## 轨迹与数据一致性（P3xx）

### P301 — 基础轨迹捕获

**目的**: 验证 TITO 轨迹捕获和字段完整性。

| 步骤 | 操作 |
|---|---|
| 1 | 注册 TITO 模型 |
| 2 | 非流式 Chat 请求（"你好，请介绍一下你自己"） |
| 3 | 查询轨迹 |
| 4 | 删除模型 |

**验收标准**:

| # | 断言 | 期望 |
|---|---|---|
| 1 | Chat HTTP 200 | ✓ |
| 2 | `choices` 存在 | ✓ |
| 3 | 轨迹 `count` | `1` |
| 4 | 必需字段完整 | `run_id, session_id, model_name, messages, response_ids, full_conversation_token_ids` 非空 |
| 5 | `cache_hit_tokens` | `== 0`（首轮） |
| 6 | `run_id` / `session_id` | 匹配请求值 |

---

### P302 — EOS Token 一致性

**目的**: 验证 `full_conversation_text` 与 `full_conversation_token_ids` 的 EOS 一致性（无双重 EOS）。

| 步骤 | 操作 |
|---|---|
| 1 | 注册 TITO 模型 |
| 2 | 第一轮非流式 Chat |
| 3 | 验证用户可见响应无 EOS 字符 |
| 4 | 查询轨迹：`response_ids` 最后 token = EOS，`response_text` 以 `<\|im_end\|>` 结尾 |
| 5 | 第二轮多轮对话（携带第一轮历史） |
| 6 | 第二轮验证：EOS token ID 一致、无双重 EOS |
| 7 | 删除模型 |

**验收标准**: 用户响应不含 EOS；`response_text` 以 `<|im_end|>` 结尾；两轮 EOS token ID 一致；`full_conversation_token_ids` 无双重 EOS；第一轮 `cache_hit_tokens == 0`。

---

### P303 — TITO 流式/非流式轨迹一致性

**目的**: 比较相同请求分别以流式和非流式发送时，轨迹字段深度一致。

| 步骤 | 操作 |
|---|---|
| 1 | 注册 TITO 模型 |
| 2 | 非流式请求（固定 prompt） |
| 3 | 流式请求（相同 prompt） |
| 4 | 查询两者轨迹 |
| 5 | 深度 JSON 递归对比（排除 id/timestamp/logprobs/token_ids 差异路径） |

**验收标准**: 两者均有轨迹记录；`model`、`messages`、`raw_request`/`raw_response` 结构、`response_text`/`response_ids`（均非空）、`token_ids`/`token_stat`、`prompt_text` 键值匹配。

---

### P304 — Direct 模式流式/非流式轨迹一致性

**目的**: 与 P303 相同逻辑，但针对 Direct 模式（`token_in_token_out=false`）。

**验收标准**: 同 P303，但 token 相关字段（`token_ids`, `prompt_text`, `response_ids`, `token_request`, `token_response`）在 Direct 模式下允许为空。

---

### P305 — PANGU 集成 + 轨迹捕获

**目的**: 验证路径和 Header 两种方式的 session_id 传递均产生轨迹记录。

| 步骤 | 操作 |
|---|---|
| 1 | 注册模型（PANGU 格式 `{model},{run_id}`） |
| 2 | 路径方式：`/s/{run_id}/{session_id}/v1/chat/completions` |
| 3 | Header 方式：`x-sandbox-traj-id: {session_id}`（相同 UUID） |
| 4 | 按 session_id 查询轨迹 |
| 5 | 删除模型 |

**验收标准**: 两个请求均返回 HTTP 200（有 `id` 和 `choices`）；轨迹 `count >= 2`，`session_id` 匹配，`model_name` 存在。

---

### P306 — TITO Tool 轨迹存储

**目的**: 验证 tool_calls 正确存储在轨迹记录中。

| 步骤 | 操作 |
|---|---|
| 1 | 注册 TITO 模型（含 tool_parser + reasoning_parser） |
| 2 | 发送含 `tools=[get_weather]` 的 Chat 请求 |
| 3 | 验证响应 tool_calls 结构 |
| 4 | 查询轨迹 |

**验收标准**: 响应 `tool_calls` 有 `id` 和 `function.name`，`arguments` 为字符串；轨迹含 `token_ids`/`response_ids`（非空 TITO）；`raw_response` 有 `tool_calls`。

---

### P307 — Trajectories API 测试

**目的**: 测试 `/trajectories` 端点及与旧版 `/trajectory` 的向后兼容性。

| 步骤 | 操作 |
|---|---|
| 1 | 注册模型 |
| 2 | 向 3 个不同 session 各发 1 个请求 |
| 3 | `GET /trajectories?run_id=X` → 验证 session 列表 |
| 4 | `GET /trajectories`（无 run_id）→ 期望 422/400 |
| 5 | `GET /trajectories/{session_id}` → 验证完整记录 |
| 6 | `GET /trajectories/{session_id_2}` |
| 7 | `GET /trajectory?session_id=X`（旧版）→ 向后兼容 |
| 8 | `GET /trajectories/nonexistent-session-id` → 空记录 |
| 9 | 删除模型 |

**验收标准**:

| 接口 | 断言 |
|---|---|
| `/trajectories?run_id=X` | `run_id` 匹配，`>= 3` 个 session，包含全部 3 个 session_id |
| `/trajectories`（无 run_id） | 422 或 400 |
| `/trajectories/{session_id}` | `session_id` 匹配，有 `request_id`/`model`/`prompt_tokens`/`completion_tokens` |
| `/trajectory?session_id=X` | `session_id` 匹配，`count > 0`，有 `count`/`records`/`session_id` |
| `/trajectories/nonexistent` | `records` 为空 |

---

### P308 — 轨迹查询 `fields` 参数

**目的**: 测试 `fields` 包含/排除语法对轨迹查询的过滤效果。

| 步骤 | 操作 |
|---|---|
| 1 | 注册模型并发送请求生成轨迹 |
| 2 | `GET /trajectories/{sid}?fields=model,request_id,prompt_tokens`（包含语法） |
| 3 | `GET /trajectories/{sid}?fields=-raw_request,-raw_response,-messages`（排除语法） |
| 4 | `GET /trajectories/{sid}`（无 fields = 全部） |
| 5 | `GET /trajectory?session_id=X&fields=model,session_id,request_id`（旧版 fields） |

**验收标准**:

| 模式 | 存在字段 | 不存在字段 |
|---|---|---|
| 包含 | `model`, `request_id`, `prompt_tokens` | `messages`, `raw_request` |
| 排除 | `model`, `prompt_tokens` | `raw_request`, `raw_response`, `messages` |
| 全部 | 所有字段 | — |
| 旧版 | `model`, `request_id` | `messages` |

---

### P309 — 轨迹字段交叉验证

**目的**: 验证存储的 messages/token_ids 与实际请求内容匹配。

| 步骤 | 操作 |
|---|---|
| 1 | 注册 TITO 模型 |
| 2 | 发送已知 prompt 请求（"Hello crosscheck test 211"） |
| 3 | 查询轨迹并交叉验证 |

**验收标准**: `messages` 包含精确的用户 prompt 内容；`token_ids` 非空；`response_ids` 非空；`len(full_conversation_token_ids) == len(token_ids) + len(response_ids)`；`cache_hit_tokens == 0`；存储的 model 匹配请求 model。

---