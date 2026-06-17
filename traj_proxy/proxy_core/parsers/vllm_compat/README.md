# vLLM Parser 适配层

零侵入式 vLLM Parser 集成方案，支持直接复制 vLLM 的 parser 代码而无需修改。

## 特性

- ✅ **零修改复制** - vLLM parser 代码原封不动
- ✅ **轻量级** - 无 GPU 依赖，无需安装 vLLM
- ✅ **自动注册** - 新 parser 只需复制 + 添加配置
- ✅ **完全兼容** - 与 vLLM 0.16.0 接口完全兼容

## 目录结构

```
vllm_compat/
├── README.md                   # 本文件
├── __init__.py                 # 自动发现和注册机制
├── vllm/                       # 模拟 vllm 包结构
│   ├── entrypoints/
│   │   └── openai/
│   │       ├── engine/         # DeltaMessage, ToolCall 等协议类
│   │       ├── chat_completion/# ChatCompletionRequest 等
│   │       └── responses/      # ResponsesRequest 等
│   ├── logger/                 # init_logger
│   ├── tokenizers/             # TokenizerLike 类型
│   ├── tool_parsers/           # ToolParser 基类 + 注册器
│   ├── reasoning/              # ReasoningParser 基类 + 注册器
│   └── utils/                  # 工具函数
├── tool_parsers/               # Tool Parser 实现（从 vLLM 复制）
│   ├── __init__.py             # 注册配置
│   └── qwen3coder_tool_parser.py
└── reasoning_parsers/          # Reasoning Parser 实现（从 vLLM 复制）
    ├── __init__.py             # 注册配置
    └── qwen3_reasoning_parser.py
```

## 使用方法

### 基本使用

```python
import sys
sys.path.insert(0, 'path/to/vllm_compat')

from vllm.tool_parsers.abstract_tool_parser import ToolParserManager
from vllm.reasoning.abs_reasoning_parsers import ReasoningParserManager

# 列出可用的 parsers
print(ToolParserManager.list_registered())
# ['qwen3_coder']

print(ReasoningParserManager.list_registered())
# ['qwen3']

# 获取 parser 类
tool_parser_cls = ToolParserManager.get_tool_parser("qwen3_coder")
reasoning_parser_cls = ReasoningParserManager.get_reasoning_parser("qwen3")

# 创建 parser 实例（需要 tokenizer）
from transformers import AutoTokenizer
tokenizer = AutoTokenizer.from_pretrained("Qwen/Qwen3-Coder-30B-A3B-Instruct")

tool_parser = tool_parser_cls(tokenizer=tokenizer)
reasoning_parser = reasoning_parser_cls(tokenizer=tokenizer)
```

### Tool Parser 示例

```python
from vllm.entrypoints.openai.chat_completion.protocol import ChatCompletionRequest

# 准备请求
request = ChatCompletionRequest(
    messages=[{"role": "user", "content": "北京天气怎么样？"}],
    tools=[...],
)

# 非流式解析
model_output = "toral<function=get_weather>\n<parameter=city>北京</parameter>\n</function>最终答案"
result = tool_parser.extract_tool_calls(model_output, request)

print(result.tools_called)  # True
print(result.tool_calls)    # [ToolCall(function=FunctionCall(name='get_weather', arguments='{"city": "北京"}'))]
print(result.content)       # None

# 流式解析（逐块处理）
for chunk in stream:
    delta_msg = tool_parser.extract_tool_calls_streaming(
        previous_text=previous_text,
        current_text=current_text,
        delta_text=delta,
        previous_token_ids=prev_ids,
        current_token_ids=curr_ids,
        delta_token_ids=delta_ids,
        request=request,
    )
    if delta_msg:
        yield delta_msg
```

### Reasoning Parser 示例

```python
# 非流式解析
model_output = "思索用户询问北京天气。需要调用 API。最终答案好的，我来查询北京的天气。"
reasoning, content = reasoning_parser.extract_reasoning(model_output, None)

print(reasoning)  # "用户询问北京天气。需要调用 API。"
print(content)    # "好的，我来查询北京的天气。"

# 流式解析
delta_msg = reasoning_parser.extract_reasoning_streaming(
    previous_text=previous_text,
    current_text=current_text,
    delta_text=delta,
    previous_token_ids=prev_ids,
    current_token_ids=curr_ids,
    delta_token_ids=delta_ids,
)
if delta_msg:
    print(delta_msg.reasoning)  # 推理内容增量
    print(delta_msg.content)    # 最终回复增量
```

## 添加新 Parser

### 步骤 1: 复制 Parser 文件

