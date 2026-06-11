# 对比测试层

> 返回主文档：[E2E 测试用例总览](e2e-case-desc.md)

本文档覆盖对比测试层（Comparison）的 **14 个场景**，其中 C1xx（OpenAI 格式）7 个、C2xx（Claude 格式）7 个。所有场景的核心逻辑：将同一请求同时发送到 vLLM 直连基线和 Proxy（Nginx → LiteLLM → trajproxy），使用 `compare.py` 工具逐字段对比两者响应，确保语义一致性且无特殊 token 泄漏。

---

## 概览

### 概览表格

| 层 | 场景数 | 说明 |
|---|---|---|
| **C1xx — OpenAI 格式对比** | 7 | Direct/TITO 各 Parser 组合与 vLLM `/v1/chat/completions` 响应逐字段一致性 |
| **C2xx — Claude 格式对比** | 7 | Direct/TITO 各 Parser 组合与 vLLM `/v1/messages` 响应逐字段一致性 |

### 测试工具 compare.py

`compare.py`（`tests/e2e/layers/comparison/compare.py`）是基于 Python 的响应对比工具，支持 OpenAI 和 Claude 两种 API 格式。

**命令行参数**：

| 参数 | 必选 | 说明 |
|---|---|---|
| `--mode` | 是 | `nonstream`（非流式）或 `stream`（流式） |
| `--api` | 否（默认 `openai`） | `openai` 或 `claude` |
| `--vllm` | 是 | vLLM 响应文件路径（JSON 或 SSE 文本） |
| `--proxy` | 是 | Proxy 响应文件路径（JSON 或 SSE 文本） |
| `--label` | 是 | 场景标签（如 `C101-direct-reasoning-tool`） |
| `--with-reasoning` | 否 | 启用 reasoning 相关字段校验（仅用于含 reasoning_parser 的场景） |
| `--skip-paths` | 否 | 逗号分隔的路径子串列表，匹配的字段跳过值对比（如 `finish_reason,arguments`） |

**退出码**：

| 退出码 | 含义 |
|---|---|
| `0` | 所有对比通过，无 FAIL 行 |
| `1` | 存在 FAIL 行（字段不一致、特殊 token 泄漏、格式错误等） |

**核心能力**：
- 非流式：解析 JSON，递归逐字段对比；跳过运行时动态字段（`id`、`created` 等）
- 流式：解析 SSE events，合并后对比完整语义
- 特殊 token 泄漏检测：检查 content/reasoning_content/tool_calls 中是否含 ` Boutique`、`</think>`、`<|tool_call_begin|>` 等禁止标记
- Proxy 专属字段验证：reasoning_parser 场景校验 `reasoning_content` 和 `provider_specific_fields`
- Claude 格式：content[] 按 type（text/thinking/tool_use）分类对比；usage 仅验证 > 0

### 调用链路

```
基线（vLLM 直连）:
  客户端 → vLLM(:8080)/v1/chat/completions  → vllm_resp.json
  客户端 → vLLM(:8080)/v1/messages           → vllm_resp.json

被测体（Proxy 链路）:
  宲户端 → Nginx(:12345) → LiteLLM → trajproxy(:12300) → vLLM(:8080)  → proxy_resp.json

对比:
  python3 compare.py --mode xxx --api xxx --vllm vllm_resp.json --proxy proxy_resp.json --label Cxxx
```

- C1xx OpenAI：vLLM 直连 `:8080/v1/chat/completions` 为基线；Nginx `:12345` 发送带 session 路径的请求为被测体
- C2xx Claude：vLLM 直连 `:8080/v1/messages` 为基线；Nginx `:12345/SESSION/v1/messages` 为被测体（经 LiteLLM 做 Claude→OpenAI 适配）
- 模型注册统一通过 `PROXY_URL(:12300)/models/register`

### 全局配置

以下配置定义在 `tests/e2e/layers/comparison/config.sh` 中。

| 变量 | 默认值 | 环境变量覆盖 | 用途 |
|---|---|---|---|
| `PROXY_URL` | `http://127.0.0.1:12300` | `TRAJ_PROXY_URL` | trajproxy 地址：模型注册、Direct 基线 |
| `NGINX_URL` | `http://127.0.0.1:12345` | `NGINX_URL` | Nginx 入口地址：所有 Proxy 请求目标 |
| `VLLM_URL` | `http://127.0.0.1:8080` | `VLLM_URL` | vLLM 直连基线地址 |
| `COMPARISON_MODEL_NAME` | `Qwen3.6-27B` | `DEFAULT_MODEL_NAME` | 测试用推理模型名 |
| `COMPARISON_TOKENIZER_PATH` | `Qwen/Qwen3.6-27B` | `DEFAULT_TOKENIZER_PATH` | TITO 模式 Tokenizer 路径 |
| `COMPARISON_TOOL_PARSER` | `qwen3_coder` | `DEFAULT_TOOL_PARSER` | 默认 tool 解析器 |
| `COMPARISON_REASONING_PARSER` | `qwen3` | `DEFAULT_REASONING_PARSER` | 默认 reasoning 解析器 |
| `COMPARISON_SAMPLING_PARAMS` | `temperature: 0.0, seed: 42` | `E2E_SAMPLING_PARAMS` | 确定性采样参数 |
| `COMPARISON_API_KEY` | `sk-1234` | `CHAT_API_KEY` | 推理请求认证 Key |

