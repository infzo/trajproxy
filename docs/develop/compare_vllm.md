# TrajProxy vs vLLM OpenAI Chat Completions 接口对比分析

> **导航**: [文档中心](../README.md) | [API 参考](api_reference.md) | [Parser 设计](../design/parser.md)

> 对比版本: TrajProxy (当前) vs vLLM 0.16.0

## 1. 概述

本文档对比分析 TrajProxy 和 vLLM 的 OpenAI Chat Completions 语义接口行为差异。一致性定义为：输入相同格式的参数，得到相同格式的返回。

## 2. 核心文件参考

**TrajProxy:**
- `traj_proxy/proxy_core/routes.py` - 路由定义
- `traj_proxy/proxy_core/processor.py` - 非流式处理
- `traj_proxy/proxy_core/streaming_processor.py` - 流式处理
- `traj_proxy/proxy_core/prompt_builder.py` - 响应构建
- `traj_proxy/proxy_core/infer_client.py` - 推理服务客户端

**vLLM:**
- `vllm/entrypoints/openai/chat_completion/api_router.py` - 路由定义
- `vllm/entrypoints/openai/chat_completion/protocol.py` - 请求/响应协议
- `vllm/entrypoints/openai/chat_completion/serving.py` - 核心处理逻辑

---

## 3. 请求参数支持差异

### 3.1 完整对比表

| 参数 | vLLM 支持 | TrajProxy 支持 | 差异说明 |
|------|-----------|----------------|----------|
| `messages` | ✅ 完整支持 | ✅ 完整支持 | 一致 |
| `model` | ✅ | ✅ | 一致 |
| `frequency_penalty` | ✅ | ✅ (转发) | 一致 |
| `presence_penalty` | ✅ | ✅ (转发) | 一致 |
| `temperature` | ✅ | ✅ (转发) | 一致 |
| `top_p` | ✅ | ✅ (转发) | 一致 |
| `max_tokens` | ✅ | ✅ (转发) | 一致 |
| `max_completion_tokens` | ✅ 支持，优先于 max_tokens | ❌ 不支持 | **差异** |
| `n` | ✅ 支持多个 choices | ❌ 不支持 | **差异** |
| `stream` | ✅ | ✅ | 一致 |
| `stream_options` | ✅ 支持 include_usage, continuous_usage_stats | ❌ 不支持 | **差异** |
| `stop` | ✅ 支持字符串或数组 | ✅ (转发) | 一致 |
| `tools` | ✅ | ✅ | 一致 |
| `tool_choice` | ✅ 支持 none/auto/required/命名工具 | ⚠️ 部分支持 | **差异**: TrajProxy 直接转发，不做本地验证 |
| `parallel_tool_calls` | ✅ | ✅ (转发) | 一致 |
| `response_format` | ✅ 支持 json_object/json_schema/structural_tag | ❌ 不支持 | **差异** |
| `logprobs` | ✅ | ❌ 不支持 | **差异** |
| `top_logprobs` | ✅ | ❌ 不支持 | **差异** |
| `logit_bias` | ✅ | ❌ 不支持 | **差异** |
| `seed` | ✅ | ✅ (转发) | 一致 |
| `user` | ✅ (忽略) | ❌ 不支持 | 差异较小 |
| `reasoning_effort` | ✅ 支持 low/medium/high | ❌ 不支持 | **差异** |
| `include_reasoning` | ✅ | ✅ | 一致 |
| `echo` | ✅ | ❌ 不支持 | **差异** |
| `chat_template` | ✅ 支持自定义模板 | ❌ 不支持 | **差异** |
| `chat_template_kwargs` | ✅ | ❌ 不支持 | **差异** |
| `documents` | ✅ | ✅ 支持 RAG | 一致 |
| `return_token_ids` | ✅ | ❌ 不支持 | **差异** |
| `priority` | ✅ | ❌ 不支持 | **差异** |

### 3.2 高优先级缺失参数