```bash
# Tool Parser
cp /path/to/vllm/vllm/tool_parsers/deepseekv3_tool_parser.py \
   tool_parsers/

# Reasoning Parser
cp /path/to/vllm/vllm/reasoning/deepseek_r1_reasoning_parser.py \
   reasoning_parsers/
```

### 步骤 2: 添加注册配置

编辑 `tool_parsers/__init__.py`:

```python
_TOOL_PARSERS_TO_REGISTER = {
    "qwen3_coder": (
        "tool_parsers.qwen3coder_tool_parser",
        "Qwen3CoderToolParser",
    ),
    # 添加新 parser
    "deepseek_v3": (
        "tool_parsers.deepseekv3_tool_parser",
        "DeepSeekV3ToolParser",
    ),
}
```

### 步骤 3: 完成！

无需修改 parser 文件本身。重启服务后，新 parser 自动可用。

## 依赖

```txt
# requirements.txt
regex>=2024.0.0      # vLLM parser 使用 regex 库
transformers>=4.45.0  # Tokenizer
```

## 测试

```bash
# 运行集成测试
python tests/test_parser_integration.py

# 输出示例：
# ============================================================
# vLLM Parser Compatibility Layer Test Suite
# ============================================================
# ✓ engine.protocol imports OK
# ✓ logger imports OK
# ✓ tokenizers imports OK
# ✓ tool_parsers.abstract_tool_parser imports OK
# ✓ reasoning.abs_reasoning_parsers imports OK
# ✓ reasoning.basic_parsers imports OK
# Tool parsers registered: ['qwen3_coder']
# Reasoning parsers registered: ['qwen3']
# ✓ All parser tests passed!
# ============================================================
```

## MD5 一致性校验

### 校验范围（强制 MD5 一致）

仅 `tool_parsers/` 与 `reasoning_parsers/` 下从 vLLM 复制的 parser 文件：

| 本目录 | 对应上游 |
|--------|---------|
| `tool_parsers/*.py`（除 `__init__.py`） | `../vllm/vllm/tool_parsers/*.py` |
| `reasoning_parsers/*.py`（除 `__init__.py`） | `../vllm/vllm/reasoning/*.py` |

```bash
# 校验示例
md5 tool_parsers/qwen3coder_tool_parser.py
md5 /Users/liujiang/Workspace/Code/vllm/vllm/tool_parsers/qwen3coder_tool_parser.py
# 两者输出必须相同
```

### 适配层（豁免 MD5）

以下属于项目自有的 stub 化或注册代码，**不要求** MD5 一致：

- `vllm/` 子目录下所有文件（为减轻依赖而简化的协议类、工具函数、tokenizers stub）
- 所有 `__init__.py`（自动发现/注册配置）

### 禁止操作

禁止对校验范围内的 parser 文件做任何本地修改——代码格式化、变量重命名、
删除注释、调整导入顺序等任何会导致 MD5 变化的操作均视为违规。

## 实现原理

### 兼容层设计

1. **包结构模拟** - 在 `vllm_compat/vllm/` 目录下创建与 vLLM 相同的包结构
2. **sys.path 注入** - 将 `vllm_compat` 目录添加到 `sys.path`，使 `from vllm.xxx import yyy` 正常工作
3. **延迟注册** - 使用 `register_lazy_module` 避免立即导入 parser 文件

### 关键文件

| 文件 | 说明 |
|------|------|
| `vllm_compat/__init__.py` | 自动发现和注册机制 |
| `vllm/tool_parsers/abstract_tool_parser.py` | ToolParser 基类 + 注册器 |
| `vllm/reasoning/basic_parsers.py` | BaseThinkingReasoningParser 基类 |
| `tool_parsers/__init__.py` | Tool Parser 注册配置 |
| `reasoning_parsers/__init__.py` | Reasoning Parser 注册配置 |

## 常见问题

### Q: 为什么不直接安装 vLLM？

A: vLLM 依赖 PyTorch (~2GB) 和 CUDA 编译，对于只需要 parser 功能的场景来说过重。适配层方案无需 GPU 依赖，轻量级。

### Q: Parser 文件真的不需要修改吗？

A: 是的！因为：
1. 导入路径 `from vllm.xxx import yyy` 通过 `sys.path` 注入兼容
2. 类型定义通过适配层的协议类兼容
3. 注册通过装饰器自动完成

### Q: 如何更新到新版本 vLLM？

A: 只需重新复制 parser 文件到对应目录，无需其他修改。

## 已验证的 Parsers

- ✅ Qwen3CoderToolParser (qwen3_coder)
- ✅ Qwen3ReasoningParser (qwen3)

更多 parser 正在添加中...
