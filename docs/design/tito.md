# TITO (Token-In-Token-Out) 前缀匹配方案

> **导航**: [文档中心](../README.md) | [架构文档](architecture.md) | [Jinja 模板处理](jinja-process.md)

## 概述

TITO (Token-In-Token-Out) 模式是一种优化推理性能的架构，通过将输入输出从文本转换为 Token IDs，实现以下优化：

- **前缀匹配缓存**：复用多轮对话中相同的 Prompt 部分，减少重复编码
- **增量推理**：只对新增内容进行推理，提高吞吐量

本系统采用**多轮一致性 Jinja 模板方案**来确保前缀匹配缓存正常工作。

---

## 核心问题

### 1. 多轮对话的一致性问题

在 RL（强化学习）场景的前缀匹配缓存中，多轮对话存在 tokenizer prompt template 不一致的问题。

**问题示例**：

**第一轮对话**：
```
<|im_start|>user\n你好<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n你好！有什么我可以帮你的吗？😊<|im_end|>
```

**第二轮对话（问题所在）**：
```
<|im_start|>user\n你好<|im_end|>\n<|im_start|>assistant\n你好！有什么我可以帮你的吗？😊<|im_end|>\n<|im_start|>user\n今天天气怎么样<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n
...
```

问题：第一轮缓存的 prompt_text + response_text 与第二轮 apply_chat_template 生成的文本不一致。

### 2. 前缀匹配缓存失效

**问题根源**：
- 原始模板包含位置相关的条件逻辑（如 loop.index0 > ns.last_query_index）
- 第一轮对话和第二轮对话重新渲染时，相同 messages 的输出文本不一致
- 前缀匹配缓存依赖文本前缀匹配，不一致会导致缓存完全失效

---

## 当前实现方案：多轮一致性 Jinja 模板

### 方案概述

通过使用多轮一致的 Jinja 模板 chat_template_tito.jinja，确保相同 messages 在不同轮次渲染时输出完全一致。

### 架构设计

```
TITO 模式处理流程
──────────────────────────────────────────────────────────────────────────────

OpenAI Request
        ↓
[MessageConverter]
        │
        ├── TITO 模式: 使用 chat_template_tito.jinja
        │   - 保证多轮一致性
        │   - 前缀匹配缓存可正常工作
        │
        └── 普通模式: 使用默认 chat_template.jinja
        ↓
     prompt_text
        ↓
[TokenConverter + PrefixMatchCache]
        │
        └── 文本前缀匹配成功 (因为模板一致性)
        ↓
     token_ids (部分缓存命中)
        ↓
     推理服务
```

### 核心组件

#### 1. MessageConverter

**文件**: traj_proxy/proxy_core/converters/message_converter.py

**职责**：将 OpenAI Messages 转换为模型特定的 PromptText

**关键特性**：
- 支持自定义 Jinja 模板（TITO 模式）
- TITO 模式下使用 chat_template_tito.jinja 确保多轮一致性
- 支持传递 tools、documents、tool_choice 等参数

**接口说明**：
- 构造函数接收 tito_template_path 参数
- convert() 方法根据模板选择进行渲染
- _render_with_tito_template() 使用自定义模板渲染

#### 2. TokenConverter

**文件**: traj_proxy/proxy_core/converters/token_converter.py

**职责**：负责 Text <-> TokenIds 转换

**关键特性**：
- 支持 cache_strategy 进行编码优化
- encode()：文本转 token IDs（可使用缓存）
- decode()：token IDs 转文本
- decode_streaming()：流式解码，处理 UTF-8 边界问题

#### 3. PrefixMatchCache

**文件**: traj_proxy/proxy_core/cache/prefix_cache.py

**职责**：通过匹配历史对话的前缀来优化 Token 编码

**优化策略**：
1. 根据 session_id 查询数据库获取该会话的所有历史请求
2. 用当前完整对话文本（请求+响应）与历史完整对话文本进行前缀匹配
3. 匹配部分使用缓存的完整对话 token_ids
4. 未匹配部分使用 tokenizer 编码
5. 拼接得到完整的 token_ids

