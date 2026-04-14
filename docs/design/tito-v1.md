# TITO 前缀匹配方案设计

> **导航**: [文档中心](../README.md) | [架构文档](architecture.md) | [TITO v2](tito-v2.md) | [模板一致性](template_consistency.md)

## 问题背景

在RL（强化学习）场景的前缀匹配缓存中，多轮对话存在tokenizer prompt template不一致的问题。

### 问题示例

**第一轮对话：**
```
<|im_start|>user\n你好<|im_end|>\n<|im_start|>assistant\n<think>\n</think>\n你好！有什么我可以帮你的吗？😊<|im_end|>
```

**第二轮对话（问题所在）：**
```
<|im_start|>user\n你好<|im_end|>\n<|im_start|>assistant\n你好！有什么我可以帮你的吗？😊<|im_end|><|im_end|>\n<|im_start|>user\n今天天气怎么样<|im_end|>\n...
```

第一轮缓存的 `prompt_text + response_text` 与第二轮 `apply_chat_template` 生成的文本不一致。

## 核心问题

1. **一致性问题**：messages → text → token 转换中，text 层面存在 boundary tokens 不一致
2. **前缀匹配问题**：基于 text 匹配不可靠

## 架构设计参考

### vLLM 架构分析

vLLM 的 Parser 设计：

```
Request → tokenizer.apply_chat_template() → inference → tokenizer.decode()
                                                      ↓
                                              ToolParser.extract_tool_calls(text)
                                                      ↓
                                              ReasoningParser.extract_reasoning(text)
                                                      ↓
                                                  Response
```

**特点**：
- **Parser 独立**：ToolParser 和 ReasoningParser 是独立组件
- **输入是 text**：Parser 直接处理解码后的文本，不是 messages
- **serving 层使用**：Parser 在 serving 层（而非 Converter 层）使用

### Miles 架构分析

Miles 的 TITOTokenizer 设计：

```
LinearTrajectory (存储 messages + token_ids)
        ↓
prepare_pretokenized() → 基于 messages 匹配
        ↓
TITOTokenizer.merge_tokens() → 合并 cached + incremental
        ↓
        token_ids
```

**特点**：
- **基于 messages 匹配**：直接比较 messages 结构，避免 text 不一致
- **增量 tokenization**：使用 dummy-message diff 计算增量 tokens
- **boundary 处理**：模型特定的 boundary token 处理

## 解决方案：对称 Converter 架构（借鉴 vLLM + Miles）

### 架构概览

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         对称 Converter 架构                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  正向（请求）                         逆向（响应）                            │
│  ─────────────                       ─────────────                          │
│                                                                              │
│  OpenAI Request ──────────────────→ OpenAI Response                         │
│        ↓                                    ↑                               │
│  [FormatConverter]                  [FormatConverter]                       │
│        ↓                                    ↑                               │
│     messages  ←──────────────────────────→ messages                         │
│        ↓                                    ↑                               │
│  [TokenConverter]                   [TokenConverter]                        │
│        ↓                                    ↑                               │
│     token_ids ──────→ Infer ──────→ response_token_ids                      │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 三个核心组件

| 组件 | 正向职责 | 逆向职责 | 说明 |
|------|---------|---------|------|
| **FormatConverter** | OpenAI Request → messages | messages → OpenAI Response | 格式转换 |
| **TokenConverter** | messages → token_ids | token_ids → messages | 编码转换+前缀匹配 |
| **ToolParser/ReasoningParser** | - | text → parsed content | 独立解析组件（vLLM 风格）|

### 与 vLLM 架构对比

| 职责层 | vLLM 架构 | 新 TITO 架构 | 对比 |
|--------|----------|--------------|------|
| **格式转换** | 无独立组件 | FormatConverter | 新架构更清晰 |
| **Tokenization** | 直接使用 tokenizer | TokenConverter | 封装前缀匹配 |
| **Tool 解析** | ToolParser（独立） | ToolParser（独立） | **一致** |
| **Reasoning 解析** | ReasoningParser（独立） | ReasoningParser（独立） | **一致** |
| **Parser 使用位置** | serving 层 | Pipeline 层 | **一致** |
| **前缀匹配** | 无 | TokenConverter 内部 | RL 特有 |

---

## 详细设计

### 1. FormatConverter

**职责**：OpenAI 格式 ↔ 内部 messages 格式

