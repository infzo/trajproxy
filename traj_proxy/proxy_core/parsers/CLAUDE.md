# Parser 模块 (traj_proxy/proxy_core/parsers/)

## 模块功能

Parser 抽象层，包含所有 tool/reasoning parser 实现。

本模块从 vLLM v0.16.0 fork 并移植 parser 代码，通过零侵入式兼容层
（`vllm_compat/`）使 vLLM parser 可直接复制使用而无需修改。

## 核心原则：vLLM 对齐原则

> **所有 parser 实现必须和 vLLM 源码对齐。**

vLLM 是本项目 parser 的上游源。任何 parser 相关的设计决策，
都必须以 vLLM 的实现为基准。项目自行引入的"改进"如果 vLLM 没有对应设计，
极大概率是错误的——F4 和 F5 的反模式已经证明了这一点。

### 为什么必须对齐

1. **vLLM 是生产验证过的**：vLLM 的 parser 代码经过大规模生产验证，
   其并发模型、状态管理、边界处理都是成熟方案。
2. **升级兼容性**：对齐 vLLM 设计意味着上游更新时可以低成本同步，
   自行设计的方案会成为升级障碍。
3. **一致性**：TrajProxy 作为代理层，应与上游推理引擎的行为保持一致，
   自行设计方案会导致微妙的行为差异。

## 接口边界

| 文件 | 职责 | 说明 |
|------|------|------|
| `parser_manager.py` | Parser 注册、选择、创建 | 统一管理接口，适配 vLLM parser |
| `base.py` | 抽象基类和数据结构 | 从 vllm_compat 重新导出核心类型 |
| `vllm_compat/` | vLLM 兼容层 | 零侵入式包结构模拟 + sys.path 注入 |
| `vllm_compat/tool_parsers/` | Tool Parser 实现 | 从 vLLM v0.16.0 复制，无需修改 |
| `vllm_compat/reasoning_parsers/` | Reasoning Parser 实现 | 从 vLLM v0.16.0 复制，无需修改 |
| `vllm_compat/vllm/` | 包结构模拟 | 模拟 vllm 包以使导入路径兼容 |

## 禁止设计（Anti-patterns）

以下设计已被实践证明是错误的，**严禁引入**：

- ❌ **自定义的 per-request Parser 克隆/隔离机制**（vLLM 没有的）
  - F4 曾为此引入 `clone_for_streaming()`，使用 `copy.copy()` 浅拷贝共享 parser
  - 浅拷贝无法正确隔离嵌套对象（如 Qwen3XMLToolParser 内嵌的
    StreamingXMLToolCallParser），导致并发竞态仍然存在
  - **正确做法**：对齐 vLLM 的 per-request 实例化模式，
    使用 `parser_cls(tokenizer, tools, **kwargs)` 从类引用创建全新实例，
   彻底消除共享引用问题

- ❌ **自定义的 reset 方法优先级链**（hasattr 检测多个候选）
  - F4 曾在 `reset_streaming_state()` 中用 `hasattr` 优先级链：
    先检测 `reset_streaming_state`，再检测 `_reset_streaming_state`
  - 这违反了 vLLM 的设计：每个 parser 子类只有一种 reset 方式，
    不需要 fallback 链
  - **正确做法**：使用 `try/except AttributeError` 分别尝试各子类的
    特有重置方法，不做优先级选择

- ❌ **修改 parser 方法签名**（破坏 vLLM 兼容性）
  - parser 的 `extract_tool_calls_streaming()` 等方法签名必须与 vLLM 一致
  - 添加额外参数（如 `tool_call_idx`）会导致复制来的 parser 无法直接使用

- ❌ **在 parser 中引入 vLLM 没有的字段或行为**
  - 例如自行添加 `thinking_enabled` 的动态切换逻辑
  - vLLM 的 reasoning parser 通过构造参数设置，不支持运行时动态切换

## 并发隔离方案（对齐 vLLM）

vLLM 的并发模型：**per-request 实例化**。

vLLM `serving_chat.py` 在每次请求时创建全新的 Parser 实例：
```python
parsers = [self.parser_cls(tokenizer, request.tools,
           chat_template_kwargs=chat_template_kwargs)
           for _ in range(num_choices)]
```

TrajProxy 采用相同策略：
- `ParserManager.create_parser()` 通过 `type()` 动态创建 Parser 子类，
  将 `reasoning_parser_cls` / `tool_parser_cls` 设置为子类的类属性
  （对齐 vLLM `_WrappedParser` 的动态子类化模式）
- 流式和非流式请求均使用 `parser_cls(tokenizer, tools, **kwargs)`
  创建独立的 per-request Parser 实例
  （对齐 vLLM `self.parser_cls(tokenizer, request.tools, chat_template_kwargs=...)`）

关键优势：
- **彻底隔离**：全新实例，无共享引用（包括 Qwen3XML 的嵌套对象）
- **vLLM 对齐**：与 vLLM 的做法完全一致，便于上游同步
- **无浅拷贝风险**：不使用 `copy.copy()`，避免嵌套对象共享引用

## MD5 一致性校验（强制）

`vllm_compat/tool_parsers/` 与 `vllm_compat/reasoning_parsers/` 下从 vLLM 复制的
parser 文件，其 MD5 校验和必须与 `../vllm/` 源码中对应文件**完全一致**。

- **校验范围（强制 MD5 一致）**：
  - `vllm_compat/tool_parsers/*.py`（除 `__init__.py` 外）→ 对应 `../vllm/vllm/tool_parsers/`
  - `vllm_compat/reasoning_parsers/*.py`（除 `__init__.py` 外）→ 对应 `../vllm/vllm/reasoning/`
- **适配层（豁免 MD5）**：
  - `vllm_compat/vllm/` 下所有文件（包结构模拟、协议类 stub、工具函数 stub、tokenizers stub）
  - 所有 `__init__.py`（自动发现 / 注册配置）
  这些属于为减轻 Python 依赖而主动 stub 化的项目自有代码，不要求与上游 MD5 一致。
- **执行时机**：修改或新增任何 parser 文件前后，均需 `md5sum` 对比上游确认。
- **禁止操作**：对校验范围内的 parser 文件做任何本地修改——代码格式化、变量重命名、
  删除注释、调整导入顺序等任何导致 MD5 变化的操作均视为违规。

## 推荐做法

- ✅ **遇到并发问题先看 vLLM 怎么解决**
  - vLLM 的 `DelegatingParser` 是并发场景的参考实现
  - 仔细阅读 vLLM 源码中的状态管理和请求隔离机制

- ✅ **修改 parser 前先对比 vLLM 源码**
  - 本地 vLLM 源码路径：`/Users/liujiang/Workspace/Code/vllm`
  - 确认 vLLM 是否已有类似设计，如果有则对齐实现

- ✅ **项目新增的 parser 也要遵循 vLLM 的设计模式**
  - 新 parser 必须继承 `vllm_compat/vllm/` 下的基类
  - 方法签名必须与 vLLM 的抽象基类一致
  - 注册通过 `ToolParserManager` / `ReasoningParserManager` 完成

## 依赖关系

- **上游依赖**：`vllm_compat/vllm/` 下的基类和协议定义
- **下游依赖**：`parser_manager.py` 被 `Processor` 和 `Pipeline` 使用
- **外部依赖**：`regex` 库（vLLM parser 使用）、`transformers`（Tokenizer）

## 相关文档

- [vLLM Parser 适配层 README](vllm_compat/README.md)
- [vLLM 对齐原则](../../.opencode/instructions/vllm-alignment.md)
- [Parser 设计文档](../../../docs/design/modules/parser.md)