#### `max_completion_tokens`
- **OpenAI 规范**: 新推荐的参数，用于替代 `max_tokens`
- **vLLM 行为**: 优先使用 `max_completion_tokens`，回退到 `max_tokens`
- **TrajProxy**: 不支持，仅转发 `max_tokens`
- **影响**: 使用 OpenAI 最新 SDK 时可能出现参数不生效

#### `n`
- **OpenAI 规范**: 生成 n 个不同的响应 choices
- **vLLM 行为**: 完整支持，返回 `choices[0..n-1]`
- **TrajProxy**: 不支持，始终返回单个 choice
- **影响**: 需要多样性的场景无法使用

#### `stream_options`
- **OpenAI 规范**:
  ```json
  {
    "stream_options": {
      "include_usage": true,           // 流结束后发送 usage
      "continuous_usage_stats": true   // 每个 chunk 包含 usage
    }
  }
  ```
- **vLLM 行为**: 完整支持
- **TrajProxy**: 不支持
- **影响**: 流式请求无法获取 token 统计信息

#### `response_format`
- **OpenAI 规范**: 支持 `json_object`, `json_schema`, `text`
- **vLLM 行为**: 支持 `json_object`, `json_schema`, `structural_tag`
- **TrajProxy**: 不支持
- **影响**: 结构化输出场景受限

#### `reasoning_effort`
- **OpenAI 规范**: 控制 reasoning 模型的思考深度 (`low`/`medium`/`high`)
- **vLLM 行为**: 支持，传递给 chat template
- **TrajProxy**: 不支持
- **影响**: DeepSeek-R1 等推理模型的思考深度无法控制

---

## 4. 响应格式差异

### 4.1 非流式响应对比

| 字段 | vLLM 格式 | TrajProxy 格式 | 差异说明 |
|------|-----------|----------------|----------|
| `id` | `chatcmpl-{uuid}` | `chatcmpl-{request_id}` | 基本一致 |
| `object` | `chat.completion` | `chat.completion` | 一致 |
| `created` | Unix 时间戳 | Unix 时间戳 | 一致 |
| `model` | 模型名 | 模型名 | 一致 |
| `choices[].index` | ✅ 0..n-1 | ✅ 始终为 0 | **差异**: 不支持 n>1 |
| `choices[].message.role` | `assistant` | `assistant` | 一致 |
| `choices[].message.content` | ✅ | ✅ | 一致 |
| `choices[].message.reasoning` | ✅ vLLM 扩展字段 | ✅ TrajProxy 扩展字段 | 一致 |
| `choices[].message.tool_calls` | ✅ 完整结构 | ✅ 完整结构 | 一致 |
| `choices[].finish_reason` | `stop`/`tool_calls`/`length` | `stop`/`tool_calls` | 基本一致 |
| `choices[].logprobs` | ✅ 支持 | ❌ 不支持 | **差异** |
| `choices[].token_ids` | ✅ 支持 | ❌ 不支持 | **差异** |
| `usage.prompt_tokens` | ✅ | ✅ | 一致 |
| `usage.completion_tokens` | ✅ | ✅ | 一致 |
| `usage.total_tokens` | ✅ | ✅ | 一致 |
| `usage.prompt_tokens_details.cached_tokens` | ✅ | ❌ 不支持 | **差异** |
| `prompt_logprobs` | ✅ vLLM 扩展 | ❌ 不支持 | **差异** |
| `prompt_token_ids` | ✅ vLLM 扩展 | ❌ 不支持 | **差异** |

### 4.2 流式响应对比

| 特性 | vLLM | TrajProxy | 差异说明 |
|------|------|-----------|----------|
| 第一个 chunk 包含 role | ✅ | ✅ | 一致 |
| `stream_options.include_usage` | ✅ 流结束后发送 usage chunk | ❌ 不支持 | **差异** |
| `stream_options.continuous_usage_stats` | ✅ 每个 chunk 包含 usage | ❌ 不支持 | **差异** |
| tool_calls 增量传输 | ✅ index 递增 | ✅ | 一致 |
| reasoning 增量传输 | ✅ delta.reasoning | ✅ delta.reasoning | 一致 |
| finish_reason 时机 | 最后一个 chunk | 最后一个 chunk | 一致 |
| `[DONE]` 消息 | ✅ | ✅ | 一致 |