```python
"""
FormatConverter - 格式转换器

正向：OpenAI Request → messages
逆向：messages → OpenAI Response
"""

class FormatConverter:
    """OpenAI 格式 ↔ Messages"""

    # ==================== 正向 ====================

    def to_messages(self, raw_request: dict) -> tuple[list, dict]:
        """
        OpenAI Request → messages + params

        Returns:
            (messages, request_params)
        """
        messages = raw_request.get("messages", [])
        params = {
            k: v for k, v in raw_request.items()
            if k not in ["messages", "model"]
        }
        return messages, params

    # ==================== 逆向 ====================

    def to_response(
        self,
        messages: list,
        assistant_message: dict,
        context: "ProcessContext",
    ) -> dict:
        """
        messages → OpenAI Response

        Args:
            messages: 完整对话的 messages
            assistant_message: 解析后的 assistant 消息
            context: 处理上下文
        """
        return {
            "id": f"chatcmpl-{context.request_id}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": context.model,
            "choices": [{
                "index": 0,
                "message": assistant_message,
                "finish_reason": context.finish_reason or "stop",
            }],
            "usage": {
                "prompt_tokens": context.prompt_tokens,
                "completion_tokens": context.completion_tokens,
                "total_tokens": context.total_tokens,
            },
        }
```

### 2. TokenConverter（核心）

**职责**：messages ↔ token_ids

**正向功能**：
- messages → token_ids
- 前缀匹配缓存（基于 messages 结构）
- 增量 tokenization（TITOTokenizer）

**逆向功能**：
- token_ids → text（解码）
- 调用 Parser 解析 tool_calls/reasoning

**重要**：TokenConverter 的逆向返回的是 **assistant message dict**，不是完整的 messages 列表

```python
"""
TokenConverter - Token 转换器

正向：messages → token_ids（含前缀匹配、增量tokenization）
逆向：token_ids → messages（含解码、tool/reasoning解析）
"""

@dataclass
class ToTokenResult:
    """正向转换结果"""
    token_ids: list[int]
    cache_hit_tokens: int
    incremental_tokens: int


class TokenConverter:
    """Messages ↔ Token IDs"""

    def __init__(
        self,
        tokenizer,
        request_repository: Optional["RequestRepository"] = None,
        tool_parser: Optional["ToolParser"] = None,
        reasoning_parser: Optional["ReasoningParser"] = None,
    ):
        self.tokenizer = tokenizer
        self.request_repository = request_repository
        self.tool_parser = tool_parser  # Parser 是独立组件（vLLM 风格）
        self.reasoning_parser = reasoning_parser
        self.tito = self._create_tito_tokenizer(tokenizer)

    # ==================== 正向：messages → token_ids ====================

    async def to_token_ids(
        self,
        messages: list,
        tools: Optional[list] = None,
        context: "ProcessContext" = None,
    ) -> ToTokenResult:
        """
        messages → token_ids

        包含：
        1. 前缀匹配缓存
        2. 增量 tokenization
        """
        # 尝试前缀匹配
        if context and context.session_id and self.request_repository:
            cached = await self._find_prefix_match(messages, context.session_id)

            if cached:
                # 增量 tokenization
                result = self.tito.tokenize_incremental(
                    old_messages=cached["messages"],
                    cached_token_ids=cached["token_ids"],
                    new_messages=messages,
                    tools=tools,
                )
                return ToTokenResult(
                    token_ids=result.token_ids,
                    cache_hit_tokens=result.cached_count,
                    incremental_tokens=result.incremental_count,
                )

        # 完整 tokenization
        token_ids = self.tito.tokenize(messages, tools=tools)
        return ToTokenResult(
            token_ids=token_ids,
            cache_hit_tokens=0,
            incremental_tokens=len(token_ids),
        )

    # ==================== 逆向：token_ids → assistant message ====================

    def to_assistant_message(
        self,
        token_ids: list[int],
        context: "ProcessContext" = None,
    ) -> dict:
        """
        token_ids → assistant message

        包含：
        1. 解码为 text
        2. 调用 Parser 解析 tool_calls 和 reasoning
        3. 构建 assistant message dict

        注意：Parser 输入是 text，不是 messages（参考 vLLM 设计）
        """
        # 解码
        text = self.tokenizer.decode(token_ids, skip_special_tokens=False)

        # 构建 assistant message
        message = {"role": "assistant", "content": text}

        # 调用 Parser 解析（Parser 是独立组件）
        if self.tool_parser:
            tool_calls, remaining_text = self.tool_parser.extract_tool_calls(text)
            if tool_calls:
                message["tool_calls"] = tool_calls
                message["content"] = remaining_text

        if self.reasoning_parser:
            reasoning, remaining_text = self.reasoning_parser.extract_reasoning(text)
            if reasoning:
                message["reasoning_content"] = reasoning
                if not message.get("tool_calls"):
                    message["content"] = remaining_text

        return message

    # ==================== 流式支持 ====================

    def to_messages_stream(
        self,
        delta_ids: list[int],
        buffer_ids: list[int],
        buffer_text: str,
        context: "ProcessContext",
    ) -> tuple[dict, list[int], str]:
        """
        流式逆向

        Returns:
            (delta_message, new_buffer_ids, new_buffer_text)
        """
        # 增量解码
        new_buffer_ids = buffer_ids + delta_ids
        full_text = self.tokenizer.decode(new_buffer_ids, skip_special_tokens=False)
        delta_text = full_text[len(buffer_text):]

        # 流式解析
        delta_message = {"role": "assistant", "content": delta_text}

        # 调用 Parser 流式解析
        if self.tool_parser:
            tool_calls = self.tool_parser.extract_tool_calls_streaming(
                previous_text=buffer_text,
                current_text=full_text,
                delta_text=delta_text,
            )
            if tool_calls:
                delta_message["tool_calls"] = tool_calls

        if self.reasoning_parser:
            reasoning = self.reasoning_parser.extract_reasoning_streaming(
                previous_text=buffer_text,
                current_text=full_text,
                delta_text=delta_text,
            )
            if reasoning:
                delta_message["reasoning_content"] = reasoning

        return delta_message, new_buffer_ids, full_text

    # ==================== 私有方法 ====================

    async def _find_prefix_match(
        self,
        messages: list,
        session_id: str,
    ) -> Optional[dict]:
        """基于 messages 结构查找前缀匹配"""
        history = await self.request_repository.get_by_session(session_id)

        for record in history:
            cached_messages = record.get("messages", [])
            cached_token_ids = record.get("token_ids", [])

            if not cached_messages or not cached_token_ids:
                continue

            if len(messages) >= len(cached_messages):
                if self._messages_prefix_match(cached_messages, messages):
                    return {
                        "messages": cached_messages,
                        "token_ids": cached_token_ids,
                    }

        return None

    def _messages_prefix_match(self, prefix: list, full: list) -> bool:
        for i, msg in enumerate(prefix):
            if not self._message_equal(msg, full[i]):
                return False
        return True

    def _message_equal(self, m1: dict, m2: dict) -> bool:
        return (
            m1.get("role") == m2.get("role")
            and m1.get("content") == m2.get("content")
        )
```