---

## C1xx - OpenAI 格式对比

### C101 - Direct T+R Parser 一致性

**目的**：验证 Direct 模式（token_in_token_out=false）下，reasoning + tool_calls 全字段与 vLLM 直连响应一致。

**Pipeline**: Direct | **流式模式**: ns+s | **Parser**: T+R | **API**: OpenAI | **轮次**: 单轮

**前置条件**：vLLM 服务在 `:8080` 正常运行；Nginx 服务在 `:12345` 正常运行

| 步骤 | 操作 |
|---|---|
| 1 | 注册模型：`register_model("run-c101", COMPARISON_MODEL_NAME, token_in_token_out=false, tool_parser=qwen3_coder, reasoning_parser=qwen3)` → `POST PROXY_URL/models/register` |
| 2 | 非流式：向 vLLM 直连发送 `build_openai_reasoning_tool_request()`（含 `tools=[get_weather]`，`enable_thinking=true`，`preserve_thinking=true`），保存响应到 vllm_resp.json；向 Nginx 发送相同请求（带 session 路径 `s/run-c101/sess-c101-xxx`），保存响应到 proxy_resp.json |
| 3 | 非流式对比：`compare.py --mode nonstream --api openai --vllm vllm_resp.json --proxy proxy_resp.json --label C101-direct-reasoning-tool --with-reasoning --skip-paths content` |
| 4 | 流式：向 vLLM 直连发送 `build_openai_reasoning_tool_stream_request()`（`stream=true`），保存 SSE 响应；向 Nginx 发送相同请求，保存 SSE 响应 |
| 5 | 流式对比：`compare.py --mode stream --api openai --vllm vllm_sse.txt --proxy proxy_sse.txt --label C101-direct-reasoning-tool-stream --with-reasoning --skip-paths content` |
| 6 | 删除模型：`DELETE PROXY_URL/models?model_name=COMPARISON_MODEL_NAME&run_id=run-c101` |

**验收标准**：

| # | 断言 | 期望 |
|---|---|---|
| 1 | vLLM 响应非空 | 是 |
| 2 | Proxy 响应非空 | 是 |
| 3 | 非流式 compare.py 退出码 | 0 |
| 4 | 流式 compare.py 退出码 | 0 |
| 5 | compare.py 输出无 FAIL 行 | 是 |
| 6 | reasoning_content 一致（--with-reasoning 启用） | 是 |
| 7 | tool_calls[].function.name 一致 | 是 |
| 8 | content 字段跳过对比（--skip-paths content） | 已配置跳过 ✓ |

---

### C102 - TITO 纯文本一致性

**目的**：验证 TITO 模式（token_in_token_out=true，无 Parser）下，content/finish_reason/usage 全字段与 vLLM 直连一致。

**Pipeline**: TITO | **流式模式**: ns+s | **Parser**: 无 | **API**: OpenAI | **轮次**: 单轮

**前置条件**：vLLM 服务在 `:8080` 正常运行；Nginx 服务在 `:12345` 正常运行

| 步骤 | 操作 |
|---|---|
| 1 | 注册模型：`register_model("run-c102", COMPARISON_MODEL_NAME, token_in_token_out=true, tokenizer_path=COMPARISON_TOKENIZER_PATH)` → `POST PROXY_URL/models/register` |
| 2 | 非流式：向 vLLM 直连发送 `build_openai_plain_request()`（`messages=[{"role":"user","content":"Say hello in one sentence."}]`，`enable_thinking=false`），保存响应；向 Nginx 发送相同请求（带 session 路径），保存响应 |
| 3 | 非流式对比：`compare.py --mode nonstream --api openai --vllm vllm_resp.json --proxy proxy_resp.json --label C102-tito-plain` |
| 4 | 流式：向 vLLM 直连发送 `build_openai_plain_stream_request()`（`stream=true`），保存 SSE 响应；向 Nginx 发送相同请求，保存 SSE 响应 |
| 5 | 流式对比：`compare.py --mode stream --api openai --vllm vllm_sse.txt --proxy proxy_sse.txt --label C102-tito-plain-stream` |
| 6 | 删除模型：`DELETE PROXY_URL/models?model_name=COMPARISON_MODEL_NAME&run_id=run-c102` |

**验收标准**：

| # | 断言 | 期望 |
|---|---|---|
| 1 | vLLM 响应非空 | 是 |
| 2 | Proxy 响应非空 | 是 |
| 3 | 非流式 compare.py 退出码 | 0 |
| 4 | 流式 compare.py 退出码 | 0 |
| 5 | compare.py 输出无 FAIL 行 | 是 |
| 6 | content 字段一致（去除空白后） | 是 |
| 7 | finish_reason 一致 | 是 |
| 8 | usage（prompt_tokens/completion_tokens/total_tokens）均 > 0 | 是 |

---

### C103 - TITO Tool 一致性

**目的**：验证 TITO 模式 + Tool Parser 下，tool_calls 结构与 vLLM 直连一致，arguments 内无 token 泄漏。

