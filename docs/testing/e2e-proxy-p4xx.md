# Proxy 直连层：缓存命中验证（P4xx）

> 返回主文档：[E2E 测试用例总览](e2e-case-desc.md)

## 缓存命中验证（P4xx）

> 所有 P4xx 测试共用两个核心校验规则（见 [缓存递增验证规则](#14-缓存递增验证规则)）。

### P401 — TITO 非流式 3 轮缓存（无 Parser）

| 步骤 | 操作 |
|---|---|
| 1 | 注册 TITO 模型（无 parser） |
| 2 | R1: "What is 10 plus 5?" → 提取助手回复 |
| 3 | R2: 含 R1 历史 + "What is 25 plus 5?" |
| 4 | R3: 含 R1+R2 历史 + "What is 35 plus 5?" |

**验收标准**: `verify_cache_incremental(session, 3, "incremental")` 通过。

---

### P402 — TITO 非流式 Tool 2 轮缓存

| 步骤 | 操作 |
|---|---|
| 1 | 注册 TITO 模型（含 tool_parser + reasoning_parser） |
| 2 | R1: "Weather in Shanghai?" + `tools=[get_weather]` → 构建 assistant + tool 角色增量消息 |
| 3 | R2: 完整历史 + "Weather in Beijing?" |

**验收标准**: `verify_cache_incremental(session, 2, "incremental")` 通过。

---

### P403 — TITO 非流式 T+R 3 轮缓存

| 步骤 | 操作 |
|---|---|
| 1 | 注册 TITO 模型（含 tool_parser + reasoning_parser） |
| 2 | R1: "Weather in Shanghai? Think first then call tool." |
| 3 | R2: 完整历史（含 R1 assistant + tool 响应） + "Weather in Beijing?" |
| 4 | R3: 完整历史（R1+R2） + "Weather in Shenzhen?" |

**验收标准**: `verify_cache_incremental(session, 3, "incremental")` 通过。

---

### P404 — TITO 流式 T+R 3 轮缓存

**目的**: 与 P403 相同的 3 轮结构，但所有请求均为流式。使用 `parse_stream_to_msg()` 将流式 delta tool_calls 组装为完整消息。

**验收标准**: `verify_cache_incremental(session, 3, "incremental")` 通过。

---

### P405 — TITO 非流式 Reasoning 3 轮缓存

| 步骤 | 操作 |
|---|---|
| 1 | 注册 TITO 模型（含 reasoning_parser，无 tool_parser） |
| 2 | R1: "Think step by step: what is 5+3?" + `chat_template_kwargs={preserve_thinking:true, enable_thinking:true}` |
| 3 | R2: 含 R1 历史 + "what is 10+7?" |
| 4 | R3: 含 R1+R2 历史 + "what is 20+15?" |

**验收标准**: R1/R2 的 `reasoning_content` 和 `content` 均非空；`verify_cache_incremental(session, 3, "incremental")` 通过；轨迹 `raw_response` 包含 R1 的 `reasoning_content`。

---

### P406 — TITO 流式 3 轮缓存（无 Parser）

| 步骤 | 操作 |
|---|---|
| 1 | 注册 TITO 模型（无 parser） |
| 2 | R1: 流式 "What is 10 + 5?" |
| 3 | R2: 流式 含 R1 历史 + "What is 20 + 8?" |
| 4 | R3: 流式 含 R1+R2 历史 + "What is 30 + 12?" |

**验收标准**: `verify_cache_incremental(session, 3, "incremental")` 通过。

---

### P407 — 混合模式（流式→非流式→流式）3 轮 T+R 缓存

**目的**: 验证跨模式（同一会话中交替流式/非流式）缓存兼容性。

| 步骤 | 操作 |
|---|---|
| 1 | 注册 TITO 模型（含 tool_parser + reasoning_parser） |
| 2 | R1: **流式** "Weather in Shanghai?" |
| 3 | R2: **非流式** 含 R1 历史 + "Weather in Beijing?" |
| 4 | R3: **流式** 含 R1+R2 历史 + "Weather in Shenzhen?" |

**验收标准**: `verify_cache_incremental(session, 3, "incremental")` 通过。

---

### P408 — T+R 单轮冒烟（NS + S，无缓存验证）

**目的**: 非流式 + 流式 T+R 冒烟测试，不验证缓存。

| 步骤 | 操作 |
|---|---|
| 1 | 注册 TITO 模型（含 parsers） |
| 2 | 非流式 "Hello"（含 tools） |
| 3 | 流式 "Hi"（含 tools） |

**验收标准**: 非流式 HTTP 200；流式包含 `data:`。

---

### P409 — 无 Session 2 轮缓存（cache_hit_tokens 恒为 0）

**目的**: 验证不带 session_id 时 `cache_hit_tokens` 始终为 0。

| 步骤 | 操作 |
|---|---|
| 1 | 注册 TITO 模型（含 parsers） |
| 2 | R1: `/v1/chat/completions`（无 session 路径） |
| 3 | R2: 相同端点，不同内容 |
| 4 | 查询 run_id 下所有轨迹 |

**验收标准**: 所有轨迹记录 `cache_hit_tokens == 0`；`len(full_conv_ids) == len(token_ids) + len(response_ids)` 仍成立。

---

### P410 — Kwargs 变更缓存失效（2 轮）

**目的**: 验证 `chat_template_kwargs` 在两轮间变更时缓存失效。

| 步骤 | 操作 |
|---|---|
| 1 | 注册 TITO 模型（含 parsers） |
| 2 | R1: `chat_template_kwargs={enable_thinking:true}` |
| 3 | R2: 同一 session，`chat_template_kwargs={enable_thinking:false}` |

**验收标准**: `verify_cache_incremental(session, 2, "kwargs_change")` 通过 — 两轮 `cache_hit_tokens` 均为 0。

---

### P411 — Tools 变更缓存失效（2 轮）

**目的**: 验证 tools 定义在两轮间变更时缓存失效。

| 步骤 | 操作 |
|---|---|
| 1 | 注册 TITO 模型（含 parsers） |
| 2 | R1: `tools=[get_weather]`（1 个工具） |
| 3 | R2: 同一 session，`tools=[get_weather, get_time]`（2 个工具，不同定义） |

**验收标准**: `verify_cache_incremental(session, 2, "tools_change")` 通过 — 两轮 `cache_hit_tokens` 均为 0。

---