### 3. TITOTokenizer（增量 tokenization）

```python
"""
TITOTokenizer - 增量 Tokenization

使用 dummy-message diff 计算增量 tokens
"""

@dataclass
class TokenizationResult:
    token_ids: list[int]
    cached_count: int = 0
    incremental_count: int = 0


class TITOTokenizer:
    """增量 Tokenization"""

    def __init__(self, tokenizer):
        self.tokenizer = tokenizer

    def tokenize(self, messages: list, tools: Optional[list] = None) -> list[int]:
        """完整 tokenization"""
        return self.tokenizer.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=True,
            tools=tools,
        )

    def tokenize_incremental(
        self,
        old_messages: list,
        cached_token_ids: list[int],
        new_messages: list,
        tools: Optional[list] = None,
    ) -> TokenizationResult:
        """
        增量 tokenization

        1. 验证 append-only
        2. 使用 dummy-message diff 计算增量
        3. 合并 cached + incremental
        """
        appended = new_messages[len(old_messages):]

        if not appended:
            return TokenizationResult(
                token_ids=cached_token_ids,
                cached_count=len(cached_token_ids),
                incremental_count=0,
            )

        # dummy-message diff
        incremental_ids = self._compute_incremental(appended, tools)

        # 合并（含 boundary 处理）
        merged_ids = self.merge_tokens(cached_token_ids, incremental_ids)

        return TokenizationResult(
            token_ids=merged_ids,
            cached_count=len(cached_token_ids),
            incremental_count=len(incremental_ids),
        )

    def merge_tokens(self, prefix_ids: list[int], incremental_ids: list[int]) -> list[int]:
        """合并 tokens（子类可覆盖处理 boundary）"""
        return list(prefix_ids) + incremental_ids

    def _compute_incremental(self, appended: list, tools: Optional[list]) -> list[int]:
        """使用 dummy-message diff 计算增量"""
        dummy_user = {"role": "user", "content": "dummy"}
        dummy_assistant = self._build_dummy_assistant(appended)
        base = [dummy_user, dummy_assistant]

        tokens_without = self.tokenizer.apply_chat_template(
            base, tokenize=True, add_generation_prompt=False
        )
        tokens_with = self.tokenizer.apply_chat_template(
            base + appended, tokenize=True, add_generation_prompt=True, tools=tools
        )

        return list(tokens_with[len(tokens_without):])

    def _build_dummy_assistant(self, appended: list) -> dict:
        tool_msgs = [m for m in appended if m.get("role") == "tool"]
        if tool_msgs:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": m.get("tool_call_id", f"call_{i}"),
                        "type": "function",
                        "function": {"name": m.get("name", "dummy"), "arguments": "{}"},
                    }
                    for i, m in enumerate(tool_msgs)
                ],
            }
        return {"role": "assistant", "content": ""}


class Qwen3TITOTokenizer(TITOTokenizer):
    """Qwen3: 处理缺失的换行符 boundary"""

    def __init__(self, tokenizer):
        super().__init__(tokenizer)
        self._newline_id = tokenizer.encode("\n", add_special_tokens=False)[0]
        self._im_end_id = tokenizer.convert_tokens_to_ids("<|im_end|>")

    def merge_tokens(self, prefix_ids: list[int], incremental_ids: list[int]) -> list[int]:
        prefix = list(prefix_ids)
        # 模型停在 <|im_end|>，但模板需要 <|im_end|>\n
        if prefix and prefix[-1] == self._im_end_id:
            prefix.append(self._newline_id)
        return prefix + incremental_ids
```