**Pipeline**: TITO | **流式模式**: ns+s | **Parser**: Tool | **API**: OpenAI | **轮次**: 单轮

**前置条件**：vLLM 服务在 `:8080` 正常运行；Nginx 服务在 `:12345` 正常运行

| 步骤 | 操作 |
|---|---|
| 1 | 注册模型：`register_model("run-c103", COMPARISON_MODEL_NAME, token_in_token_out=true, tokenizer_path=COMPARISON_TOKENIZER_PATH, tool_parser=qwen3_coder)` → `POST PROXY_URL/models/register` |
| 2 | 非流式：向 vLLM 直连发送 `build_openai_tool_request()`（含 `tools=[get_weather]`，`enable_thinking=false`），保存响应；向 Nginx 发送相同请求，保存响应 |
| 3 | 非流式对比：`compare.py --mode nonstream --api openai --vllm vllm_resp.json --proxy proxy_resp.json --label C103-tito-tool` |
| 4 | 流式：向 vLLM 直连发送 `build_openai_tool_stream_request()`（`stream=true`），保存 SSE 响应；向 Nginx 发送相同请求，保存 SSE 响应 |
| 5 | 流式对比：`compare.py --mode stream --api openai --vllm vllm_sse.txt --proxy proxy_sse.txt --label C103-tito-tool-stream` |
| 6 | 删除模型：`DELETE PROXY_URL/models?model_name=COMPARISON_MODEL_NAME&run_id=run-c103` |

**验收标准**：

| # | 断言 | 期望 |
|---|---|---|
| 1 | vLLM 响应非空 | 是 |
| 2 | Proxy 响应非空 | 是 |
| 3 | 非流式 compare.py 退出码 | 0 |
| 4 | 流式 compare.py 退出码 | 0 |
| 5 | compare.py 输出无 FAIL 行 | 是 |
| 6 | tool_calls[].function.name 一致 | 是 |
| 7 | tool_calls[].function.arguments 一致 | 是 |
| 8 | arguments 内无禁止特殊 token（ Boutique 等） | 是 |
| 9 | arguments 是合法 JSON | 是 |

---

### C104 - TITO Reasoning 一致性

**目的**：验证 TITO 模式 + Reasoning Parser 下，reasoning_content 字段与 vLLM 直连一致，content 剥离 Boutique 标记。

**Pipeline**: TITO | **流式模式**: ns+s | **Parser**: Reasoning | **API**: OpenAI | **轮次**: 单轮

**前置条件**：vLLM 服务在 `:8080` 正常运行；Nginx 服务在 `:12345` 正常运行

| 步骤 | 操作 |
|---|---|
| 1 | 注册模型：`register_model("run-c104", COMPARISON_MODEL_NAME, token_in_token_out=true, tokenizer_path=COMPARISON_TOKENIZER_PATH, reasoning_parser=qwen3)` → `POST PROXY_URL/models/register` |
| 2 | 非流式：向 vLLM 直连发送 `build_openai_reasoning_request()`（`messages=[{"role":"user","content":"Solve: if x+3=7, what is x? Think step by step."}]`，`preserve_thinking=true, enable_thinking=true`），保存响应；向 Nginx 发送相同请求，保存响应 |
| 3 | 非流式对比：`compare.py --mode nonstream --api openai --vllm vllm_resp.json --proxy proxy_resp.json --label C104-tito-reasoning --with-reasoning` |
| 4 | 流式：向 vLLM 直连发送 `build_openai_reasoning_stream_request()`（`stream=true`），保存 SSE 响应；向 Nginx 发送相同请求，保存 SSE 响应 |
| 5 | 流式对比：`compare.py --mode stream --api openai --vllm vllm_sse.txt --proxy proxy_sse.txt --label C104-tito-reasoning-stream --with-reasoning` |
| 6 | 删除模型：`DELETE PROXY_URL/models?model_name=COMPARISON_MODEL_NAME&run_id=run-c104` |

**验收标准**：

| # | 断言 | 期望 |
|---|---|---|
| 1 | vLLM 响应非空 | 是 |
| 2 | Proxy 响应非空 | 是 |
| 3 | 非流式 compare.py 退出码 | 0 |
| 4 | 流式 compare.py 退出码 | 0 |
| 5 | compare.py 输出无 FAIL 行 | 是 |
| 6 | reasoning_content 一致（--with-reasoning 启用） | 是 |
| 7 | content 不含 Boutique 等 thinking 标记 | 是 |
| 8 | provider_specific_fields.reasoning 有效 | 是 |

---

### C105 - TITO Reasoning+Tool 一致性

**目的**：验证 TITO 模式 + T+R Parser 下，reasoning + tool_calls 全字段与 vLLM 直连一致。

**Pipeline**: TITO | **流式模式**: ns+s | **Parser**: T+R | **API**: OpenAI | **轮次**: 单轮

**前置条件**：vLLM 服务在 `:8080` 正常运行；Nginx 服务在 `:12345` 正常运行

