# OpenAI 兼容接口规范

> **导航**: [文档中心](../README.md) | [API 参考](overview.md) | [TrajProxy API](proxy.md)

TrajProxy 的聊天补全接口与 OpenAI API 对齐，支持两种运行模式：

- **Direct 模式**（直接转发）：请求原样转发到推理服务的 `/chat/completions` 端点
- **TITO 模式**（Token-in-Token-out）：经过 Message→Text→Token 转换后发送到 `/completions` 端点

本文档定义两种模式下各参数的支持情况。

---

## 输入参数

### 支持状态总览

| 参数 | 类型 | 必填 | 默认值 | Direct | TITO | 说明 |
|------|------|:----:|:------:|:------:|:----:|------|
| [model](#model) | string | 是 | - | ✅ | ✅ | 模型名称 |
| [messages](#messages) | array | 是 | - | ✅ | ✅ | 对话历史 |
| [max_tokens](#max_tokens) | integer | 否 | - | ✅ | ✅ | 最大生成 token 数 |
| [temperature](#temperature) | float | 否 | - | ✅ | ✅ | 采样温度 |
| [top_p](#top_p) | float | 否 | - | ✅ | ✅ | 核采样概率阈值 |
| [seed](#seed) | integer | 否 | - | ✅ | ✅ | 随机数种子 |
| [presence_penalty](#presence_penalty) | float | 否 | - | ✅ | ✅ | 重复度控制 |
| [stop](#stop) | string/array | 否 | - | ✅ | ✅ | 停止生成条件 |
| [tools](#tools) | array | 否 | - | ✅ | ✅ | 工具库定义 |
| [stream](#stream) | boolean | 否 | false | ✅ | ✅ | 是否流式输出 |
| [stream_options](#stream_options) | object | 否 | - | ✅ | ✅ | 流式输出配置 |
| [n](#n) | integer | 否 | 1 | ❌ | ❌ | 生成响应个数（**不支持**） |
| [enable_search](#enable_search) | boolean | 否 | false | ❌ | ❌ | 启用互联网搜索（**不支持**） |

---

### model

| 属性 | 值 |
|------|---|
| 类型 | string |
| 必填 | 是 |
| Direct | ✅ 支持 |
| TITO | ✅ 支持 |

指定目标模型。支持 `model_name` 或 `model_name,run_id` 两种格式：

```json
{"model": "qwen3-235b-a22b"}
{"model": "qwen3-235b-a22b,run_001"}
```

**说明**：
- Proxy 使用 `model` 参数进行模型路由，实际发送到推理服务的模型名称以注册时配置为准
- `run_id` 提取优先级：路径参数 > `x-run-id` header > `model` 逗号后部分
- 模型必须已注册，否则返回 404

---

### messages

| 属性 | 值 |
|------|---|
| 类型 | array |
| 必填 | 是 |
| Direct | ✅ 支持 |
| TITO | ✅ 支持 |

对话历史，每个元素格式为 `{"role": "角色", "content": "内容"}`。

```json
{
  "messages": [
    {"role": "system", "content": "你是一个有帮助的助手"},
    {"role": "user", "content": "你好"},
    {"role": "assistant", "content": "你好！有什么可以帮助你的？"},
    {"role": "user", "content": "介绍一下自己"}
  ]
}
```

**说明**：
- 支持 `system`、`user`、`assistant`、`tool` 四种角色
- `system` 仅支持在 `messages[0]` 中出现
- `user` 和 `assistant` 需要交替出现
- 最后一个元素的 `role` 必须为 `user`
- Direct 模式下原样传输到推理服务
- TITO 模式下通过 Chat Template 转换为模型特定的 prompt 格式

---

### max_tokens

| 属性 | 值 |
|------|---|
| 类型 | integer |
| 必填 | 否 |
| Direct | ✅ 支持 |
| TITO | ✅ 支持 |

指定模型可生成的最大 token 个数。

```json
{"max_tokens": 1024}
```

**说明**：
- 不同模型有不同的输出上限，具体参见模型文档
- 同时支持 `max_completion_tokens` 参数（自动映射为 `max_tokens`，映射值优先）
- 值为 `null` 时不发送该参数

---

### temperature

| 属性 | 值 |
|------|---|
| 类型 | float |
| 必填 | 否 |
| Direct | ✅ 支持 |
| TITO | ✅ 支持 |

控制模型回复的随机性和多样性。

```json
{"temperature": 0.7}
```

**说明**：
- 取值范围：[0, 2)，不建议取值为 0
- 值较高时输出更多样化，值较低时输出更确定
- 原样透传到推理服务，Proxy 不做值校验

---

### top_p

| 属性 | 值 |
|------|---|
| 类型 | float |
| 必填 | 否 |
| Direct | ✅ 支持 |
| TITO | ✅ 支持 |

核采样方法的概率阈值。

```json
{"top_p": 0.9}
```

**说明**：
- 取值范围：(0, 1.0)
- 仅保留概率加起来大于等于该阈值的最可能 token 集合
- 原样透传到推理服务

---

### seed

| 属性 | 值 |
|------|---|
| 类型 | integer |
| 必填 | 否 |
| Direct | ✅ 支持 |
| TITO | ✅ 支持 |

随机数种子，用于控制生成内容的随机性。

```json
{"seed": 42}
```

**说明**：
- 支持无符号 64 位整数
- 相同 seed + 相同输入理论上产出相同结果
- `seed=0` 正常生效（不会被过滤）

---

### presence_penalty

| 属性 | 值 |
|------|---|
| 类型 | float |
| 必填 | 否 |
| Direct | ✅ 支持 |
| TITO | ✅ 支持 |

控制生成时整个序列中的重复度。

```json
{"presence_penalty": 1.0}
```

**说明**：
- 取值范围：[-2.0, 2.0]
- 提高 `presence_penalty` 可以降低生成内容的重复度
- 原样透传到推理服务

---

### stop

| 属性 | 值 |
|------|---|
| 类型 | string 或 array |
| 必填 | 否 |
| Direct | ✅ 支持 |
| TITO | ✅ 支持 |

在模型生成内容即将包含指定字符串或 token_id 时自动停止。

```json
{"stop": "结束"}
{"stop": ["你好", "再见"]}
{"stop": [108386, 104307]}
{"stop": [[108386, 103924], [35946, 101243]]}
```

**说明**：
- 支持 string 类型：生成指定字符串时停止
- 支持 array 类型：元素可为 token_id（整数）、字符串、或 token_id 数组
- array 中不可混合 token_id 和字符串
- Proxy 不做格式校验，原样透传

---

### tools

| 属性 | 值 |
|------|---|
| 类型 | array |
| 必填 | 否 |
| Direct | ✅ 支持 |
| TITO | ✅ 支持 |

定义可供模型调用的工具库（function calling）。

```json
{
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "获取指定城市的天气",
        "parameters": {
          "type": "object",
          "properties": {
            "city": {
              "type": "string",
              "description": "城市名称"
            }
          },
          "required": ["city"]
        }
      }
    }
  ]
}
```

**说明**：
- 每个 tool 包含 `type`（当前仅支持 `"function"`）和 `function` 定义
- `function` 包含 `name`（最大 64 字符）、`description`、`parameters`（JSON Schema）
- 模型在一次 function call 流程中选择其中一个工具
- Direct 模式下直接透传到推理服务
- TITO 模式下通过 Chat Template 嵌入 prompt，响应阶段通过 Parser 解析还原 tool_calls
- 支持 `tool_choice` 参数配合使用（`"auto"` / `"required"` / `{"type": "function", "function": {"name": "xxx"}}`）

---

### stream

| 属性 | 值 |
|------|---|
| 类型 | boolean |
| 必填 | 否 |
| 默认值 | false |
| Direct | ✅ 支持 |
| TITO | ✅ 支持 |

控制是否使用流式输出。

```json
{"stream": true}
```

**说明**：
- `false`：非流式，返回完整响应
- `true`：流式输出，以 SSE（Server-Sent Events）格式逐个返回增量内容
- 流式响应以 `data: [DONE]` 标记结束

---

### stream_options

| 属性 | 值 |
|------|---|
| 类型 | object |
| 必填 | 否 |
| Direct | ✅ 支持 |
| TITO | ✅ 支持 |

流式输出配置。

```json
{"stream_options": {"include_usage": true}}
```

**说明**：
- 仅在 `stream=true` 时生效
- `include_usage: true` 时，流式末尾会返回一个独立的 usage chunk（包含 token 统计信息）
- TITO 模式下无论是否设置，流式结尾均会追加 usage chunk

---

### n ❌ 不支持

| 属性 | 值 |
|------|---|
| 类型 | integer |
| 必填 | 否 |
| Direct | ❌ **不支持** |
| TITO | ❌ **不支持** |

生成响应的个数。

> **不支持原因**：Proxy 的轨迹存储和响应构建架构基于单 choice 设计。当 `n > 1` 时：
> - Direct 模式下虽然请求被透传，但内部轨迹记录仅存储 `choices[0]` 的数据
> - TITO 模式下固定返回 1 条响应，后端多生成的 response 被静默丢弃
>
> 如需多个候选响应，请发起多次请求。

---

### enable_search ❌ 不支持

| 属性 | 值 |
|------|---|
| 类型 | boolean |
| 必填 | 否 |
| Direct | ❌ **不支持** |
| TITO | ❌ **不支持** |

启用互联网搜索功能。

> **不支持原因**：`enable_search` 是特定推理服务（如 DashScope）的扩展参数，不属于 OpenAI 标准 API。Proxy 不对此参数做特殊处理，是否生效取决于底层推理服务的实际能力。作为通用代理层，Proxy 不承诺支持此类非标准扩展参数。

---

## 返回参数

### 非流式响应格式

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1677652288,
  "model": "qwen3-235b-a22b",
  "system_fingerprint": "",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "回复内容",
        "reasoning": null,
        "reasoning_content": null,
        "tool_calls": null
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

### 返回参数说明

| 参数 | 类型 | Direct | TITO | 说明 |
|------|------|:------:|:----:|------|
| **id** | string | ✅ | ✅ | 调用标识。Direct 保留推理服务原始值；TITO 格式为 `chatcmpl-{request_id}` |
| **object** | string | ✅ | ✅ | 固定值 `"chat.completion"` |
| **model** | string | ✅ | ✅ | 本次调用的模型名称 |
| **system_fingerprint** | string | ✅ | ⚠️ | 模型运行时配置版本。Direct 保留推理服务原始值；TITO 始终为 `null` |
| **created** | integer | ✅ | ✅ | 时间戳（秒）。Direct 使用推理服务值；TITO 使用 Proxy 接收时间 |
| **choices** | array | ✅ | ✅ | 生成内容详情数组 |
| **choices[i].index** | integer | ✅ | ✅ | 结果序列编号，固定为 0 |
| **choices[i].message** | object | ✅ | ✅ | 模型输出消息 |
| **choices[i].message.role** | string | ✅ | ✅ | 固定为 `"assistant"` |
| **choices[i].message.content** | string | ✅ | ✅ | 模型生成的文本内容 |
| **choices[i].message.reasoning** | string | ✅ | ✅ | 推理模型的思考过程（如有） |
| **choices[i].message.reasoning_content** | string | ✅ | ✅ | 同 `reasoning`，双字段镜像 |
| **choices[i].message.tool_calls** | array | ✅ | ✅ | 工具调用列表（如有） |
| **choices[i].finish_reason** | string | ✅ | ✅ | 结束原因：`null`（生成中）/ `"stop"`（正常结束）/ `"length"`（长度限制）/ `"tool_calls"`（工具调用） |
| **usage** | object | ✅ | ✅ | Token 消耗统计 |
| **usage.prompt_tokens** | integer | ✅ | ✅ | 输入 token 数。Direct 保留推理服务值；TITO 自行计算 `len(token_ids)` |
| **usage.completion_tokens** | integer | ✅ | ⚠️ | 输出 token 数。Direct 保留推理服务值；TITO 非流式优先后端值降级自算，流式始终自算 `len(response_ids)` |
| **usage.total_tokens** | integer | ✅ | ✅ | `prompt_tokens + completion_tokens` 的总和 |

### 流式响应格式

每个 SSE 事件的格式：

```
data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1677652288,"model":"qwen3-235b-a22b","choices":[{"index":0,"delta":{"role":"assistant","content":"你"},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1677652288,"model":"qwen3-235b-a22b","choices":[{"index":0,"delta":{"content":"好"},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1677652288,"model":"qwen3-235b-a22b","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

**流式响应说明**：
- 第一个 chunk 的 `delta` 包含 `role: "assistant"`
- 后续 chunk 的 `delta` 包含增量 `content`
- 最后一个 chunk 包含 `finish_reason`
- 设置 `stream_options.include_usage = true` 时，流末尾会追加一个包含 `usage` 的独立 chunk
- 包含 `reasoning` 的推理模型，`delta` 中会额外包含 `reasoning` 字段

---

## Proxy 行为说明

### 参数透传机制

Proxy 采用**黑名单透传**策略处理请求参数：

1. `model`、`messages`、`stream` 由 Proxy 单独处理，不进入透传通道
2. 其余所有参数一律透传到推理服务（`null` 值除外）
3. Proxy 强制注入 `logprobs` 和 `return_token_ids` 用于轨迹记录，但不在客户端响应中返回

### 两种模式的差异

| 特性 | Direct 模式 | TITO 模式 |
|------|-----------|-----------|
| 推理端点 | `/chat/completions` | `/completions` |
| 请求格式 | OpenAI Messages 原样透传 | Messages→Text→Token IDs |
| tools 处理 | 直接透传 | Chat Template 嵌入 + Parser 还原 |
| usage 来源 | 推理服务返回 | 自行计算（prompt: token_count, completion: response_ids 长度） |
| system_fingerprint | 保留推理服务值 | 始终为 `null` |
| 前缀缓存 | 不支持 | 支持（PrefixMatchCache） |

### 扩展参数

除上述定义的参数外，任何 OpenAI 标准参数或推理服务扩展参数（如 `frequency_penalty`、`logit_bias` 等）均会被自动透传到推理服务，无需 Proxy 代码变更即可生效。

---

↑ [返回顶部](#openai-兼容接口规范) | [API 参考](overview.md)