---

## Pipeline 流程

```python
class TITOPipeline:
    """对称 Pipeline - 参考 vLLM 设计"""

    def __init__(
        self,
        model,
        infer_client,
        request_repository,
        tokenizer_path,
        tool_parser: Optional["ToolParser"] = None,      # Parser 独立传入
        reasoning_parser: Optional["ReasoningParser"] = None,
    ):
        self.model = model
        self.infer_client = infer_client

        self.format_converter = FormatConverter()
        self.token_converter = TokenConverter(
            tokenizer=self._load_tokenizer(tokenizer_path),
            request_repository=request_repository,
            tool_parser=tool_parser,          # 传递给 TokenConverter
            reasoning_parser=reasoning_parser,
        )

    async def process(self, raw_request: dict, context: "ProcessContext"):
        """非流式处理"""

        # ==================== 正向 ====================

        # OpenAI Request → messages
        messages, params = self.format_converter.to_messages(raw_request)
        context.messages = messages
        context.request_params = params

        # messages → token_ids（含前缀匹配、增量 tokenization）
        result = await self.token_converter.to_token_ids(
            messages, tools=params.get("tools"), context=context
        )
        context.token_ids = result.token_ids
        context.cache_hit_tokens = result.cache_hit_tokens
        context.prompt_tokens = len(result.token_ids)

        # 推理
        response = await self.infer_client.send_completion(
            prompt=result.token_ids, model=self.model, **params
        )

        # ==================== 逆向 ====================

        # response_token_ids → assistant_message（含 Parser 解析）
        response_token_ids = self._extract_token_ids(response)
        assistant_message = self.token_converter.to_assistant_message(response_token_ids, context)
        context.response_text = assistant_message.get("content", "")

        # messages → OpenAI Response
        full_messages = messages + [assistant_message]
        context.raw_response = self.format_converter.to_response(
            messages=full_messages,
            assistant_message=assistant_message,
            context=context,
        )

        # 存储（用于下次前缀匹配）
        context.full_conversation_messages = full_messages
        context.full_conversation_token_ids = result.token_ids + response_token_ids
        await self._store_trajectory(context)

        return context
```

---

## Parser 组件设计（参考 vLLM）

### 设计原则

**Parser 是独立组件**，不是 Converter 的一部分：
- 输入：`text`（解码后的文本）
- 输出：解析后的结构（tool_calls、reasoning）
- 使用位置：Pipeline 层（通过 TokenConverter 调用）

这与 vLLM 的设计一致，Parser 在 serving 层使用。

### ToolParser 接口