| 步骤 | 操作 |
|---|---|
| 1 | 注册模型：`register_model("run-c105", COMPARISON_MODEL_NAME, token_in_token_out=true, tokenizer_path=COMPARISON_TOKENIZER_PATH, tool_parser=qwen3_coder, reasoning_parser=qwen3)` → `POST PROXY_URL/models/register` |
| 2 | 非流式：向 vLLM 直连发送 `build_openai_reasoning_tool_request()`（含 `tools=[get_weather]`，`preserve_thinking=true, enable_thinking=true`），保存响应；向 Nginx 发送相同请求，保存响应 |
| 3 | 非流式对比：`compare.py --mode nonstream --api openai --vllm vllm_resp.json --proxy proxy_resp.json --label C105-tito-reasoning-tool --with-reasoning` |
| 4 | 流式：向 vLLM 直连发送 `build_openai_reasoning_tool_stream_request()`（`stream=true`），保存 SSE 响应；向 Nginx 发送相同请求，保存 SSE 响应 |
| 5 | 流式对比：`compare.py --mode stream --api openai --vllm vllm_sse.txt --proxy proxy_sse.txt --label C105-tito-reasoning-tool-stream --with-reasoning` |
| 6 | 删除模型：`DELETE PROXY_URL/models?model_name=COMPARISON_MODEL_NAME&run_id=run-c105` |

**验收标准**：

| # | 断言 | 期望 |
|---|---|---|
| 1 | vLLM 响应非空 | 是 |
| 2 | Proxy 响应非空 | 是 |
| 3 | 非流式 compare.py 退出码 | 0 |
| 4 | 流式 compare.py 退出码 | 0 |
| 5 | compare.py 输出无 FAIL 行 | 是 |
| 6 | reasoning_content 一致 | 是 |
| 7 | tool_calls[].function.name 一致 | 是 |
| 8 | tool_calls[].function.arguments 是合法 JSON 且无特殊 token | 是 |
| 9 | provider_specific_fields.reasoning 有效 | 是 |

---

### C106 - 多轮 T+R 非流式一致性

**目的**：验证 TITO + T+R Parser 下，4 轮非流式请求每轮与 vLLM 直连一致；内嵌参数变体子场景（tool_choice=required、enable_thinking=false、tool_choice=named）。

**Pipeline**: TITO | **流式模式**: ns | **Parser**: T+R | **API**: OpenAI | **轮次**: 4 轮（含 3 个子场景）

**前置条件**：vLLM 服务在 `:8080` 正常运行；Nginx 服务在 `:12345` 正常运行

**子场景说明**：
- **第1轮**：基础 T+R 请求（`preserve_thinking=true, enable_thinking=true, tool_choice=auto`）
- **第2轮**（子场景 a）：`tool_choice=required` 强制调用（qwen3_coder 专属路径）
- **第3轮**（子场景 c）：`enable_thinking=false` 关闭推理（qwen3 专属 kwargs）
- **第4轮**（子场景 b）：`tool_choice={"type":"function","function":{"name":"get_weather"}}` 指定工具（qwen3_xml 专属路径），`--skip-paths finish_reason,arguments`

| 步骤 | 操作 |
|---|---|
| 1 | 注册模型：`register_model("run-c106", COMPARISON_MODEL_NAME, token_in_token_out=true, tokenizer_path=COMPARISON_TOKENIZER_PATH, tool_parser=qwen3_coder, reasoning_parser=qwen3)` |
| 2 | **第1轮**：向 vLLM 和 Nginx 各发送 `build_openai_reasoning_tool_request()`（基础 T+R），非流式对比 `--label C106-round1 --with-reasoning` |
| 3 | **第2轮**：在第1轮请求体基础上追加 `"tool_choice":"required"`，向 vLLM 和 Nginx 各发送，非流式对比 `--label C106-round2-required --with-reasoning` |
| 4 | **第3轮**：在第1轮请求体基础上替换 `"enable_thinking":true → "enable_thinking":false`，向 vLLM 和 Nginx 各发送，非流式对比 `--label C106-round3-nothink --with-reasoning` |
| 5 | **第4轮**：在第1轮请求体基础上追加 `"tool_choice":{"type":"function","function":{"name":"get_weather"}}`，向 vLLM 和 Nginx 各发送，非流式对比 `--label C106-round4-named --with-reasoning --skip-paths finish_reason,arguments` |
| 6 | 删除模型：`DELETE PROXY_URL/models?model_name=COMPARISON_MODEL_NAME&run_id=run-c106` |

> **注意**：C 系列不验证 cache_hit_tokens 递增（由 P4xx 专项覆盖）。每轮请求独立对比，不依赖前轮响应累积。

**验收标准**：

| # | 断言 | 期望 |
|---|---|---|
| 1 | 第1轮 compare.py 退出码 | 0 |
| 2 | 第2轮 compare.py 退出码 | 0 |
| 3 | 第3轮 compare.py 退出码 | 0 |
| 4 | 第4轮 compare.py 退出码 | 0 |
| 5 | 所有轮次 compare.py 输出无 FAIL 行 | 是 |
| 6 | 第2轮 tool_choice=required 场景：响应含 tool_calls | 是 |
| 7 | 第3轮 enable_thinking=false 场景：reasoning_content 为空或不存在 | 是 |
| 8 | 第4轮 tool_choice=named 场景：finish_reason 和 arguments 跳过对比（--skip-paths） | 已配置跳过 ✓ |

