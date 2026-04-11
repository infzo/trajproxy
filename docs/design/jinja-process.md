# Jinja 模板多轮一致性转换工作流

> **导航**: [文档中心](../README.md) | [模板一致性](template_consistency.md) | [TITO 方案](tito-v1.md)

## 1. 多轮一致性定义

### 1.1 核心概念

在 RL（强化学习）场景的前缀匹配缓存中，**多轮一致性**是指：

- 第一轮 `messages + response` 拼接为字符串 **a**
- 第二轮 `messages + response` 拼接为字符串 **b**
- **a 必须是 b 的子字符串**

数学表示：

```
output_round1 ∈ output_round2 ∈ output_round3 ...
```

### 1.2 应用场景

多轮一致性对于以下场景至关重要：

- **前缀匹配缓存**：RL 训练时，多轮对话的 prompt 可以复用前一轮的 token 序列
- **增量 tokenization**：只需计算新增轮次的 token，避免重复编码
- **内存优化**：减少缓存存储和计算开销

### 1.3 问题根源

原始 Jinja 模板通常包含**位置相关的条件逻辑**，例如：

```jinja2
{#- 原始模板：根据消息位置决定输出格式 #}
{%- if loop.index0 < ns.last_query_index %}
    {{- '#@|im_start|@#assistant\n#@think@#\n' + reasoning_content + '\n#@/think@#\n\n' + content }}
{%- else %}
    {{- '#@|im_start|@#assistant\n' + content }}
{%- endif %}
```

这种逻辑导致：
- 第一轮对话：assistant 不使用思考格式
- 第二轮对话：第一轮的 assistant 突然需要思考格式

结果：第二轮重新渲染时，第一轮的输出文本发生变化，破坏前缀匹配。

---

## 2. 一致性模板要求

### 2.1 核心要求

一致性模板必须满足：

1. **统一的输出格式**：所有 assistant 消息使用相同的渲染逻辑
2. **无位置依赖**：输出不依赖消息在序列中的位置
3. **首轮对话一致性约束**：转换后的模板在首轮对话（单轮对话）场景下，输出必须与原模板完全一致

### 2.2 首轮对话一致性约束

**这是关键约束**，确保转换不会偏离原模板的核心行为。

**验证方法**：
```python
from jinja2 import Environment, FileSystemLoader

def verify_first_round_consistency(original_template, consistency_template, messages_round1):
    """验证首轮对话一致性"""
    output_original = original_template.render(messages=messages_round1)
    output_consistency = consistency_template.render(messages=messages_round1)
    
    assert output_original == output_consistency, (
        f"首轮对话不一致:\n"
        f"原始输出:\n{output_original}\n\n"
        f"一致性输出:\n{output_consistency}"
    )
```

**示例**：
```python
messages_round1 = [
    {"role": "user", "content": "你好"},
    {"role": "assistant", "content": "你好！有什么可以帮助你的？"}
]
# 原始模板和一致性模板的输出必须完全相同
```

### 2.3 转换示例

**原始模板**（chat_template.jinja）：
```jinja2
{#- 存在位置相关条件 #}
{%- set ns = namespace(multi_step_tool=true, last_query_index=messages|length - 1) %}
{%- for message in messages[::-1] %}
    {#- 计算 last_query_index #}
{%- endfor %}

{%- if loop.index0 < ns.last_query_index %}
    {{- '#@|im_start|@#assistant\n#@think@#\n' + reasoning_content + ... }}
{%- else %}
    {{- '#@|im_start|@#assistant\n' + content }}
{%- endif %}
```

**一致性模板**（chat_template_tito.jinja）：
```jinja2
{#- 删除 last_query_index 计算，统一输出格式 #}
{%- for message in messages %}
    {#- 所有 assistant 使用相同格式 #}
    {{- '#@|im_start|@#assistant\n#@think@#\n' + reasoning_content + '\n#@/think@#\n\n' + content }}
{%- endfor %}
```

---

## 3. 三步转换工作流

### 3.1 工作流概述

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Step 1        │     │   Step 2        │     │   Step 3        │
│   敏感字符替换   │ ──► │   大模型分析     │ ──► │   敏感字符还原   │
│   sed 命令      │     │   Jinja → TITO  │     │   sed 命令      │
└─────────────────┘     └─────────────────┘     └─────────────────┘
      │                        │                       │
      ▼                        ▼                       ▼