**vLLM 流式 usage chunk 示例**:
```json
data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","choices":[],"usage":{"prompt_tokens":10,"completion_tokens":20,"total_tokens":30}}
data: [DONE]
```

---

## 5. Tool Calling 差异

### 5.1 `tool_choice` 处理差异

| `tool_choice` 值 | vLLM 行为 | TrajProxy 行为 |
|------------------|-----------|----------------|
| `none` | **本地处理**: 即使有 tools 也不调用，返回普通文本 | **直接转发**: 依赖后端模型处理 |
| `auto` | 自动判断是否需要调用工具 | 直接转发 |
| `required` | **本地处理**: 强制调用至少一个工具 | **直接转发**: 依赖后端模型处理 |
| `{"type": "function", "function": {"name": "xxx"}}` | 验证工具存在，强制调用指定工具 | 直接转发，不做本地验证 |

### 5.2 参数验证差异

**vLLM 验证逻辑** (protocol.py):
```python
# 1. tool_choice 需要有 tools
if "tool_choice" in data and data["tool_choice"] is not None:
    if "tools" not in data or data["tools"] is None:
        raise ValueError("When using `tool_choice`, `tools` must be set.")

# 2. 命名 tool_choice 需要匹配 tools 中的工具
if isinstance(data["tool_choice"], dict):
    for tool in data["tools"]:
        if tool["function"]["name"] == function_name:
            valid_tool = True
            break
    if not valid_tool:
        raise ValueError("The tool specified in `tool_choice` does not match")
```

**TrajProxy**: 无本地验证，直接转发

### 5.3 并行工具调用

两者都支持并行工具调用，格式一致:
```json
{
  "choices": [{
    "message": {
      "tool_calls": [
        {"id": "call_xxx1", "type": "function", "function": {...}},
        {"id": "call_xxx2", "type": "function", "function": {...}}
      ]
    },
    "finish_reason": "tool_calls"
  }]
}
```

---

## 6. Reasoning (思考过程) 差异

### 6.1 Parser 支持对比

| 模型 | vLLM Parser | TrajProxy Parser |
|------|-------------|------------------|
| DeepSeek-R1 | ✅ `deepseek_r1` | ✅ 配置指定 |
| DeepSeek-V3 | ✅ `deepseek_v3` | ✅ 配置指定 |
| Qwen3 | ✅ `qwen3` | ✅ 配置指定 |
| Mistral | ✅ `mistral` | ✅ 配置指定 |
| GLM-4-MoE | ✅ `glm45` | ✅ 配置指定 |
| Hunyuan | ✅ `hunyuan_a13b` | ✅ 配置指定 |
| Kimi K2 | ✅ `kimi_k2` | ✅ 配置指定 |
| ... | 15+ 内置 | 通过配置指定 |

### 6.2 参数差异

| 参数 | vLLM | TrajProxy |
|------|------|-----------|
| `reasoning_effort` | ✅ 支持 `low`/`medium`/`high` | ❌ 不支持 |
| `include_reasoning` | ✅ 控制是否返回 reasoning 字段 | ✅ 支持 |

### 6.3 响应格式

两者一致:
```json
{
  "choices": [{
    "message": {
      "role": "assistant",
      "reasoning": "思考过程内容...",
      "content": "最终回答内容..."
    }
  }]
}
```

---

## 7. 错误处理差异