---

### C107 - 多轮 T+R 流式一致性

**目的**：验证 TITO + T+R Parser 下，4 轮流式请求每轮与 vLLM 直连一致；含与 C106 相同的 3 个子场景。

**Pipeline**: TITO | **流式模式**: s | **Parser**: T+R | **API**: OpenAI | **轮次**: 4 轮（含 3 个子场景）

**前置条件**：vLLM 服务在 `:8080` 正常运行；Nginx 服务在 `:12345` 正常运行

**子场景说明**（同 C106）：
- **第1轮**：基础 T+R 流式请求
- **第2轮**（子场景 a）：`tool_choice=required` 流式
- **第3轮**（子场景 c）：`enable_thinking=false` 流式
- **第4轮**（子场景 b）：`tool_choice=named` 流式

| 步骤 | 操作 |
|---|---|
| 1 | 注册模型：`register_model("run-c107", COMPARISON_MODEL_NAME, token_in_token_out=true, tokenizer_path=COMPARISON_TOKENIZER_PATH, tool_parser=qwen3_coder, reasoning_parser=qwen3)` |
| 2 | **第1轮**：向 vLLM 和 Nginx 各发送 `build_openai_reasoning_tool_stream_request()`（`stream=true`），流式对比 `--label C107-round1-stream --with-reasoning` |
| 3 | **第2轮**：在第1轮请求体基础上追加 `"tool_choice":"required"`，向 vLLM 和 Nginx 各发送流式请求，流式对比 `--label C107-round2-required-stream --with-reasoning` |
| 4 | **第3轮**：在第1轮请求体基础上替换 `"enable_thinking":true → "enable_thinking":false`，流式对比 `--label C107-round3-nothink-stream --with-reasoning` |
| 5 | **第4轮**：在第1轮请求体基础上追加 `"tool_choice":{"type":"function","function":{"name":"get_weather"}}`，流式对比 `--label C107-round4-named-stream --with-reasoning` |
| 6 | 删除模型：`DELETE PROXY_URL/models?model_name=COMPARISON_MODEL_NAME&run_id=run-c107` |

> **注意**：流式对比将 SSE chunks 合并后比较完整语义。C 系列不验证 cache_hit_tokens 递增。

**验收标准**：

| # | 断言 | 期望 |
|---|---|---|
| 1 | 第1轮 compare.py 退出码 | 0 |
| 2 | 第2轮 compare.py 退出码 | 0 |
| 3 | 第3轮 compare.py 退出码 | 0 |
| 4 | 第4轮 compare.py 退出码 | 0 |
| 5 | 所有轮次 compare.py 输出无 FAIL 行 | 是 |
| 6 | 流式 SSE 包含 `data:` 前缀和 `[DONE]` 终止符 | 是 |
| 7 | 合并后 content 无特殊 token 泄漏 | 是 |

---

## C2xx - Claude 格式对比

Claude 格式对比通过 LiteLLM 的 `/v1/messages` 端点进行。vLLM 原生支持 `/v1/messages`，Proxy 链路经 Nginx → LiteLLM（Claude→OpenAI 适配）→ trajproxy → vLLM。Claude 响应字段为 `content[]`（含 `type`/`text`/`thinking`/`tool_use`）、`usage.input_tokens`/`usage.output_tokens`、`stop_reason` 等。

---

### C201 - Claude Direct T+R Parser 一致性

**目的**：验证 Direct 模式下，Claude 格式 thinking blocks + tool_use 全字段与 vLLM 直连一致。

**Pipeline**: Direct | **流式模式**: ns+s | **Parser**: T+R | **API**: Claude | **轮次**: 单轮

**前置条件**：vLLM 服务在 `:8080` 正常运行；Nginx 服务在 `:12345` 正常运行

| 步骤 | 操作 |
|---|---|
| 1 | 注册模型：`register_model("run-c201", COMPARISON_MODEL_NAME, token_in_token_out=false, tool_parser=qwen3_coder, reasoning_parser=qwen3)` → `POST PROXY_URL/models/register` |
| 2 | 非流式：向 vLLM 直连 `:8080/v1/messages` 发送 `build_claude_reasoning_tool_request()`（含 `tools=[get_weather]`，Claude 格式请求体），保存响应；向 Nginx `:12345/s/run-c201/sess-c201-xxx/v1/messages` 发送相同请求，保存响应 |
| 3 | 非流式对比：`compare.py --mode nonstream --api claude --vllm vllm_resp.json --proxy proxy_resp.json --label C201-claude-direct-reasoning-tool` |
| 4 | 流式：向 vLLM 和 Nginx 各发送 `build_claude_reasoning_tool_stream_request()`（Claude 格式，`stream=true`），保存 SSE 响应 |
| 5 | 流式对比：`compare.py --mode stream --api claude --vllm vllm_sse.txt --proxy proxy_sse.txt --label C201-claude-direct-reasoning-tool-stream` |
| 6 | 删除模型：`DELETE PROXY_URL/models?model_name=COMPARISON_MODEL_NAME&run_id=run-c201` |

**验收标准**：

