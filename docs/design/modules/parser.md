# Parser 行为逻辑文档

> **导航**: [文档中心](../../README.md) | [架构文档](../architecture.md) | [vLLM 对比](../../api/vs-vllm.md)

## 概述

TrajProxy 的 Parser 模块参考 vLLM 0.16.0 接口设计，提供统一的工具调用和推理内容解析能力。支持非流式和流式两种模式。

**核心设计：**
- 使用**适配器模式**封装 vLLM 兼容层 Parser
- 对外提供统一的 `Parser` 接口
- 支持 vLLM 原始 parser 无需修改即可使用
- **注意：项目不依赖 vLLM 包本身**，而是通过 `vllm_compat/` 兼容层（将 vllm_compat 目录注入 `sys.path`）提供 `from vllm.xxx import yyy` 的导入兼容，使得代码与 vLLM 接口兼容但无需安装 vLLM

---

## 一、架构设计

### 1.1 组件结构

```
┌─────────────────────────────────────────────────────────────────────┐
│                           Parser (统一接口)                          │
│                     (适配器模式，对外统一接口)                        │
│                                                                      │
│   ┌──────────────────────────┐    ┌──────────────────────────┐      │
│   │      ToolParser          │    │    ReasoningParser       │      │
│   │    (vLLM 原生接口)        │    │    (vLLM 原生接口)       │      │
│   │                          │    │                          │      │
│   │  - extract_tool_calls    │    │  - extract_reasoning     │      │
│   │  - extract_tool_calls_   │    │  - extract_reasoning_    │      │
│   │    streaming             │    │    streaming             │      │
│   └──────────────────────────┘    └──────────────────────────┘      │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.2 ParserManager 统一管理器

**文件：** `traj_proxy/proxy_core/parsers/parser_manager.py`

**职责：**
- 统一管理 Tool Parser 和 Reasoning Parser 的创建和获取
- 提供一站式接口创建 Parser 实例

**核心接口：**

```python
class ParserManager:
    @classmethod
    def create_parser(
        cls,
        tool_parser_name: Optional[str],
        reasoning_parser_name: Optional[str],
        tokenizer: Any,
    ) -> Parser:
        """创建 Parser 实例（统一接口）"""
        pass

    @classmethod
    def list_tool_parsers(cls) -> List[str]:
        """列出所有已注册的 Tool Parser 名称"""
        pass

    @classmethod
    def list_reasoning_parsers(cls) -> List[str]:
        """列出所有已注册的 Reasoning Parser 名称"""
        pass