| 场景 | vLLM | TrajProxy | 差异说明 |
|------|------|-----------|----------|
| 无效 model | 404 ErrorResponse | 404 `{"detail": "模型未注册"}` | 格式不同 |
| 无效 tool_choice | 400 ValueError | 可能静默失败或依赖后端 | **差异** |
| 缺少必要参数 | 422 Pydantic 错误 | 简单验证 | **差异** |
| 流式中断 | 返回错误 chunk | 依赖后端 | **差异** |
| JSON 解析失败 | 400 错误 | 可能返回原始文本 | **差异** |

**vLLM ErrorResponse 格式**:
```json
{
  "error": {
    "message": "...",
    "type": "BadRequestError",
    "param": "tool_choice",
    "code": 400
  }
}
```

**TrajProxy ErrorResponse 格式**:
```json
{
  "detail": "模型 'xxx' 未注册"
}
```

---

## 8. 一致性评估总结

### 8.1 完全一致 ✅

- 基本请求/响应结构
- Tool calling 核心机制 (tools, tool_calls 格式)
- Reasoning 解析机制
- 流式基本格式 (SSE, [DONE])
- `documents` (RAG) 支持
- `include_reasoning` 参数

### 8.2 部分一致 ⚠️

| 方面 | 说明 |
|------|------|
| 参数传递 | TrajProxy 转发大部分参数，但不做本地处理/验证 |
| tool_choice | 参数能传递，但 vLLM 有本地验证和强制行为 |
| 错误处理 | 两边都有错误处理，但格式和行为不同 |

### 8.3 不一致 ❌

| 方面 | vLLM | TrajProxy |
|------|------|-----------|
| `n` 参数 | 支持多 choice | 不支持 |
| `response_format` | 支持结构化输出 | 不支持 |
| `logprobs` | 支持 | 不支持 |
| `stream_options` | 支持流式 usage | 不支持 |
| `reasoning_effort` | 支持 | 不支持 |
| `max_completion_tokens` | 优先使用 | 不支持 |

---

## 9. 改进建议

### 9.1 高优先级 (影响核心功能)

1. **支持 `max_completion_tokens`**
   - 兼容 OpenAI 最新规范
   - 优先级高于 `max_tokens`

2. **支持 `stream_options.include_usage`**
   - 流式结束后返回 usage chunk
   - 很多客户端依赖此功能统计 token

3. **支持 `response_format`**
   - 至少支持 `json_object`
   - 结构化输出是常见需求

### 9.2 中优先级 (提升兼容性)

1. **支持 `n` 参数**
   - 需要修改核心处理逻辑
   - 支持生成多个 choices

2. **支持 `reasoning_effort`**
   - 传递给 chat template
   - 控制推理模型思考深度

3. **增强 `tool_choice` 本地验证**
   - 验证 `tool_choice` 与 `tools` 的匹配
   - 本地处理 `none` 和 `required`

### 9.3 低优先级 (锦上添花)

1. **支持 `logprobs`/`top_logprobs`**
2. **支持 `logit_bias`**
3. **支持 `echo` 参数**
4. **统一错误响应格式**

---

## 10. 附录: vLLM 内置 Parser 列表

### Tool Parsers (30+)

`deepseek_v3`, `deepseek_v31`, `deepseek_v32`, `ernie45`, `glm45`, `glm47`, `granite-20b-fc`, `granite`, `hermes`, `hunyuan_a13b`, `internlm`, `jamba`, `kimi_k2`, `llama3_json`, `llama4_json`, `llama4_pythonic`, `longcat`, `minimax_m2`, `minimax`, `mistral`, `olmo3`, `openai`, `phi4_mini_json`, `pythonic`, `qwen3_coder`, `qwen3_xml`, `seed_oss`, `step3`, `step3p5`, `xlam`, `gigachat3`, `functiongemma`

### Reasoning Parsers (15+)

`deepseek_r1`, `deepseek_v3`, `ernie45`, `glm45`, `openai_gptoss`, `granite`, `holo2`, `hunyuan_a13b`, `kimi_k2`, `minimax_m2`, `minimax_m2_append_think`, `mistral`, `olmo3`, `qwen3`, `seed_oss`, `step3`, `step3p5`