| # | 断言 | 期望 |
|---|---|---|
| 1 | vLLM 响应非空 | 是 |
| 2 | Proxy 响应非空 | 是 |
| 3 | 非流式 compare.py 退出码 | 0 |
| 4 | 流式 compare.py 退出码 | 0 |
| 5 | compare.py 输出无 FAIL 行 | 是 |
| 6 | thinking blocks 一致（存在 + 无特殊 token） | 是 |
| 7 | tool_use[].name 一致 | 是 |
| 8 | tool_use[].input 一致且无特殊 token | 是 |
| 9 | usage.input_tokens > 0, usage.output_tokens > 0 | 是 |

---

### C202 - Claude TITO 纯文本一致性

**目的**：验证 TITO 模式（无 Parser）下，Claude 基线纯文本响应与 vLLM 直连一致。

**Pipeline**: TITO | **流式模式**: ns+s | **Parser**: 无 | **API**: Claude | **轮次**: 单轮

**前置条件**：vLLM 服务在 `:8080` 正常运行；Nginx 服务在 `:12345` 正常运行

| 步骤 | 操作 |
|---|---|
| 1 | 注册模型：`register_model("run-c202", COMPARISON_MODEL_NAME, token_in_token_out=true, tokenizer_path=COMPARISON_TOKENIZER_PATH)` → `POST PROXY_URL/models/register` |
| 2 | 非流式：向 vLLM 直连 `:8080/v1/messages` 发送 `build_claude_plain_request()`（`messages=[{"role":"user","content":"Say hello in one sentence."}]`），保存响应；向 Nginx `:12345/s/run-c202/sess-c202-xxx/v1/messages` 发送相同请求，保存响应 |
| 3 | 非流式对比：`compare.py --mode nonstream --api claude --vllm vllm_resp.json --proxy proxy_resp.json --label C202-claude-tito-plain` |
| 4 | 流式：向 vLLM 和 Nginx 各发送 `build_claude_plain_stream_request()`（`stream=true`），保存 SSE 响应 |
| 5 | 流式对比：`compare.py --mode stream --api claude --vllm vllm_sse.txt --proxy proxy_sse.txt --label C202-claude-tito-plain-stream` |
| 6 | 删除模型：`DELETE PROXY_URL/models?model_name=COMPARISON_MODEL_NAME&run_id=run-c202` |

**验收标准**：

| # | 断言 | 期望 |
|---|---|---|
| 1 | vLLM 响应非空 | 是 |
| 2 | Proxy 响应非空 | 是 |
| 3 | 非流式 compare.py 退出码 | 0 |
| 4 | 流式 compare.py 退出码 | 0 |
| 5 | compare.py 输出无 FAIL 行 | 是 |
| 6 | content.text 存在且无特殊 token | 是 |
| 7 | type/role/stop_reason 一致 | 是 |
| 8 | usage.input_tokens > 0, usage.output_tokens > 0 | 是 |

---

### C203 - Claude TITO Tool 一致性

**目的**：验证 TITO + Tool Parser 下，Claude tool_use 结构与 vLLM 直连一致，arguments 禁止特殊 token 泄漏。

**Pipeline**: TITO | **流式模式**: ns+s（流式暂跳过） | **Parser**: Tool | **API**: Claude | **轮次**: 单轮

**前置条件**：vLLM 服务在 `:8080` 正常运行；Nginx 服务在 `:12345` 正常运行

| 步骤 | 操作 |
|---|---|
| 1 | 注册模型：`register_model("run-c203", COMPARISON_MODEL_NAME, token_in_token_out=true, tokenizer_path=COMPARISON_TOKENIZER_PATH, tool_parser=qwen3_coder)` → `POST PROXY_URL/models/register` |
| 2 | 非流式：向 vLLM 直连 `:8080/v1/messages` 发送 `build_claude_tool_request()`（含 `tools=[get_weather]`，Claude 格式），保存响应；向 Nginx 发送相同请求，保存响应 |
| 3 | 非流式对比：`compare.py --mode nonstream --api claude --vllm vllm_resp.json --proxy proxy_resp.json --label C203-claude-tito-tool` |
| 4 | 流式：向 vLLM 和 Nginx 各发送 `build_claude_tool_stream_request()`，保存 SSE 响应（**流式请求正常发送，但对比验证暂不执行**——`compare_claude_stream` 调用已注释禁用，详见下方已知问题） |
| 5 | 删除模型：`DELETE PROXY_URL/models?model_name=COMPARISON_MODEL_NAME&run_id=run-c203` |

> **已知问题**：Claude Tool 流式对比暂跳过。根因：Claude `/v1/messages` 请求不传 `thinking` 参数（符合 Anthropic 规范），LiteLLM 做 Claude→OpenAI 转换时未注入 `enable_thinking=false`，导致 parser_manager 误判 `enable_thinking=True`，流式合并后 text 块残留 Boutique token。修复方向：parser_manager 需感知请求来源格式，Claude 路径无 `chat_template_kwargs` 时 `enable_thinking` 应默认为 `False`。

**验收标准**：

