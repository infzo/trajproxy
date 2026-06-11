# TrajProxy E2E 测试用例说明文档

> 本文档汇总所有 E2E 测试用例的测试步骤与验收标准，共 62 个场景，分布于 5 个测试层。

---

## 目录

- [1. 测试框架概述](#1-测试框架概述)
- [2. Nginx 入口层（2 个场景）](#2-nginx-入口层)
- [3. Proxy 直连层（39 个场景）](#3-proxy-直连层)
  - [3.1 P1xx/P2xx — API 与模型管理（19 个场景）](#31-p1xxp2xx--api-与模型管理)
  - [3.2 P3xx — 轨迹与数据一致性（11 个场景）](#32-p3xx--轨迹与数据一致性)
  - [3.3 P4xx — 缓存命中验证（9 个场景）](#33-p4xx--缓存命中验证)
- [4. 归档调度层（4 个场景）](#4-归档调度层)
- [5. 对比测试层（14 个场景）](#5-对比测试层)
  - [5.1 C1xx — OpenAI 格式对比（7 个场景）](#51-c1xx--openai-格式对比)
  - [5.2 C2xx — Claude 格式对比（7 个场景）](#52-c2xx--claude-格式对比)
- [6. 性能测试层（3 个场景）](#6-性能测试层)

---

## 1. 测试框架概述

### 1.1 测试层架构

| 层 | 端口 | 场景数 | 说明 |
|---|---|---|---|
| **Nginx 入口层** | 12345 | 2 | 经 Nginx 入口的基本 chat/轨迹冒烟测试 |
| **Proxy 直连层** | 12300 | 39 | 模型 CRUD、参数透传、轨迹捕获、缓存命中、解析器等 |
| **归档调度层** | — | 4 | 归档配置、手动/定时归档、归档恢复 |
| **对比测试层** | 12345 | 14 | vLLM 直连 vs Proxy 响应对比（OpenAI + Claude 格式） |
| **性能测试层** | 12345 | 3 | 稳定性、并发、流式并发 |

### 1.2 场景命名规范

| 前缀 | 含义 |
|---|---|
| `N1xx` | Nginx 入口层测试 |
| `P1xx` | 模型管理测试 |
| `P2xx` | 请求处理测试 |
| `P3xx` | 轨迹与数据一致性测试 |
| `P4xx` | 缓存命中验证测试 |
| `A1xx` | 归档测试 |
| `C1xx` | OpenAI 格式对比测试 |
| `C2xx` | Claude 格式对比测试 |
| `T1xx` | 性能测试 |

### 1.3 通用断言工具

| 函数 | 作用 |
|---|---|
| `assert_eq` | 相等性断言 |
| `assert_contains` / `assert_not_contains` | 包含/不包含断言 |
| `assert_http_status` | HTTP 状态码断言 |
| `json_get` / `json_get_number` / `json_get_bool` | JSON 字段提取 |
| `verify_cache_incremental(session_id, expected_rounds, mode)` | 缓存递增验证（4 种模式：`incremental`/`nosession`/`kwargs_change`/`tools_change`） |

### 1.4 缓存递增验证规则

- **校验1**: `cache_hit_tokens[R_n] == len(full_conversation_token_ids[R_{n-1}])`（递增模式）
- **校验2**: `len(full_conversation_token_ids) == len(token_ids) + len(response_ids)`

### 1.5 全局配置（各层共享）

以下参数定义在 `tests/e2e/config.sh` 中，所有测试层统一继承。

| 配置项 | 默认值 | 环境变量覆盖 | 说明 |
|---|---|---|---|
| `DEFAULT_MODEL_NAME` | `Qwen3.6-27B` | `DEFAULT_MODEL_NAME` | 测试用推理模型名 |
| `DEFAULT_TOKENIZER_PATH` | `Qwen/Qwen3.6-27B` | `DEFAULT_TOKENIZER_PATH` | Tokenizer 路径（TITO 模式使用） |
| `DEFAULT_TOOL_PARSER` | `qwen3_coder` | `DEFAULT_TOOL_PARSER` | 默认 tool 解析器 |
| `DEFAULT_REASONING_PARSER` | `qwen3` | `DEFAULT_REASONING_PARSER` | 默认 reasoning 解析器 |
| `CHAT_API_KEY` | `sk-1234` | — | 推理请求认证 Key |
| `BACKEND_MODEL_URL` | `http://host.docker.internal:8080/v1` | `BACKEND_MODEL_URL` | 后端 vLLM 推理服务地址 |
| 采样参数 | `temperature: 0.0, seed: 42` | — | 确定性采样，消除推理随机性 |

### 1.6 服务地址配置（按层覆盖）

`BASE_URL` **不是全局统一值**，而是每个测试层根据自身目标各自覆盖。所有层先 `source` 全局 `config.sh`（默认 `BASE_URL` 指向 `:12300`），然后按层重写。

| 层 | 配置文件 | `BASE_URL` 实际值 | 环境变量 | 目标服务 |
|---|---|---|---|---|
| **全局默认** | `tests/e2e/config.sh` | `http://127.0.0.1:12300` | `TRAJ_PROXY_URL` | trajproxy 直连 |
| **Proxy 层** | `layers/proxy/config.sh` | `http://127.0.0.1:12300` | `TRAJ_PROXY_URL` | trajproxy 直连 |
| **Nginx 层** | `layers/nginx/config.sh` | `http://127.0.0.1:12345` | `NGINX_URL` | Nginx 入口 → LiteLLM → trajproxy |
| **Performance 层** | `layers/performance/config.sh` | `http://127.0.0.1:12345` | `NGINX_URL` | Nginx 入口 |
| **归档层** | `layers/archive/config.sh` | — （直连 DB，不使用 BASE_URL） | — | PostgreSQL + Docker |
| **对比层** | `layers/comparison/config.sh` | — （不使用 BASE_URL，定义独立变量） | — | 见下表 |

**对比层独立服务地址**（`layers/comparison/config.sh`）：

| 变量 | 默认值 | 环境变量 | 用途 |
|---|---|---|---|
| `PROXY_URL` | `http://127.0.0.1:12300` | `TRAJ_PROXY_URL` | C1xx OpenAI 格式对比：模型注册用 |
| `NGINX_URL` | `http://127.0.0.1:12345` | `NGINX_URL` | C1xx/C2xx Proxy 请求目标 |
| `VLLM_URL` | `http://127.0.0.1:8080` | `VLLM_URL` | vLLM 直连基线（两者共用） |

**调用链路**：

```
Nginx(:12345) → LiteLLM → trajproxy(:12300) → vLLM(:8080)     # Nginx/Performance 层
trajproxy(:12300) → vLLM(:8080)                                 # Proxy 直连层
vLLM(:8080)                                                     # 对比层基线
```

---

## 2. Nginx 入口层

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

---

## 3. Proxy 直连层

### 3.1 P1xx/P2xx — API 与模型管理

#### P101 — 模型 CRUD（注册/列表/删除）

**目的**: 验证模型注册、列表、删除全生命周期。

| 步骤 | 操作 |
|---|---|
| 1 | 注册模型 A（无 run_id → 默认 DEFAULT） |
| 2 | 注册模型 B（run_id: test-run-001） |
| 3 | 列出所有模型 |
| 4 | 删除模型 A（run_id: default） |
| 5 | 删除模型 B（run_id: test-run-001） |
| 6 | 再次列出所有模型 |

**验收标准**: 所有注册/删除返回 HTTP 200 且 `status=success`；模型 A 的 `run_id=DEFAULT`；注册后列表包含两个模型；删除后列表不包含任一模型。

---

#### P102 — PANGU 格式集成（model_name,run_id）

**目的**: 测试 PANGU 的 `{model_name},{run_id}` 模型参数格式。

| 步骤 | 操作 |
|---|---|
| 1 | 注册模型（run_id: run-p102） |
| 2 | 非流式请求，使用 `model: "Qwen3.6-27B,run-p102"` |
| 3 | 流式请求，使用相同格式 |
| 4 | 删除模型 |

**验收标准**: 注册成功；非流式返回 `id` 和 `choices`；流式包含 `data:`、`choices`、`[DONE]` 且有多个 chunk；删除成功。

---

#### P103 — 重复注册（幂等性）

**目的**: 验证同一模型注册两次是幂等的。

| 步骤 | 操作 |
|---|---|
| 1 | 注册模型（run_id: run-p103） |
| 2 | 再次注册同一模型 |

**验收标准**: 两次注册均返回 HTTP 200（或第二次 409 视为幂等）。

---

#### P104 — 预设模型保护（不可删除）

**目的**: 验证 config.yaml 中的预设模型不能通过 API 删除。

| 步骤 | 操作 |
|---|---|
| 1 | 列出模型 → 确认预设模型存在 |
| 2 | 尝试删除预设模型（run_id 为空）→ 期望 404 |
| 3 | 尝试删除预设模型（run_id=app-001）→ 期望 404 |
| 4 | 再次列出模型 → 确认预设模型仍存在 |

**验收标准**: 两次删除均返回 HTTP 404；预设模型始终存在于列表中。

---

#### P105 — 未注册模型

**目的**: 验证请求未注册模型时返回错误。

| 步骤 | 操作 |
|---|---|
| 1 | 向 `unregistered_model_xyz` 发送 Chat 请求 |

**验收标准**: 返回 HTTP 404 或 400。

---

#### P106 — 无效模型参数

**目的**: 验证模型参数校验。

| 步骤 | 操作 |
|---|---|
| 1 | 发送 `model=""` 请求 |
| 2 | 发送 `model=null` 请求 |
| 3 | 不传 model 字段 |

**验收标准**: 三种情况均返回 HTTP 422 或 400。

---

#### P107 — 并发限流

**目的**: 验证 `max_concurrent_requests` 限制导致 HTTP 429。

| 步骤 | 操作 |
|---|---|
| 1 | 注册模型（run_id: run-p107） |
| 2 | 发送 20 个并发 Chat 请求 |
| 3 | 收集所有 HTTP 状态码 |

**验收标准**: 系统不崩溃；至少部分请求返回 429（限流触发）；至少部分请求返回 200。

---

#### P201 — 参数透传（Direct 模式）

**目的**: 验证 OpenAI 参数和自定义 Header 在 Direct 模式下正确透传到后端，内部 Header 被拦截。

**前置条件**: Mock 推理服务器启动（端口 19990），Direct 模式模型注册。

| 步骤 | 操作 |
|---|---|
| 1 | 启动 Mock 服务器 |
| 2 | 注册 Direct 模式模型 |
| 3 | 非流式请求：`seed=42, n=1, stop=["\\n"], logprobs=true, top_logprobs=3, user=test-user` |
| 4 | 验证 Mock 接收到的参数 |
| 5 | 流式请求：`seed=99` |
| 6 | 验证流式参数 |
| 7 | 删除模型 |

**验收标准**:

| # | 断言 | 期望 |
|---|---|---|
| 1 | `seed` 透传 | `42`（Non-Stream）/ `99`（Stream） |
| 2 | `n` 透传 | `1` |
| 3 | `user` 透传 | `"test-user-passthrough"` |
| 4 | `logprobs` 被强制覆盖 | `1`（非用户的 `true`） |
| 5 | `x-sandbox-traj-id` 透传 | ✓ |
| 6 | `x-custom-header` 透传 | ✓ |
| 7 | `x-run-id` 不转发（内部 Header） | ✓ |
| 8 | `x-session-id` 不转发（内部 Header） | ✓ |

---

#### P202 — 参数过滤（TITO 模式）

**目的**: 验证 TITO 模式下不兼容参数被过滤，兼容参数正常透传。

**前置条件**: Mock 服务器（端口 19991），TITO 模式模型注册。

| 步骤 | 操作 |
|---|---|
| 1 | 启动 Mock 服务器 |
| 2 | 注册 TITO 模型（含 tokenizer） |
| 3 | 发送请求：兼容参数（`seed=42, n=1, user`）+ 不兼容参数（`response_format, logit_bias`） |
| 4 | 验证 Mock 参数 |
| 5 | 删除模型 |

**验收标准**: `response_format` → NOT_FOUND（被过滤）；`logit_bias` → NOT_FOUND（被过滤）；`seed=42, n=1, user="tito-user"` 正常透传；`x-run-id`/`x-session-id` 不转发。

---

#### P203 — Logprobs 强制覆盖与返回过滤

**目的**: 验证 Proxy 强制设置 `logprobs=1` 和 `return_token_ids=True`，从客户端响应中剥离，存储到轨迹中。

| 步骤 | 操作 |
|---|---|
| 1-5 | 非流式 5 种场景：无参数 / `logprobs=true` / `logprobs=5` / `return_token_ids=true` / 两者同时 |
| 6-10 | 流式相同 5 种场景 |
| 11 | 对每种：验证客户端响应和轨迹存储 |

**验收标准**: 客户端响应中无 `logprobs`、无 `token_ids`；轨迹 `raw_response` 中 `logprobs` 非空、`token_ids` 非空；流式所有 chunk 均无 logprobs/token_ids。

---

#### P204 — chat_template_kwargs 透传与消费

**目的**: Direct 模式 kwargs 透传到后端；TITO 模式 kwargs 被 MessageConverter 消费，不出现在 completion 请求中。

| 步骤 | 操作 |
|---|---|
| **Part A** | Direct 模型：注册 → NS+Stream 发送含 kwargs 请求 → 验证 Mock 有 kwargs → 删除 |
| **Part B** | TITO 模型：注册 → NS+Stream 发送含 kwargs 请求 → 验证 Mock 无 kwargs → 删除 |

**验收标准**: Part A Mock 收到 `chat_template_kwargs`（含 `enable_thinking`）；Part B completion 请求中 NOT_FOUND；两者均返回 200。

---

#### P108 — Processor 懒加载

**目的**: 验证模型配置在注册时存储，Processor 仅在首次请求时加载。

| 步骤 | 操作 |
|---|---|
| 1 | 注册 Direct 模型（run-lazy-001） |
| 2 | 首次请求（触发懒加载） |
| 3 | 第二次请求（命中 LRU 缓存） |
| 4 | 流式请求（验证缓存 Processor 支持流式） |
| 5 | 删除模型 |

**验收标准**: 所有请求返回 HTTP 200 且有 `choices`；流式响应包含 SSE `data:` 前缀。

---

#### P109 — Processor LRU 缓存命中与访问排序

**目的**: 验证 5 个模型的 LRU 缓存管理、缓存命中与访问顺序。

| 步骤 | 操作 |
|---|---|
| 1 | 注册 5 个模型（run-lru-002-{1..5}） |
| 2 | 第一轮：请求全部 5 个（触发懒加载） |
| 3 | 第二轮：再次请求全部 5 个（应命中缓存） |
| 4 | 交替请求模型 1 和 5（3 轮，验证 LRU 访问顺序） |
| 5 | 删除全部 5 个模型 |

**验收标准**: 第一轮懒加载+请求成功；第二轮缓存命中+请求成功；交替请求全部成功。

---

#### P110 — 预设模型懒加载

**目的**: 验证 config.yaml 预设模型在首次请求时懒加载，缓存命中，且不可被 API 删除。

| 步骤 | 操作 |
|---|---|
| 1 | 列出模型 → 确认预设存在 |
| 2 | 首次请求预设模型（触发懒加载） |
| 3 | 第二次请求（缓存命中） |
| 4 | 带 `run_id=app-001` 请求 |
| 5 | 尝试删除预设模型 → 期望 404 |

**验收标准**: 列表包含预设模型；首次请求成功（懒加载）；第二次成功；删除返回 404。

---

#### P111 — LRU 逐出与重新加载

**目的**: 注册 35 个模型（超过 LRU 默认容量 32），验证逐出和重新加载机制。

| 步骤 | 操作 |
|---|---|
| 1 | 注册 35 个模型（run-lru-evict-{1..35}） |
| 2 | 第一轮：请求全部 35 个（填充缓存，触发逐出） |
| 3 | 重新请求模型 1-3（应已被逐出，验证重新加载） |
| 4 | 重新请求模型 33-35（应仍在缓存中） |
| 5 | 删除全部 35 个模型 |

**验收标准**: 所有 35 个注册成功；逐出模型（1-3）重新加载成功；缓存中模型（33-35）仍成功；所有 35 个删除成功。

---

#### P205 — 自定义 Tool Parser

**目的**: 验证 `custom_parsers/tool_parsers/` 下的自定义 tool_parser 自动发现和加载。

| 步骤 | 操作 |
|---|---|
| 1 | 注册 TITO 模型（`tool_parser="test_tool_parser"`） |
| 2 | 发送含 `tools=[get_weather]` 的 Chat 请求 |
| 3 | 查询轨迹 |
| 4 | 删除模型 |

**验收标准**: 注册成功且 `tool_parser` 匹配；响应有 `tool_calls` 且 `function` 非空；轨迹 `count > 0` 且 `session_id` 匹配。

---

#### P206 — 自定义 Reasoning Parser

**目的**: 验证 `custom_parsers/reasoning_parsers/` 下的自定义 reasoning_parser 自动发现和加载。

| 步骤 | 操作 |
|---|---|
| 1 | 注册 TITO 模型（`reasoning_parser="test_reasoning_parser"`） |
| 2 | 发送 Chat 请求（"Output exactly: The answer is 42."） |
| 3 | 查询轨迹 |
| 4 | 删除模型 |

**验收标准**: 注册成功且 `reasoning_parser` 匹配；`reasoning` 字段非空；轨迹 `count > 0`。

---

#### P207 — TITO Token 模式冒烟（非流式）

**目的**: TITO 非流式模式基础功能验证 + response_ids 校验。

| 步骤 | 操作 |
|---|---|
| 1 | 注册 TITO 模型（无 parser） |
| 2 | 发送非流式请求 |
| 3 | 查询轨迹 |

**验收标准**: HTTP 200；轨迹 `response_ids` 长度 > 0。

---

#### P208 — TITO Token 模式冒烟（流式）

**目的**: TITO 流式模式基础功能验证。

| 步骤 | 操作 |
|---|---|
| 1 | 注册 TITO 模型 |
| 2 | 发送流式请求 |

**验收标准**: 流式响应包含 `data:` 和 `[DONE]`。

---

### 3.2 P3xx — 轨迹与数据一致性

#### P301 — 基础轨迹捕获

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

#### P302 — EOS Token 一致性

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

#### P303 — TITO 流式/非流式轨迹一致性

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

#### P304 — Direct 模式流式/非流式轨迹一致性

**目的**: 与 P303 相同逻辑，但针对 Direct 模式（`token_in_token_out=false`）。

**验收标准**: 同 P303，但 token 相关字段（`token_ids`, `prompt_text`, `response_ids`, `token_request`, `token_response`）在 Direct 模式下允许为空。

---

#### P305 — PANGU 集成 + 轨迹捕获

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

#### P306 — TITO Tool 轨迹存储

**目的**: 验证 tool_calls 正确存储在轨迹记录中。

| 步骤 | 操作 |
|---|---|
| 1 | 注册 TITO 模型（含 tool_parser + reasoning_parser） |
| 2 | 发送含 `tools=[get_weather]` 的 Chat 请求 |
| 3 | 验证响应 tool_calls 结构 |
| 4 | 查询轨迹 |

**验收标准**: 响应 `tool_calls` 有 `id` 和 `function.name`，`arguments` 为字符串；轨迹含 `token_ids`/`response_ids`（非空 TITO）；`raw_response` 有 `tool_calls`。

---

#### P307 — Trajectories API 测试

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

#### P308 — 轨迹查询 `fields` 参数

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

#### P309 — 轨迹字段交叉验证

**目的**: 验证存储的 messages/token_ids 与实际请求内容匹配。

| 步骤 | 操作 |
|---|---|
| 1 | 注册 TITO 模型 |
| 2 | 发送已知 prompt 请求（"Hello crosscheck test 211"） |
| 3 | 查询轨迹并交叉验证 |

**验收标准**: `messages` 包含精确的用户 prompt 内容；`token_ids` 非空；`response_ids` 非空；`len(full_conversation_token_ids) == len(token_ids) + len(response_ids)`；`cache_hit_tokens == 0`；存储的 model 匹配请求 model。

---

### 3.3 P4xx — 缓存命中验证

> 所有 P4xx 测试共用两个核心校验规则（见 [缓存递增验证规则](#14-缓存递增验证规则)）。

#### P401 — TITO 非流式 3 轮缓存（无 Parser）

| 步骤 | 操作 |
|---|---|
| 1 | 注册 TITO 模型（无 parser） |
| 2 | R1: "What is 10 plus 5?" → 提取助手回复 |
| 3 | R2: 含 R1 历史 + "What is 25 plus 5?" |
| 4 | R3: 含 R1+R2 历史 + "What is 35 plus 5?" |

**验收标准**: `verify_cache_incremental(session, 3, "incremental")` 通过。

---

#### P402 — TITO 非流式 Tool 2 轮缓存

| 步骤 | 操作 |
|---|---|
| 1 | 注册 TITO 模型（含 tool_parser + reasoning_parser） |
| 2 | R1: "Weather in Shanghai?" + `tools=[get_weather]` → 构建 assistant + tool 角色增量消息 |
| 3 | R2: 完整历史 + "Weather in Beijing?" |

**验收标准**: `verify_cache_incremental(session, 2, "incremental")` 通过。

---

#### P403 — TITO 非流式 T+R 3 轮缓存

| 步骤 | 操作 |
|---|---|
| 1 | 注册 TITO 模型（含 tool_parser + reasoning_parser） |
| 2 | R1: "Weather in Shanghai? Think first then call tool." |
| 3 | R2: 完整历史（含 R1 assistant + tool 响应） + "Weather in Beijing?" |
| 4 | R3: 完整历史（R1+R2） + "Weather in Shenzhen?" |

**验收标准**: `verify_cache_incremental(session, 3, "incremental")` 通过。

---

#### P404 — TITO 流式 T+R 3 轮缓存

**目的**: 与 P403 相同的 3 轮结构，但所有请求均为流式。使用 `parse_stream_to_msg()` 将流式 delta tool_calls 组装为完整消息。

**验收标准**: `verify_cache_incremental(session, 3, "incremental")` 通过。

---

#### P405 — TITO 非流式 Reasoning 3 轮缓存

| 步骤 | 操作 |
|---|---|
| 1 | 注册 TITO 模型（含 reasoning_parser，无 tool_parser） |
| 2 | R1: "Think step by step: what is 5+3?" + `chat_template_kwargs={preserve_thinking:true, enable_thinking:true}` |
| 3 | R2: 含 R1 历史 + "what is 10+7?" |
| 4 | R3: 含 R1+R2 历史 + "what is 20+15?" |

**验收标准**: R1/R2 的 `reasoning_content` 和 `content` 均非空；`verify_cache_incremental(session, 3, "incremental")` 通过；轨迹 `raw_response` 包含 R1 的 `reasoning_content`。

---

#### P406 — TITO 流式 3 轮缓存（无 Parser）

| 步骤 | 操作 |
|---|---|
| 1 | 注册 TITO 模型（无 parser） |
| 2 | R1: 流式 "What is 10 + 5?" |
| 3 | R2: 流式 含 R1 历史 + "What is 20 + 8?" |
| 4 | R3: 流式 含 R1+R2 历史 + "What is 30 + 12?" |

**验收标准**: `verify_cache_incremental(session, 3, "incremental")` 通过。

---

#### P407 — 混合模式（流式→非流式→流式）3 轮 T+R 缓存

**目的**: 验证跨模式（同一会话中交替流式/非流式）缓存兼容性。

| 步骤 | 操作 |
|---|---|
| 1 | 注册 TITO 模型（含 tool_parser + reasoning_parser） |
| 2 | R1: **流式** "Weather in Shanghai?" |
| 3 | R2: **非流式** 含 R1 历史 + "Weather in Beijing?" |
| 4 | R3: **流式** 含 R1+R2 历史 + "Weather in Shenzhen?" |

**验收标准**: `verify_cache_incremental(session, 3, "incremental")` 通过。

---

#### P408 — T+R 单轮冒烟（NS + S，无缓存验证）

**目的**: 非流式 + 流式 T+R 冒烟测试，不验证缓存。

| 步骤 | 操作 |
|---|---|
| 1 | 注册 TITO 模型（含 parsers） |
| 2 | 非流式 "Hello"（含 tools） |
| 3 | 流式 "Hi"（含 tools） |

**验收标准**: 非流式 HTTP 200；流式包含 `data:`。

---

#### P409 — 无 Session 2 轮缓存（cache_hit_tokens 恒为 0）

**目的**: 验证不带 session_id 时 `cache_hit_tokens` 始终为 0。

| 步骤 | 操作 |
|---|---|
| 1 | 注册 TITO 模型（含 parsers） |
| 2 | R1: `/v1/chat/completions`（无 session 路径） |
| 3 | R2: 相同端点，不同内容 |
| 4 | 查询 run_id 下所有轨迹 |

**验收标准**: 所有轨迹记录 `cache_hit_tokens == 0`；`len(full_conv_ids) == len(token_ids) + len(response_ids)` 仍成立。

---

#### P410 — Kwargs 变更缓存失效（2 轮）

**目的**: 验证 `chat_template_kwargs` 在两轮间变更时缓存失效。

| 步骤 | 操作 |
|---|---|
| 1 | 注册 TITO 模型（含 parsers） |
| 2 | R1: `chat_template_kwargs={enable_thinking:true}` |
| 3 | R2: 同一 session，`chat_template_kwargs={enable_thinking:false}` |

**验收标准**: `verify_cache_incremental(session, 2, "kwargs_change")` 通过 — 两轮 `cache_hit_tokens` 均为 0。

---

#### P411 — Tools 变更缓存失效（2 轮）

**目的**: 验证 tools 定义在两轮间变更时缓存失效。

| 步骤 | 操作 |
|---|---|
| 1 | 注册 TITO 模型（含 parsers） |
| 2 | R1: `tools=[get_weather]`（1 个工具） |
| 3 | R2: 同一 session，`tools=[get_weather, get_time]`（2 个工具，不同定义） |

**验收标准**: `verify_cache_incremental(session, 2, "tools_change")` 通过 — 两轮 `cache_hit_tokens` 均为 0。

---

## 4. 归档调度层

### A100 — 归档进程配置验证

**目的**: 验证独立归档进程正确启动，且与核心 Proxy 解耦。

| 步骤 | 操作 | 预期 |
|---|---|---|
| 1 | `docker ps` 检查归档容器 | 容器正在运行 |
| 2 | 搜索归档日志中的启动消息 | 包含 `"TrajArchiver 已启动"` 或 `"ArchiveScheduler 已启动"` |
| 3a | 搜索 `poll_interval` | 找到且解析为整数 |
| 3b | 搜索 `retention_days` | 找到且解析为整数 |
| 4 | 搜索 **Proxy** 日志中 `ArchiveScheduler` | **不应找到**（解耦验证） |
| 5 | 查询 `pg_class` 中 `request_details_active` 分区数 | 至少 1 个分区存在 |

---

### A101 — 手动触发归档（按 run_id 粒度）

**目的**: 验证过期数据的完整归档流程，归档文件格式 `{run_id}/{session_id}.jsonl.gz`。

**前置条件**: 归档容器已运行；上月分区已创建。

| 步骤 | 操作 | 预期 |
|---|---|---|
| 1 | 创建分区 + 插入 10 条测试数据（上月） | 数据已写入 |
| 2 | 统计 `request_metadata` 行数 | `== 10` |
| 3 | `run_archive_once(1)`（1 天保留，上月数据完全过期） | 归档执行完成 |
| 4 | 分区记录数 | `== 0`（全部从活跃表删除） |
| 5 | 检查 `archive_location` 列 | 包含 `test-run-a101`（路径含 run_id） |
| 6a | 检查归档文件存在 | 文件存在（本地或 S3） |
| 6b | 验证归档文件（gzip 行数） | 精确 **10** 行 |

---

### A102 — 未过期数据不归档（反例验证）

**目的**: 验证未过期 run 的数据 **不会** 被归档。

| 步骤 | 操作 | 预期 |
|---|---|---|
| 1 | 创建分区 + 插入 5 条测试数据（**当月**） | 数据已写入 |
| 2 | `run_archive_once(30)`（30 天保留，当月数据不过期） | 归档执行完成 |
| 3 | 分区记录数 | `== 执行前`（无数据被删除） |
| 4 | 检查 `archive_location` 列 | **空/NULL**（无归档记录） |

---

### A103 — 归档数据恢复（文件验证）

**目的**: 验证归档 `.jsonl.gz` 文件可读、合法 GZIP+JSON、包含正确字段且与 DB 元数据一致。

| 步骤 | 操作 | 预期 |
|---|---|---|
| 1 | 清理旧分区 → 创建分区 → 插入 5 条数据（2 个月前） → `run_archive_once(1)` | 文件存在 |
| 2a | 解压读取首行 | 首行非空 → GZIP 可读 |
| 2b | JSON 解析首行 | **合法 JSON** |
| 3 | 检查首行 3 个必需字段：`unique_id`, `created_at`, `messages` | 3 个字段均存在 |
| 4 | 验证归档文件行数 | 精确 **5** 行 |
| 5 | 提取首行 `unique_id`，查询 DB `request_metadata.unique_id` | 归档 `unique_id` == DB `unique_id` |
| 6 | 验证 `archive_location` 格式 | 包含 `test-run-a103` |

---

### 归档层覆盖率矩阵

| 验证项 | A100 | A101 | A102 | A103 |
|---|:---:|:---:|:---:|:---:|
| 归档容器运行 | ✅ | | | |
| 归档启动日志 | ✅ | | | |
| 调度器配置（poll_interval/retention） | ✅ | | | |
| 核心 Proxy 无归档代码 | ✅ | | | |
| DB 分区基础设施 | ✅ | | | |
| 过期数据归档并删除 | | ✅ | | ✅ |
| 未过期数据保留 | | | ✅ | |
| archive_location 格式 | | ✅ | | ✅ |
| 归档文件存在 | | ✅ | | ✅ |
| GZIP + JSON 合法 | | | | ✅ |
| 字段完整性 | | | | ✅ |
| 行数匹配 | | ✅ | | ✅ |
| 归档与 DB 元数据一致 | | | | ✅ |

---

## 5. 对比测试层

> 所有对比测试的核心逻辑：将同一请求同时发送到 **vLLM 直连** 和 **Proxy（NGINX→trajproxy）**，比较响应是否语义一致。