```python
"""
ToolParser - 独立的工具解析组件

参考：vllm/tool_parsers/abstract_tool_parser.py
"""

from abc import ABC, abstractmethod
from typing import Optional


class ToolParser(ABC):
    """工具调用解析器基类"""

    @abstractmethod
    def extract_tool_calls(
        self,
        text: str,
    ) -> tuple[list[dict], str]:
        """
        从文本中提取工具调用

        Args:
            text: 模型生成的文本

        Returns:
            (tool_calls, remaining_text)
        """
        pass

    @abstractmethod
    def extract_tool_calls_streaming(
        self,
        previous_text: str,
        current_text: str,
        delta_text: str,
    ) -> Optional[list[dict]]:
        """
        流式提取工具调用

        Args:
            previous_text: 之前的累积文本
            current_text: 当前累积文本
            delta_text: 本轮增量文本

        Returns:
            增量 tool_calls（可能为 None）
        """
        pass
```

### ReasoningParser 接口

```python
"""
ReasoningParser - 独立的推理解析组件
"""

from abc import ABC, abstractmethod
from typing import Optional


class ReasoningParser(ABC):
    """推理内容解析器基类"""

    @abstractmethod
    def extract_reasoning(
        self,
        text: str,
    ) -> tuple[Optional[str], str]:
        """
        从文本中提取推理内容

        Args:
            text: 模型生成的文本

        Returns:
            (reasoning, remaining_text)
        """
        pass

    @abstractmethod
    def extract_reasoning_streaming(
        self,
        previous_text: str,
        current_text: str,
        delta_text: str,
    ) -> Optional[str]:
        """
        流式提取推理内容
        """
        pass
```

---

## 数据模型调整

### ProcessContext

```python
@dataclass
class ProcessContext:
    # ... 保留字段 ...

    # 新增：用于 TITO 前缀匹配
    full_conversation_messages: Optional[List[Dict[str, Any]]] = None
```

### 数据库 Schema

```sql
ALTER TABLE public.request_details_active
ADD COLUMN IF NOT EXISTS full_conversation_messages JSONB;
```

---

## 模块位置

```
traj_proxy/proxy_core/
├── parsers/                        # Parser 组件（独立，参考 vLLM）
│   ├── __init__.py
│   ├── base.py                     # ToolParser, ReasoningParser 基类
│   ├── qwen_tool_parser.py         # Qwen 工具解析实现
│   └── qwen_reasoning_parser.py    # Qwen 推理解析实现
├── converters/
│   ├── __init__.py
│   ├── format_converter.py         # FormatConverter
│   └── token_converter.py          # TokenConverter（调用 Parser）
├── tokenization/
│   ├── __init__.py
│   └── tito_tokenizer.py           # TITOTokenizer
├── pipeline/
│   ├── __init__.py
│   ├── base.py
│   ├── direct_pipeline.py          # 直接转发模式
│   └── tito_pipeline.py            # TITO 模式
```

---

## 实施步骤

### 第一阶段：核心模块

- [ ] 创建 `parsers/` 目录
  - [ ] `parsers/base.py`: ToolParser 和 ReasoningParser 基类
  - [ ] `parsers/qwen_tool_parser.py`: Qwen 模型的工具解析实现
  - [ ] `parsers/qwen_reasoning_parser.py`: Qwen 模型的推理解析实现

- [ ] 创建 `tokenization/tito_tokenizer.py`
  - [ ] TITOTokenizer 基类
  - [ ] Qwen3TITOTokenizer 子类

- [ ] 创建 `converters/format_converter.py`
  - [ ] FormatConverter 类

- [ ] 创建 `converters/token_converter.py`
  - [ ] TokenConverter 类
  - [ ] 集成 Parser 调用

### 第二阶段：Pipeline

- [ ] 创建 `pipeline/tito_pipeline.py`
  - [ ] TITOPipeline 类
  - [ ] 非流式处理流程
  - [ ] 流式处理流程

- [ ] 修改 Processor 选择 Pipeline

- [ ] 数据模型和数据库调整
  - [ ] ProcessContext 增加 `full_conversation_messages`
  - [ ] 数据库 schema 增加 `full_conversation_messages` 列

### 第三阶段：测试验证

- [ ] 单元测试
  - [ ] TITOTokenizer 测试
  - [ ] Converter 测试
  - [ ] Parser 测试

- [ ] 集成测试
  - [ ] 多轮对话前缀匹配测试
  - [ ] Tool calling 测试
  - [ ] Reasoning 测试

### 第四阶段：清理

- [ ] 标记旧 Converter 废弃
- [ ] 迁移现有逻辑