| # | 断言 | 期望 |
|---|---|---|
| 1 | vLLM 非流式响应非空 | 是 |
| 2 | Proxy 非流式响应非空 | 是 |
| 3 | 非流式 compare.py 退出码 | 0 |
| 4 | 非流式 compare.py 输出无 FAIL 行 | 是 |
| 5 | tool_use[].name 一致 | 是 |
| 6 | tool_use[].input 一致且无特殊 token | 是 |
| 7 | usage.input_tokens > 0, usage.output_tokens > 0 | 是 |
| 8 | 流式对比暂不执行（已知问题） | 已跳过 |

---

### C204 - Claude TITO Reasoning 一致性

**目的**：验证 TITO + Reasoning Parser 下，Claude thinking blocks 与 vLLM 直连一致，text 块禁止 Boutique 泄漏。

**Pipeline**: TITO | **流式模式**: ns+s | **Parser**: Reasoning | **API**: Claude | **轮次**: 单轮

**前置条件**：vLLM 服务在 `:8080` 正常运行；Nginx 服务在 `:12345` 正常运行

| 步骤 | 操作 |
|---|---|
| 1 | 注册模型：`register_model("run-c204", COMPARISON_MODEL_NAME, token_in_token_out=true, tokenizer_path=COMPARISON_TOKENIZER_PATH, reasoning_parser=qwen3)` → `POST PROXY_URL/models/register` |
| 2 | 非流式：向 vLLM 直连 `:8080/v1/messages` 发送 `build_claude_reasoning_request()`（`messages=[{"role":"user","content":"Solve: if x+3=7, what is x? Think step by step."}]`），保存响应；向 Nginx 发送相同请求，保存响应 |
| 3 | 非流式对比：`compare.py --mode nonstream --api claude --vllm vllm_resp.json --proxy proxy_resp.json --label C204-claude-tito-reasoning` |
| 4 | 流式：向 vLLM 和 Nginx 各发送 `build_claude_reasoning_stream_request()`（`stream=true`），保存 SSE 响应 |
| 5 | 流式对比：`compare.py --mode stream --api claude --vllm vllm_sse.txt --proxy proxy_sse.txt --label C204-claude-tito-reasoning-stream` |
| 6 | 删除模型：`DELETE PROXY_URL/models?model_name=COMPARISON_MODEL_NAME&run_id=run-c204` |

**验收标准**：

| # | 断言 | 期望 |
|---|---|---|
| 1 | vLLM 响应非空 | 是 |
| 2 | Proxy 响应非空 | 是 |
| 3 | 非流式 compare.py 退出码 | 0 |
| 4 | 流式 compare.py 退出码 | 0 |
| 5 | compare.py 输出无 FAIL 行 | 是 |
| 6 | thinking blocks 存在且无特殊 token | 是 |
| 7 | text 块存在且无 Boutique 泄漏 | 是 |
| 8 | usage.input_tokens > 0, usage.output_tokens > 0 | 是 |

---

### C205 - Claude TITO Reasoning+Tool 一致性

**目的**：验证 TITO + T+R Parser 下，Claude thinking + tool_use 全字段与 vLLM 直连一致。

**Pipeline**: TITO | **流式模式**: ns+s | **Parser**: T+R | **API**: Claude | **轮次**: 单轮

**前置条件**：vLLM 服务在 `:8080` 正常运行；Nginx 服务在 `:12345` 正常运行

| 步骤 | 操作 |
|---|---|
| 1 | 注册模型：`register_model("run-c205", COMPARISON_MODEL_NAME, token_in_token_out=true, tokenizer_path=COMPARISON_TOKENIZER_PATH, tool_parser=qwen3_coder, reasoning_parser=qwen3)` → `POST PROXY_URL/models/register` |
| 2 | 非流式：向 vLLM 直连 `:8080/v1/messages` 发送 `build_claude_reasoning_tool_request()`（含 `tools=[get_weather]`，`max_tokens=2048`），保存响应；向 Nginx 发送相同请求，保存响应 |
| 3 | 非流式对比：`compare.py --mode nonstream --api claude --vllm vllm_resp.json --proxy proxy_resp.json --label C205-claude-tito-reasoning-tool` |
| 4 | 流式：向 vLLM 和 Nginx 各发送 `build_claude_reasoning_tool_stream_request()`（`stream=true, max_tokens=2048`），保存 SSE 响应 |
| 5 | 流式对比：`compare.py --mode stream --api claude --vllm vllm_sse.txt --proxy proxy_sse.txt --label C205-claude-tito-reasoning-tool-stream` |
| 6 | 删除模型：`DELETE PROXY_URL/models?model_name=COMPARISON_MODEL_NAME&run_id=run-c205` |

> **注意**：TITO 路径 chat template 无 `enable_thinking`，模型输出冗长，`max_tokens` 设为 2048。

**验收标准**：

| # | 断言 | 期望 |
|---|---|---|
| 1 | vLLM 响应非空 | 是 |
| 2 | Proxy 响应非空 | 是 |
| 3 | 非流式 compare.py 退出码 | 0 |
| 4 | 流式 compare.py 退出码 | 0 |
| 5 | compare.py 输出无 FAIL 行 | 是 |
| 6 | thinking blocks 存在且无特殊 token | 是 |
| 7 | tool_use[].name 一致 | 是 |
| 8 | tool_use[].input 一致且无特殊 token | 是 |
| 9 | usage.input_tokens > 0, usage.output_tokens > 0 | 是 |