xxx.jinja              xxx-safe.jinja            chat_template_tito.jinja
(原始)                 (安全字符)               (TITO 一致性模板)
```

### 3.2 Step 1: 敏感字符替换

**目的**：避免大模型误解 Jinja 语法中的 `<` 和 `>` 为 HTML 标签或比较运算符。

**替换规则**：
| 原字符 | 替换为 | Unicode | 说明 |
|--------|--------|---------|------|
| `<` | `⟨` | U+27E8 | 数学左尖括号 |
| `>` | `⟩` | U+27E9 | 数学右尖括号 |

**sed 命令**：
```bash
# 替换敏感字符
sed -e 's/</⟨/g' -e 's/>/⟩/g' chat_template.jinja > chat_template_safe.jinja
```

**效果对比**：

原始：
```jinja2
{%- if content is mapping %}
    {{- content | tojson }}
{%- endif %}
```

替换后：
```jinja2
{%- if content is mapping %}
    {{- content | tojson }}
{%- endif %}
```

### 3.3 Step 2: 大模型分析转换

**输入**：`chat_template_safe.jinja`（敏感字符已替换）

**输出**：`chat_template_safe-tito.jinja`（具有多轮一致性）

**分析要点**：

1. **识别位置相关条件**
   - 搜索 `loop.index0`、`loop.first`、`loop.last` 等循环变量
   - 搜索 `ns.last_query_index`、`messages|length` 等位置判断

2. **分析条件分支影响**
   - 不同分支输出的差异
   - 哪些差异会破坏前缀匹配

3. **设计统一格式**
   - 选择最通用的输出格式
   - 确保首轮对话一致性约束

4. **验证转换结果**
   - 多轮一致性测试
   - 首轮对话一致性测试

**Prompt 模板**：
```
请分析以下 Jinja 模板，将其转换为具有多轮一致性的模板。

要求：
1. 所有 assistant 消息使用统一的输出格式
2. 删除或修改位置相关的条件逻辑
3. 首轮对话输出必须与原模板一致

模板内容：
[chat_template_safe.jinja 内容]
```

### 3.4 Step 3: 敏感字符还原

**目的**：将占位符还原为原始字符。

**sed 命令**：
```bash
# 还原敏感字符
sed -e 's/⟨/</g' -e 's/⟩/>/g' chat_template_safe-tito.jinja > chat_template_tito.jinja
```

### 3.5 完整脚本示例

```bash
#!/bin/bash
# jinja_to_tito.sh - Jinja 模板一致性转换脚本

INPUT_FILE=$1
OUTPUT_FILE=${2:-"${INPUT_FILE%.jinja}_tito.jinja"}

# 临时文件
SAFE_FILE=$(mktemp)
TITO_SAFE_FILE=$(mktemp)

# Step 1: 敏感字符替换
sed -e 's/</⟨/g' -e 's/>/⟩/g' "$INPUT_FILE" > "$SAFE_FILE"

# Step 2: 大模型分析转换（这里需要调用 LLM API）
# 示例：调用 Claude API
# claude_api --input "$SAFE_FILE" --output "$TITO_SAFE_FILE" --prompt "转换为一致性模板"
echo "请将 $SAFE_FILE 输入大模型进行分析转换，输出到 $TITO_SAFE_FILE"
read -p "转换完成后按回车继续..."

# Step 3: 敏感字符还原
sed -e 's/⟨/</g' -e 's/⟩/>/g' "$TITO_SAFE_FILE" > "$OUTPUT_FILE"

# 清理临时文件
rm -f "$SAFE_FILE" "$TITO_SAFE_FILE"

echo "转换完成: $OUTPUT_FILE"
```

---

## 4. Skill 集成

### 4.1 Skill 定义

将此工作流封装为可复用的 skill：

```yaml
# .claude/skills/jinja-to-tito-v1.md
name: jinja-to-tito
description: 将 Jinja 模板转换为具有多轮一致性的 TITO 模板
trigger: 当用户需要转换 Jinja 模板以支持 RL 前缀匹配缓存时
```

### 4.2 输入规范

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| input_file | string | 是 | 原始 Jinja 模板路径 |
| output_file | string | 否 | 输出文件路径，默认为 `{input}_tito.jinja` |

### 4.3 输出规范

| 输出 | 说明 |
|------|------|
| 一致性模板文件 | 具有多轮一致性的 Jinja 模板 |
| 转换报告 | 包含修改点、验证结果 |

### 4.4 验证检查清单

转换完成后，必须执行以下验证：

- [ ] 多轮一致性测试：使用 `tests/test_template_consistency.py`
- [ ] 首轮对话一致性测试：对比原始模板输出
- [ ] Tool calls 场景测试
- [ ] Reasoning content 场景测试

---

## 5. 参考

- 测试定义：`tests/test_template_consistency.py`
- 校验脚本：`scripts/tools/verify_jinja_consistency.py`
- 背景说明：`docs/tito.md`
- TITO 集成：`docs/tito-v2.md`
- 模板示例：
  - 原始：`models/Qwen/Qwen3.5-2B/chat_template.jinja`
  - TITO 一致性：`models/Qwen/Qwen3.5-2B/chat_template_tito.jinja`