#### 4. chat_template_tito.jinja

**文件**: models/Qwen/Qwen3.5-2B/chat_template_tito.jinja

**关键特性**：
- 删除位置相关的条件逻辑（如 loop.index0 > ns.last_query_index）
- 所有 assistant 消息使用统一的思考格式
- 确保多轮对话渲染时输出完全一致

**核心差异**：
```jinja
{#- 多轮一致性模板：删除 last_query_index 计算，所有 assistant 都使用思考格式 #}
{%- for message in messages %}
    {%- elif message.role == "assistant" %}
        {#- 所有 assistant 都使用思考格式，不根据位置判断 #}
        {{- '<|im_start|>' + message.role + '\n' }}
        {{- ' \n' + reasoning_content + '\n \n\n' + content }}
```

### 处理流程

1. **Processor 初始化**
   - 查找 chat_template_tito.jinja 文件
   - 创建 MessageConverter 并传入模板路径
   - 创建 TokenConverter 并传入 PrefixMatchCache

2. **请求处理**
   - MessageConverter 将 messages 转换为 prompt_text
   - TokenConverter 使用 PrefixMatchCache 进行编码
   - 缓存命中的部分直接使用缓存的 token_ids
   - 未命中的部分使用 tokenizer 编码
   - 合并得到完整的 token_ids

---

## 方案对比

以下介绍三种不同的 TITO 前缀匹配方案，它们各有优缺点，适用于不同的场景。

### 方案一：多轮一致性 Jinja 模板（当前实现）

**核心思路**：通过修改 Jinja 模板实现多轮一致性，确保相同 messages 在不同轮次渲染时输出完全一致。

**架构特点**：
- 使用 chat_template_tito.jinja 替代默认模板
- 基于文本的前缀匹配缓存
- 保持现有架构，改动最小

**优点**：
- 实现简单，改动最小
- 只需要修改模板文件和少量代码
- 与现有架构兼容性好
- 前缀匹配缓存基于文本，易于理解和调试

**缺点**：
- 仍然基于文本匹配，理论上存在歧义风险
- 不同模型需要定制不同的模板
- 增量 tokenization 依赖 tokenizer 行为

**适用场景**：
- 模型相对固定，不需要频繁更换
- 追求快速实现和部署
- 前缀匹配缓存效率要求不是极端严格

### 方案二：对称 Converter 架构（设计文档）

**核心思路**：基于 Messages 结构的前缀匹配，借鉴 vLLM 架构设计。

**架构特点**：
- 对称 Converter 架构：FormatConverter + TokenConverter
- 基于 Messages 结构匹配，避免文本不一致问题
- 独立的 Parser 组件（ToolParser、ReasoningParser）

**优点**：
- 基于结构化数据匹配，更可靠
- 不受 Jinja 模板变化影响
- Parser 组件独立，易于扩展
- 更清晰的职责划分

**缺点**：
- 实现复杂度较高
- 需要重构现有架构
- 存储需要保存完整 messages 结构
- 开发和测试成本较高

**适用场景**：
- 需要支持多种模型
- 对缓存效率要求极高
- 架构需要长期演进和维护

### 方案三：Miles TITOTokenizer（对照组）

**核心思路**：使用 TITOTokenizer 进行增量 tokenization，基于 messages 匹配和 dummy-message diff。

**项目来源**：/Users/liujiang/Workspace/Code/miles

**架构特点**：
- LinearTrajectory 跟踪完整消息历史和 token IDs
- 基于 messages 结构匹配，支持回滚
- 增量 tokenization 使用 dummy-message diff
- 模型特定的 boundary token 处理（Qwen3、GLM47）

**核心组件**：
- **TITOTokenizer**：增量 tokenization 和 prefix 合并
- **LinearTrajectory**：跟踪消息历史和 token IDs 检查点
- **SessionRegistry**：session ID 到 trajectory 的映射