---

### C206 - Claude 多轮 T+R 非流式一致性

**目的**：验证 TITO + T+R Parser 下，3 轮 Claude 非流式请求每轮与 vLLM 直连一致。不含 kwargs/tool_choice 子场景（Claude 不支持 required/named）。

**Pipeline**: TITO | **流式模式**: ns | **Parser**: T+R | **API**: Claude | **轮次**: 3 轮

**前置条件**：vLLM 服务在 `:8080` 正常运行；Nginx 服务在 `:12345` 正常运行

**轮次间消息累积**：
- **第1轮**：基础 T+R 请求（`messages=[user: "Shanghai"]`）
- **第2轮**：替换 `Shanghai → Beijing`（相同 tools，不同 question）
- **第3轮**：替换 `Shanghai → Shenzhen`（相同 tools，不同 question）

> 每轮请求独立发送，不依赖前轮 assistant 响应累积（C 系列不验 cache_hit，由 P4xx 覆盖）。

| 步骤 | 操作 |
|---|---|
| 1 | 注册模型：`register_model("run-c206", COMPARISON_MODEL_NAME, token_in_token_out=true, tokenizer_path=COMPARISON_TOKENIZER_PATH, tool_parser=qwen3_coder, reasoning_parser=qwen3)` |
| 2 | **第1轮**：向 vLLM 和 Nginx 各发送 `build_claude_reasoning_tool_request()`（Claude 格式，含 tools），非流式对比 `--label C206-round1` |
| 3 | **第2轮**：在第1轮请求体基础上 `sed 's/Shanghai/Beijing/'`，向 vLLM 和 Nginx 各发送，非流式对比 `--label C206-round2` |
| 4 | **第3轮**：在第1轮请求体基础上 `sed 's/Shanghai/Shenzhen/'`，向 vLLM 和 Nginx 各发送，非流式对比 `--label C206-round3` |
| 5 | 删除模型：`DELETE PROXY_URL/models?model_name=COMPARISON_MODEL_NAME&run_id=run-c206` |

**验收标准**：

| # | 断言 | 期望 |
|---|---|---|
| 1 | 第1轮 compare.py 退出码 | 0 |
| 2 | 第2轮 compare.py 退出码 | 0 |
| 3 | 第3轮 compare.py 退出码 | 0 |
| 4 | 所有轮次 compare.py 输出无 FAIL 行 | 是 |
| 5 | 每轮 thinking blocks 存在且无特殊 token | 是 |
| 6 | 每轮 tool_use 结构一致 | 是 |
| 7 | 每轮 usage.input_tokens > 0, usage.output_tokens > 0 | 是 |

---

### C207 - Claude 多轮 T+R 流式一致性

**目的**：验证 TITO + T+R Parser 下，3 轮 Claude 流式请求每轮与 vLLM 直连一致。流式版本，不含 kwargs/tool_choice 子场景。

**Pipeline**: TITO | **流式模式**: s | **Parser**: T+R | **API**: Claude | **轮次**: 3 轮

**前置条件**：vLLM 服务在 `:8080` 正常运行；Nginx 服务在 `:12345` 正常运行

**轮次间消息累积**（同 C206）：
- **第1轮**：基础 T+R 流式请求
- **第2轮**：替换 `Shanghai → Beijing`
- **第3轮**：替换 `Shanghai → Shenzhen`

| 步骤 | 操作 |
|---|---|
| 1 | 注册模型：`register_model("run-c207", COMPARISON_MODEL_NAME, token_in_token_out=true, tokenizer_path=COMPARISON_TOKENIZER_PATH, tool_parser=qwen3_coder, reasoning_parser=qwen3)` |
| 2 | **第1轮**：向 vLLM 和 Nginx 各发送 `build_claude_reasoning_tool_stream_request()`（Claude 格式，`stream=true`），流式对比 `--label C207-round1-stream` |
| 3 | **第2轮**：在第1轮请求体基础上 `sed 's/Shanghai/Beijing/'`，流式对比 `--label C207-round2-stream` |
| 4 | **第3轮**：在第1轮请求体基础上 `sed 's/Shanghai/Shenzhen/'`，流式对比 `--label C207-round3-stream` |
| 5 | 删除模型：`DELETE PROXY_URL/models?model_name=COMPARISON_MODEL_NAME&run_id=run-c207` |

**验收标准**：

| # | 断言 | 期望 |
|---|---|---|
| 1 | 第1轮 compare.py 退出码 | 0 |
| 2 | 第2轮 compare.py 退出码 | 0 |
| 3 | 第3轮 compare.py 退出码 | 0 |
| 4 | 所有轮次 compare.py 输出无 FAIL 行 | 是 |
| 5 | 流式 SSE 包含 `message_start` 和 `message_stop` 事件 | 是 |
| 6 | 合并后 thinking blocks 存在且无特殊 token | 是 |
| 7 | 合并后 tool_use 结构一致 | 是 |
| 8 | 流式 usage（message_delta 中）input_tokens/output_tokens > 0 | 是 |