```

---

## 二、Parser 统一接口

### 2.1 类定义

**文件：** `traj_proxy/proxy_core/parsers/parser_manager.py`

```python
class Parser:
    """Parser 统一接口（适配器模式）
    
    对外提供统一接口，对内适配 vLLM parser。
    支持 vLLM 原始 parser 无需修改即可使用。
    """

    def __init__(
        self,
        tool_parser: Optional[ToolParser],
        reasoning_parser: Optional[ReasoningParser],
    ):
        self._tool_parser = tool_parser
        self._reasoning_parser = reasoning_parser

    # 流式状态管理
    def reset_streaming_state(self):
        """重置流式解析状态"""
        pass

    def __enter__(self):
        """进入上下文管理器，自动重置流式状态"""
        pass

    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出上下文管理器"""
        pass
```

### 2.2 使用示例

```python
from traj_proxy.proxy_core.parsers import ParserManager

# 创建 Parser 实例
parser = ParserManager.create_parser(
    tool_parser_name="qwen3_coder",
    reasoning_parser_name="qwen3",
    tokenizer=tokenizer
)

# 使用上下文管理器自动管理流式状态
with parser:
    async for chunk in stream:
        delta = parser.extract_tool_calls_streaming(...)
```

---

## 三、核心数据结构

### 3.1 工具调用结构

```python
# 函数调用（符合 OpenAI 规范）
@dataclass
class FunctionCall:
    name: str = ""           # 函数名
    arguments: str = ""      # 参数（JSON 字符串）

# 工具调用（符合 OpenAI 规范）
@dataclass
class ToolCall:
    id: str = field(default_factory=make_tool_call_id)  # 唯一标识，格式: "chatcmpl-tool-xxx"，自动生成
    type: str = "function"           # 类型固定为 "function"
    function: Optional[FunctionCall] = None  # 嵌套的函数对象

# 流式增量函数调用
@dataclass
class DeltaFunctionCall:
    name: Optional[str] = None       # 可选的函数名增量
    arguments: Optional[str] = None  # 可选的参数增量

# 流式增量工具调用
@dataclass
class DeltaToolCall:
    id: Optional[str] = None         # 工具调用 ID
    type: Optional[str] = None       # 类型
    index: int = 0                   # 索引（用于流式合并）
    function: Optional[DeltaFunctionCall] = None
```

### 3.2 提取结果结构

```python
# 非流式工具调用提取结果
@dataclass
class ExtractedToolCallInformation:
    tools_called: bool                  # 是否有工具调用
    tool_calls: List[ToolCall]          # 工具调用列表
    content: Optional[str] = None       # 工具调用前的文本内容

# 流式增量消息
@dataclass
class DeltaMessage:
    role: Optional[str] = None           # 角色
    content: Optional[str] = None        # 内容增量
    reasoning: Optional[str] = None      # 推理内容增量
    tool_calls: List[DeltaToolCall] = field(default_factory=list)
```

---

## 四、Tool Parser

### 4.1 已注册的 Tool Parsers

| 名称 | 类名 | 格式特点 |
|------|------|----------|
| `qwen3_coder` | Qwen3CoderToolParser | <tool_call>/</tool_call> 边界标记 + `<function=>` XML |
| `qwen3_xml` | Qwen3XMLToolParser | <tool_call>/</tool_call> 边界标记 + `<function=>` XML（使用 StreamingXMLToolCallParser） |
| `hermes` | Hermes2ProToolParser | Hermes JSON 格式，使用 <tool_call>/</tool_call> 边界标记 |

### 4.2 非流式解析

**方法签名：**

```python
def extract_tool_calls(
    self,
    model_output: str,
    request: Union[Dict[str, Any], ChatCompletionRequest],
) -> ExtractedToolCallInformation
```

**返回结构：** `ExtractedToolCallInformation`

#### 场景1: 有工具调用（Qwen3 Coder 格式）

```python
# 输入 (Qwen3 Coder 格式)
'<tool_call><function=get_weather>\n<parameter=city>北京</parameter>\n</function> </tool_call>'

# 返回
ExtractedToolCallInformation(
    tools_called=True,
    tool_calls=[
        ToolCall(
            id="chatcmpl-tool-xxx...",
            type="function",
            function=FunctionCall(
                name="get_weather",
                arguments='{"city": "北京"}'
            )
        )
    ],
    content=None
)
```

#### 场景2: 有工具调用 + 前置文本

```python
# 输入
'好的，我来帮您查询。<tool_call><function=get_weather>...</function> </tool_call>'

# 返回
ExtractedToolCallInformation(
    tools_called=True,
    tool_calls=[...],
    content='好的，我来帮您查询。'  # 工具调用前的内容
)
```

#### 场景3: 无工具调用

```python
# 输入
'这是普通的回复内容'

# 返回
ExtractedToolCallInformation(
    tools_called=False,
    tool_calls=[],
    content='这是普通的回复内容'  # 原内容
)
```

#### 场景4: 多个工具调用

```python
# 返回
ExtractedToolCallInformation(
    tools_called=True,
    tool_calls=[
        ToolCall(id="chatcmpl-tool-xxx1", function=FunctionCall(name="func1", arguments='{"a": 1}')),
        ToolCall(id="chatcmpl-tool-xxx2", function=FunctionCall(name="func2", arguments='{"b": 2}'))
    ],
    content=None
)
# 注意: 每个 tool_call 有唯一的 id
```

### 4.3 流式解析

**方法签名：**

```python
def extract_tool_calls_streaming(
    self,
    previous_text: str,
    current_text: str,
    delta_text: str,
    previous_token_ids: Sequence[int],
    current_token_ids: Sequence[int],
    delta_token_ids: Sequence[int],
    request: Union[Dict[str, Any], ChatCompletionRequest],
) -> Optional[DeltaMessage]
```

**返回结构：** `Optional[DeltaMessage]`

**vLLM 风格说明：**
- 返回 `None` = "等待更多数据" 或 "不输出任何内容"
- 返回 `DeltaMessage` = 输出增量内容

#### 流式增量示例

```python
# 1. 开始工具调用 - 发送工具头
DeltaMessage(
    tool_calls=[
        DeltaToolCall(
            index=0,
            id="chatcmpl-tool-xxx",
            type="function",
            function=DeltaFunctionCall(
                name="get_weather",
                arguments=""
            )
        )
    ]
)

# 2. 参数增量
DeltaMessage(
    tool_calls=[
        DeltaToolCall(
            index=0,
            function=DeltaFunctionCall(
                arguments='{"city": "北'
            )
        )
    ]
)

# 3. 参数结束
DeltaMessage(
    tool_calls=[
        DeltaToolCall(
            index=0,
            function=DeltaFunctionCall(
                arguments='"}'
            )
        )
    ]
)
```

### 4.4 不同模型格式示例

#### Qwen3 Coder XML
```
<tool_call><function=get_weather>
<parameter=city>北京</parameter>
<parameter=unit>celsius</parameter>
</function> </tool_call>
```

#### Qwen3 XML（与 Coder 相同的 XML 结构，使用 StreamingXMLToolCallParser）
```
<tool_call><function=get_weather>
<parameter=city>北京</parameter>
<parameter=unit>celsius</parameter>
</function> </tool_call>
```

#### Hermes 格式
```
<tool_call>{"name": "get_weather", "arguments": {"city": "北京"}} </tool_call>
```

---

## 五、Reasoning Parser

### 5.1 已注册的 Reasoning Parsers

| 名称 | 类名 | 格式特点 |
|------|------|----------|
| `qwen3` | Qwen3ReasoningParser | Qwen3 推理标记（继承 BaseThinkingReasoningParser） |

### 5.2 非流式解析

**方法签名：**

```python
def extract_reasoning(
    self,
    model_output: str,
    request: Union[Dict[str, Any], ChatCompletionRequest],
) -> tuple[Optional[str], Optional[str]]
```

**返回结构：** `(reasoning, content)` 元组

#### 场景1: 有推理 + 有回复

```python
# 输入
'<think>\n用户询问天气情况。\n需要确认城市。\n</think>请问您想查询哪个城市的天气？'

# 返回
(
    '用户询问天气情况。\n需要确认城市。\n',  # reasoning（不含标记）
    '请问您想查询哪个城市的天气？'            # content
)
```

#### 场景2: 只有推理（无回复）

```python
# 输入
'<think>\n这是一个纯推理过程。\n</think>'

# 返回
(
    '这是一个纯推理过程。\n',  # reasoning
    None                        # content 为 None 或空
)
```

#### 场景3: 只有回复（无推理标记）

```python
# 输入
'这是普通的回复内容'

# 返回
(
    None,                       # reasoning 为 None
    '这是普通的回复内容'        # content 为原内容
)
```

#### 场景4: 只有 end_token（无 start_token）—— Qwen3 不匹配

```python
# 输入
'这是推理内容</think>这是正常回复'

# Qwen3ReasoningParser 返回（需要两个标记同时存在）
(
    None,                       # reasoning 为 None（缺少 start_token）
    '这是推理内容</think>这是正常回复'  # content 为原内容
)
```

> **注意**：Qwen3ReasoningParser 严格要求 `<think>` 和 `</think>` 同时存在才能提取推理内容。这与某些其他 parser（如 BaseThinkingReasoningParser 的子类）不同，后者只需 end_token 即可提取。

#### 场景5: 推理未结束（无 end_token）—— Qwen3 不匹配

```python
# 输入
'<think>\n推理内容没有结束\n持续推理中...'

# Qwen3ReasoningParser 返回（缺少 end_token）
(
    None,                       # reasoning 为 None
    '<think>\n推理内容没有结束\n持续推理中...'  # content 为原内容
)
```

### 5.3 流式解析

**方法签名：**

```python
def extract_reasoning_streaming(
    self,
    previous_text: str,
    current_text: str,
    delta_text: str,
    previous_token_ids: Sequence[int],
    current_token_ids: Sequence[int],
    delta_token_ids: Sequence[int]
) -> Optional[DeltaMessage]
```

**返回结构：** `Optional[DeltaMessage]`

#### 流式增量示例

```python
# 1. 在推理区域内
DeltaMessage(
    reasoning='推理内容增量'
)

# 2. 在 end_token 处（转换点）
DeltaMessage(
    reasoning='最后的推理',  # end_token 前
    content='正常回复开始'   # end_token 后
)

# 3. 推理结束后
DeltaMessage(
    content='正常回复内容增量'
)
```

### 5.4 Qwen3 推理格式

```
<think>推理过程</think>正常回复
```

---

## 六、组合场景（Reasoning + Tool Calls）

### 6.1 典型组合格式

```python
# 输入（Qwen3 推理 + Qwen3 Coder 工具调用）
'<think>\n用户询问北京天气。\n需要调用 API。\n</think><tool_call><function=get_weather>\n<parameter=city>北京</parameter>\n</function> </tool_call>'
```

### 6.2 解析流程

```python
# 步骤1: 用 ReasoningParser 提取 reasoning
reasoning, remaining = parser.extract_reasoning(output)
# reasoning = '用户询问北京天气。\n需要调用 API。\n'
# remaining = '<tool_call><function=get_weather>... </tool_call>'

# 步骤2: 用 ToolParser 从 remaining 提取 tool_calls
tool_result = parser.extract_tool_calls(remaining, request=raw_request)
# tool_result.tools_called = True
# tool_result.tool_calls = [ToolCall(...)]
# tool_result.content = None
```

### 6.3 最终响应结构

```python
{
    "role": "assistant",
    "content": tool_result.content,        # 可能为 None
    "reasoning": reasoning,                 # 推理内容
    "tool_calls": [
        {
            "id": "chatcmpl-tool-xxx",
            "type": "function",
            "function": {
                "name": "get_weather",
                "arguments": '{"city": "北京"}'
            }
        }
    ]
}
```

---

## 七、流式解析处理流程

### 7.1 TokenPipeline 中的流式解析

**文件：** `traj_proxy/proxy_core/pipeline/token_pipeline.py`

```python
async def _process_streaming_parse(
    self,
    delta_text: str,
    context: ProcessContext,
    previous_text: str,
    current_text: str,
    previous_token_ids: list,
    current_token_ids: list,
    delta_token_ids: list
) -> tuple:
    """处理流式解析
    
    协调 Tool Parser 和 Reasoning Parser 的流式解析。
    
    Returns:
        (content_delta, tool_calls_delta, reasoning_delta)
    """
    # 按照 vLLM 的设计: 初始化为 None
    content_delta = None
    tool_calls_delta = None
    reasoning_delta = None

    # 1. 先处理推理解析（可能改变内容归属）
    if self.parser.has_reasoning_parser and include_reasoning:
        delta_msg = self.parser.extract_reasoning_streaming(...)
        if delta_msg:
            content_delta = delta_msg.content
            reasoning_delta = delta_msg.reasoning

    # 2. 处理工具调用解析（vLLM 风格）
    if self.parser.has_tool_parser and tools:
        delta_msg = self.parser.extract_tool_calls_streaming(...)
        if delta_msg:
            tool_calls_delta = [...]
            content_delta = delta_msg.content
        else:
            # parser 返回 None = 不输出任何内容
            content_delta = None

    return content_delta, tool_calls_delta, reasoning_delta
```

### 7.2 vLLM 风格要点

1. **增量内容初始化为 None**：只有在明确需要输出时才设置值
2. **Tool Parser 优先级最高**：如果 Tool Parser 返回 None，则不输出任何内容
3. **Reasoning Parser 先执行**：可能改变内容归属

---

## 八、响应格式决策表

### 8.1 非流式响应

| 场景 | reasoning | content | tool_calls | tools_called |
|------|-----------|---------|------------|--------------|
| 纯文本 | None | 原文本 | [] | False |
| 纯推理 | 推理内容 | None/空 | [] | False |
| 推理 + 文本 | 推理内容 | 文本内容 | [] | False |
| 工具调用 | None | 前置文本/None | [ToolCall...] | True |
| 推理 + 工具调用 | 推理内容 | None | [ToolCall...] | True |
| 推理 + 文本 + 工具调用 | 推理内容 | 文本内容 | [ToolCall...] | True |

### 8.2 流式响应

| 阶段 | DeltaMessage 字段 |
|------|-------------------|
| 推理中 | `reasoning` 有值 |
| 推理结束点 | `reasoning` + `content` 可能同时有值 |
| 正常回复 | `content` 有值 |
| 工具调用开始 | `tool_calls[0].id`, `tool_calls[0].function.name` |
| 工具参数增量 | `tool_calls[0].function.arguments` 增量 |
| 工具调用结束 | `tool_calls[0].function.arguments` 闭合 |

---

## 九、关键文件路径

| 类型 | 路径 |
|------|------|
| Parser 统一接口 | `traj_proxy/proxy_core/parsers/parser_manager.py` |
| Parser 基类 | `traj_proxy/proxy_core/parsers/base.py` |
| Tool Parsers 目录 | `traj_proxy/proxy_core/parsers/vllm_compat/tool_parsers/` |
| Reasoning Parsers 目录 | `traj_proxy/proxy_core/parsers/vllm_compat/reasoning_parsers/` |
| vLLM 兼容层 | `traj_proxy/proxy_core/parsers/vllm_compat/` |
| TokenPipeline 使用 | `traj_proxy/proxy_core/pipeline/token_pipeline.py` |
| OpenAIResponseBuilder 使用 | `traj_proxy/proxy_core/builders/openai_builder.py` |

---

## 十、使用示例

### 10.1 获取 Parser

```python
from traj_proxy.proxy_core.parsers import ParserManager

# 方式1: 创建组合 Parser
parser = ParserManager.create_parser(
    tool_parser_name="qwen3_coder",
    reasoning_parser_name="qwen3",
    tokenizer=tokenizer
)

# 方式2: 列出已注册的 Parsers
tool_parsers = ParserManager.list_tool_parsers()
# ['qwen3_coder', 'qwen3_xml', 'hermes']

reasoning_parsers = ParserManager.list_reasoning_parsers()
# ['qwen3']
```

### 10.2 非流式解析

```python
# 工具调用解析
result = parser.extract_tool_calls(model_output, request=raw_request)
if result.tools_called:
    for tc in result.tool_calls:
        print(f"Function: {tc.function.name}")
        print(f"Arguments: {tc.function.arguments}")

# 推理内容解析
reasoning, content = parser.extract_reasoning(model_output, request=raw_request)
if reasoning:
    print(f"Reasoning: {reasoning}")
if content:
    print(f"Content: {content}")
```

### 10.3 流式解析

```python
# 使用上下文管理器自动重置流式状态
with parser:
    for chunk in stream:
        delta_text = chunk.choices[0].delta.content or ""
        
        # 工具调用流式解析
        tool_delta = parser.extract_tool_calls_streaming(
            previous_text=previous_text,
            current_text=current_text,
            delta_text=delta_text,
            previous_token_ids=previous_token_ids,
            current_token_ids=current_token_ids,
            delta_token_ids=delta_token_ids,
            request=raw_request
        )
        
        # 推理内容流式解析
        reasoning_delta = parser.extract_reasoning_streaming(
            previous_text=previous_text,
            current_text=current_text,
            delta_text=delta_text,
            previous_token_ids=previous_token_ids,
            current_token_ids=current_token_ids,
            delta_token_ids=delta_token_ids
        )
        
        previous_text = current_text
        previous_token_ids = current_token_ids
```

### 10.4 属性访问

```python
# 检查是否有 Parser
if parser.has_tool_parser:
    print("配置了工具解析器")

if parser.has_reasoning_parser:
    print("配置了推理解析器")

# 访问底层 Parser（高级用法）
tool_parser = parser.tool_parser
reasoning_parser = parser.reasoning_parser
```

---

## 十一、注册自定义 Parser

> **注意**：以下导入 `from vllm.xxx` 均通过 `vllm_compat/` 兼容层（`sys.path` 注入）实现，**无需安装 vLLM 包**。兼容层在模块加载时自动初始化。

### 11.1 注册 Tool Parser

```python
from vllm.tool_parsers.abstract_tool_parser import ToolParserManager

@ToolParserManager.register_tool_parser("my_custom_tool")
class MyCustomToolParser(ToolParser):
    def extract_tool_calls(self, model_output, request):
        # 自定义解析逻辑
        pass

    def extract_tool_calls_streaming(self, ...):
        # 自定义流式解析逻辑
        pass
```

### 11.2 注册 Reasoning Parser

```python
from vllm.reasoning.abs_reasoning_parsers import ReasoningParserManager

@ReasoningParserManager.register_reasoning_parser("my_custom_reasoning")
class MyCustomReasoningParser(ReasoningParser):
    def extract_reasoning(self, model_output, request):
        # 自定义解析逻辑
        pass

    def extract_reasoning_streaming(self, ...):
        # 自定义流式解析逻辑
        pass
```

---

## 十二、自定义 Parser 按需发现机制

TrajProxy 支持从自定义目录按需发现和加载 Parser，无需修改代码即可扩展解析能力。

### 12.1 配置方式

在 `config.yaml` 中配置自定义 Parser 目录：

```yaml
# 自定义 parser 目录路径
custom_parsers_dir: /app/custom_parsers
```

**本地开发配置：**

```yaml
# 本地开发时可配置为项目相对路径
custom_parsers_dir: ./custom_parsers
```

### 12.2 目录结构

自定义 Parser 目录需按以下结构组织：

```
custom_parsers/
  tool_parsers/           # 自定义工具解析器目录
    my_tool_parser.py     # 文件名即为 parser 名称
    another_parser.py
  reasoning_parsers/      # 自定义推理解析器目录
    my_reasoning_parser.py
```

**命名规则：**

- Tool Parser 文件放在 `tool_parsers/` 目录下
- Reasoning Parser 文件放在 `reasoning_parsers/` 目录下
- 文件名即为 parser 名称（如 `my_parser.py` → `"my_parser"`）

### 12.3 按需加载流程

当请求使用未注册的 parser 时，ParserManager 会自动从自定义目录查找并加载：

1. 收到 parser 名称请求（如注册模型时指定 `tool_parser: "my_custom_parser"`）
2. 先查询默认注册表（内置 parser）
3. 若未找到，从 `custom_parsers/tool_parsers/` 目录查找同名 `.py` 文件
4. 动态加载模块，查找并注册 ToolParser/ReasoningParser 子类
5. 缓存注册结果，后续请求直接使用

### 12.4 使用示例

**步骤 1：创建自定义 Parser 文件**

创建 `custom_parsers/tool_parsers/my_tool_parser.py`：

```python
from vllm.tool_parsers.abstract_tool_parser import ToolParser
from vllm.entrypoints.openai.chat_completion.protocol import (
    ChatCompletionRequest,
    ExtractedToolCallInformation,
)

class MyToolParser(ToolParser):
    """自定义工具解析器"""
    
    def extract_tool_calls(
        self,
        model_output: str,
        request: ChatCompletionRequest,
    ) -> ExtractedToolCallInformation:
        # 实现自定义解析逻辑
        # 返回 ExtractedToolCallInformation
        pass
    
    def extract_tool_calls_streaming(
        self,
        previous_text: str,
        current_text: str,
        delta_text: str,
        previous_token_ids,
        current_token_ids,
        delta_token_ids,
        request: ChatCompletionRequest,
    ):
        # 实现自定义流式解析逻辑
        pass
```

**步骤 2：注册模型时指定自定义 parser**

```python
# 注册模型（使用自定义 parser）
curl -X POST 'http://localhost:12300/models/register' \
  -H 'Content-Type: application/json' \
  -d '{
    "model_name": "my-model",
    "url": "http://inference-server:8000/v1",
    "api_key": "sk-xxx",
    "tokenizer_path": "Qwen/Qwen3.5-2B",
    "tool_parser": "my_tool_parser",  # 自定义 parser 名称
    "token_in_token_out": true
  }'
```

**步骤 3：发送请求**

首次请求时，系统自动发现并加载 `my_tool_parser.py`，后续请求直接使用缓存。

### 12.5 示例 Parser

项目自带了两个示例 Parser 文件，位于 `custom_parsers/` 目录下：

- `custom_parsers/tool_parsers/test_tool_parser.py` — `TestToolParser`（基于 Qwen3CoderToolParser 的测试示例）
- `custom_parsers/reasoning_parsers/test_reasoning_parser.py` — `TestReasoningParser`（基于 Qwen3ReasoningParser 的测试示例）

这两个 Parser 可通过按需发现机制加载，名称分别为 `"test_tool_parser"` 和 `"test_reasoning_parser"`。

### 12.6 注意事项

1. **类定义要求**：Parser 类必须继承 `ToolParser` 或 `ReasoningParser` 基类
2. **模块归属检查**：只注册在当前模块中定义的类（通过 `__module__` 属性检查），避免误注册导入的基类
3. **命名一致性**：文件名与 parser 名称必须一致
4. **错误处理**：加载失败会记录错误日志，返回 None

---

↑ [返回顶部](#parser-行为逻辑文档)