**优点**：
- 基于 messages 结构匹配，不受模板不一致影响
- 增量 tokenization 使用 dummy-message diff，计算高效
- 支持模型特定的 boundary token 处理
- 支持单步回滚，处理 agent 重试场景
- 成熟的实现，经过生产验证

**缺点**：
- 实现复杂度较高
- 需要维护完整的消息历史和 token IDs 检查点
- 需要为每种模型定制 TITOTokenizer 子类
- 与现有架构差异较大

**增量 Tokenization 示例**：
```
1. tokens_without = tokenize([dummy_user, dummy_assistant], add_generation_prompt=False)
2. tokens_with = tokenize([dummy_user, dummy_assistant] + appended, add_generation_prompt=True)
3. incremental_ids = tokens_with[len(tokens_without):]
```

**模型特定处理**：
- **Qwen3TITOTokenizer**：处理缺失的换行符 boundary（模型停在 <|im_end|>，模板需要 <|im_end|>\n）
- **GLM47TITOTokenizer**：处理歧义 boundary tokens（```` 和 ````既是停止标记又是开始标记）

**适用场景**：
- 需要高效的增量 tokenization
- 需要支持 agent 重试和回滚
- 需要支持多种模型
- 可以接受较高的实现复杂度

### 对比总结

| 维度 | 方案一（当前实现） | 方案二（设计文档） | 方案三（Miles） |
|------|-------------------|-------------------|------------------|
| 匹配方式 | 文本前缀匹配 | Messages 结构匹配 | Messages 结构匹配 |
| 实现复杂度 | 低 | 高 | 高 |
| 改动范围 | 模板 + 少量代码 | 架构重构 | 架构重构 |
| 可靠性 | 依赖模板一致性 | 结构化匹配 | 结构化匹配 + boundary 处理 |
| 模型适应性 | 需要定制模板 | 架构无关 | 需要定制 TITOTokenizer |
| 部署成本 | 低 | 高 | 高 |
| 维护成本 | 中 | 低 | 中 |
| Parser 集成 | 无独立组件 | 独立 Parser 组件 | 不适用 |
| 增量 Tokenization | 依赖 tokenizer | 需要额外实现 | dummy-message diff |
| 回滚支持 | 不支持 | 不支持 | 支持单步回滚 |
| 生产验证 | 是 | 否 | 是 |

---

## 文件位置

```
traj_proxy/
├── proxy_core/
│   ├── converters/
│   │   ├── message_converter.py      # MessageConverter（支持 TITO 模板）
│   │   └── token_converter.py         # TokenConverter
│   └── cache/
│       └── prefix_cache.py            # PrefixMatchCache
models/
└── Qwen/Qwen3.5-2B/
    └── chat_template_tito.jinja        # 多轮一致性模板
```

**方案三参考项目**：
```
miles/
├── miles/utils/chat_template_utils/
│   ├── tito_tokenizer.py            # TITOTokenizer 及模型特定实现
│   └── token_seq_comparator.py      # Token 序列比较器
└── miles/rollout/session/
    └── linear_trajectory.py          # LinearTrajectory 和 SessionRegistry
```

---

## 兼容性考虑

- 如果 chat_template_tito.jinja 不存在，回退到默认模板（并输出警告）
- TITO 模板与默认模板的首轮输出必须一致（通过验证脚本保证）
- 不影响非 TITO 模式的正常使用

---

## 验证方案

1. **模板验证**：运行验证脚本确保模板一致性
2. **前缀匹配验证**：在 E2E 测试中验证多轮对话的缓存命中率
3. **输出一致性验证**：对比 TITO 模式和普通模式的单轮输出

---

## 参考资料

- **方案三实现**：[Miles 项目](https://github.com/miles-project/miles)
- **vLLM 架构**：[vLLM GitHub](https://github.com/vllm-project/vllm)
