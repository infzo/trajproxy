# TITO 模式 Message Converter 集成一致性 Jinja 模板设计方案

> **导航**: [文档中心](../README.md) | [TITO v1](tito-v1.md) | [模板一致性](template_consistency.md) | [Jinja 处理](jinja-process.md)

## Context

当前系统在 TITO (token-in-token-out) 模式下使用 `MessageConverter` 将 OpenAI messages 转换为 prompt text。现有的 `chat_template.jinja` 模板存在**多轮不一致**问题，导致前缀匹配缓存失败。

**问题根源**：
- 原始模板包含位置相关的条件逻辑（如 `loop.index0 > ns.last_query_index`）
- 第一轮对话和第二轮对话重新渲染时，相同 messages 的输出文本不一致
- 前缀匹配缓存依赖文本前缀匹配，不一致会导致缓存完全失效

**解决方案**：
- 使用多轮一致性模板 `chat_template_tito.jinja`（原 `chat_template_consistency.jinja`）
- 确保相同 messages 在不同轮次渲染时输出完全一致

---

## Plan

### 1. 重命名一致性模板

将 `chat_template_consistency.jinja` 重命名为 `chat_template_tito.jinja`：

```bash
mv models/Qwen/Qwen3.5-2B/chat_template_consistency.jinja models/Qwen/Qwen3.5-2B/chat_template_tito.jinja
```

### 2. 修改 MessageConverter 支持自定义 chat_template

**文件**: `traj_proxy/proxy_core/converters/message_converter.py`

**修改内容**:
- 新增 `tito_template_path` 参数，支持加载自定义 jinja 模板
- 当 TITO 模式开启时，使用 `chat_template_tito.jinja` 替代默认模板

### 3. 修改 Processor 传递 TITO 模板配置

**文件**: `traj_proxy/proxy_core/processor.py`

**修改内容**:
- 在创建 `MessageConverter` 时，传入 TITO 模板路径
- 模板路径自动推断：`{tokenizer_path}/chat_template_tito.jinja`

### 4. 架构设计

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         TITO 模式处理流程                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  OpenAI Request                                                              │
│        ↓                                                                     │
│  [MessageConverter]                                                          │
│        │                                                                     │
│        ├── TITO 模式: 使用 chat_template_tito.jinja                         │
│        │   - 保证多轮一致性                                                  │
│        │   - 前缀匹配缓存可正常工作                                          │
│        │                                                                     │
│        └── 普通模式: 使用默认 chat_template.jinja                           │
│        ↓                                                                     │
│     prompt_text                                                              │
│        ↓                                                                     │
│  [TokenConverter + PrefixMatchCache]                                         │
│        │                                                                     │
│        └── 文本前缀匹配成功 ✓ (因为模板一致性)                               │
│        ↓                                                                     │
│     token_ids (部分缓存命中)                                                  │
│        ↓                                                                     │
│     推理服务                                                                  │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 5. 关键修改点

#### 5.1 MessageConverter 改造

```python
class MessageConverter(BaseConverter):
    """消息转换器 - 将 OpenAI Messages 转换为 PromptText"""

    def __init__(
        self,
        tokenizer,
        tito_template_path: Optional[str] = None
    ):
        """初始化 MessageConverter

        Args:
            tokenizer: transformers.PreTrainedTokenizerBase 实例
            tito_template_path: TITO 模式使用的 jinja 模板路径
        """
        self.tokenizer = tokenizer
        self.tito_template_path = tito_template_path
        self._tito_template = None

        if tito_template_path:
            self._tito_template = self._load_jinja_template(tito_template_path)

    def _load_jinja_template(self, path: str):
        """加载自定义 jinja 模板"""
        from jinja2 import Environment, FileSystemLoader
        from pathlib import Path

        template_path = Path(path)
        if not template_path.exists():
            raise FileNotFoundError(f"TITO 模板文件不存在: {path}")

        env = Environment(loader=FileSystemLoader(template_path.parent))
        return env.get_template(template_path.name)

    async def convert(self, messages, context) -> str:
        """将 OpenAI Messages 转换为 PromptText"""
        processed_messages = self._preprocess_messages(messages)

        if self._tito_template:
            # TITO 模式：使用自定义 jinja 模板
            return self._render_with_tito_template(processed_messages, context)

        # 普通模式：使用 tokenizer 默认模板
        return self.tokenizer.apply_chat_template(
            processed_messages,
            tokenize=False,
            add_generation_prompt=True,
            **self._build_template_kwargs(context)
        )

    def _render_with_tito_template(self, messages, context) -> str:
        """使用 TITO 模板渲染"""
        template_kwargs = {
            "messages": messages,
            "add_generation_prompt": True,
        }

        # 添加 tools, documents 等参数
        tools = context.request_params.get("tools")
        if tools:
            template_kwargs["tools"] = tools

        return self._tito_template.render(**template_kwargs)
```

#### 5.2 Processor 改造

```python
def _create_token_pipeline(self) -> BasePipeline:
    """创建 Token 模式 Pipeline 及其依赖"""
    # 加载 tokenizer
    tokenizer = AutoTokenizer.from_pretrained(self.tokenizer_path)

    # 推断 TITO 模板路径
    tito_template_path = self._get_tito_template_path()

    # 创建 MessageConverter（传入 TITO 模板）
    message_converter = MessageConverter(tokenizer, tito_template_path)

    # ... 其他组件创建 ...

def _get_tito_template_path(self) -> Optional[str]:
    """获取 TITO 模板路径"""
    from pathlib import Path

    tokenizer_dir = Path(self.tokenizer_path)
    tito_template = tokenizer_dir / "chat_template_tito.jinja"

    if tito_template.exists():
        return str(tito_template)

    logger.warning(
        f"TITO 模板不存在: {tito_template}，将使用默认模板。"
        f"前缀匹配缓存可能无法正常工作。"
    )
    return None
```

---

## 验证方案

1. **模板验证**：运行 `scripts/tools/verify_jinja_consistency.py` 确保模板一致性
2. **前缀匹配验证**：在 E2E 测试中验证多轮对话的缓存命中率
3. **输出一致性验证**：对比 TITO 模式和普通模式的单轮输出

---

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `models/Qwen/Qwen3.5-2B/chat_template_consistency.jinja` | 重命名 | → `chat_template_tito.jinja` |
| `traj_proxy/proxy_core/converters/message_converter.py` | 修改 | 支持自定义 jinja 模板 |
| `traj_proxy/proxy_core/processor.py` | 修改 | 自动推断并传入 TITO 模板路径 |
| `docs/jinja-process.md` | 更新 | 更新模板命名说明 |

---

## 兼容性考虑

- 如果 `chat_template_tito.jinja` 不存在，回退到默认模板（并输出警告）
- TITO 模板与默认模板的首轮输出必须一致（通过验证脚本保证）
- 不影响非 TITO 模式的正常使用