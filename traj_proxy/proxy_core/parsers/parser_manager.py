"""
Parser 管理器

统一管理 Tool Parser 和 Reasoning Parser 的创建和获取。
支持从自定义目录按需发现和加载 parser。
"""
import sys
import importlib.util
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Any, Dict, Sequence, Union, Tuple

# 确保 vllm 兼容层初始化
from traj_proxy.proxy_core.parsers.vllm_compat import ensure_initialized

# 导入 vllm 兼容的管理器
from vllm.tool_parsers.abstract_tool_parser import (
    ToolParser,
    ToolParserManager,
)
from vllm.reasoning.abs_reasoning_parsers import (
    ReasoningParser,
    ReasoningParserManager,
)
from vllm.entrypoints.openai.chat_completion.protocol import (
    ChatCompletionRequest,
    ChatCompletionToolsParam,
)
from vllm.entrypoints.openai.engine.protocol import (
    DeltaMessage,
    ExtractedToolCallInformation,
    FunctionDefinition,
)
from traj_proxy.utils.config import get_custom_parsers_dir

_logger = logging.getLogger(__name__)


def _get_custom_parsers_dirs() -> Tuple[Path, Path]:
    """获取自定义 parser 目录路径（从配置文件读取）"""
    base_dir = Path(get_custom_parsers_dir())
    return base_dir / "tool_parsers", base_dir / "reasoning_parsers"


@dataclass
class StreamState:
    """Mutable state for Parser.parse_delta(). One per stream.

    对齐 vLLM DelegatingParser.StreamState 设计。
    """
    reasoning_ended: bool = False
    tool_call_text_started: bool = False
    prompt_reasoning_checked: bool = False
    previous_text: str = ""
    previous_token_ids: list = field(default_factory=list)
    history_tool_call_cnt: int = 0
    tool_call_id_type: str = "random"
    function_name_returned: bool = False


class Parser:
    """Parser 统一接口（适配器模式）

    对外提供统一接口，对内适配 vLLM parser。
    支持 vLLM 原始 parser 无需修改即可使用。

    对齐 vllm _WrappedParser 设计：
    - reasoning_parser_cls / tool_parser_cls 作为类属性存储 parser 类引用，
      所有实例共享同一个类引用（对齐 vllm _WrappedParser.reasoning_parser_cls /
      _WrappedParser.tool_parser_cls 的类属性设计）
    - ParserManager.create_parser() 通过 type() 动态创建子类并设置类属性，
      与 vllm ParserManager.get_parser() 的动态子类化模式一致
    - __init__ 中从类属性 self.__class__.reasoning_parser_cls /
      self.__class__.tool_parser_cls 实例化子 parser，
      与 vllm _WrappedParser.__init__ 完全一致
    - 流式路径和非流式路径均使用 parser_cls(tokenizer, tools, **kwargs)
      创建独立实例，与 vllm serving_chat.py 中
      self.parser_cls(tokenizer, request.tools) 的 per-request 实例化模式一致

    使用示例：
        # ParserManager.create_parser 返回 (parser_cls, parser_instance)
        parser_cls, parser = ParserManager.create_parser(...)
        # 流式和非流式：均使用 parser_cls 创建 per-request parser
        request_parser = parser_cls(tokenizer, request.tools, **kwargs)
    """

    # 类属性，默认 None（对齐 vllm _WrappedParser）
    # 动态子类通过 type() 设置这些类属性
    reasoning_parser_cls: Optional[type] = None
    tool_parser_cls: Optional[type] = None

    def __init__(
        self,
        tokenizer: Any,
        tools: Optional[list] = None,
        **kwargs,
    ):
        """初始化 Parser（对齐 vllm _WrappedParser.__init__）

        从类属性 self.__class__.reasoning_parser_cls /
        self.__class__.tool_parser_cls 实例化子 parser，
        与 vllm _WrappedParser.__init__ 的做法完全一致。

        Args:
            tokenizer: Tokenizer 实例
            tools: 工具列表（传给 tool_parser）
            **kwargs: 额外参数（如 chat_template_kwargs，传给 reasoning_parser）
        """
        self.model_tokenizer = tokenizer
        # 从类属性实例化子 parser（对齐 vllm _WrappedParser.__init__）
        self._reasoning_parser: Optional[ReasoningParser] = None
        if self.__class__.reasoning_parser_cls is not None:
            self._reasoning_parser = self.__class__.reasoning_parser_cls(
                tokenizer, **kwargs
            )
        self._tool_parser: Optional[ToolParser] = None
        if self.__class__.tool_parser_cls is not None:
            self._tool_parser = self.__class__.tool_parser_cls(
                tokenizer, tools
            )
        self._stream_state = StreamState()

    @staticmethod
    def _build_request(request: Union[Dict[str, Any], ChatCompletionRequest]) -> ChatCompletionRequest:
        """将 dict 格式的请求转换为 ChatCompletionRequest 对象

        Args:
            request: dict 格式的请求或已有的 ChatCompletionRequest 对象

        Returns:
            ChatCompletionRequest 对象
        """
        if isinstance(request, ChatCompletionRequest):
            return request

        # 从 dict 构造 ChatCompletionRequest，转换 tools 列表
        raw_tools = request.get("tools")
        tools = None
        if raw_tools:
            tools = []
            for tool in raw_tools:
                func_dict = tool.get("function", {})
                tools.append(ChatCompletionToolsParam(
                    type=tool.get("type", "function"),
                    function=FunctionDefinition(
                        name=func_dict.get("name", ""),
                        description=func_dict.get("description"),
                        parameters=func_dict.get("parameters"),
                    )
                ))

        return ChatCompletionRequest(
            messages=request.get("messages", []),
            model=request.get("model"),
            tools=tools,
            tool_choice=request.get("tool_choice", "auto"),
        )

    # ==================== 推理阶段判断（对齐 vllm DelegatingParser 接口） ====================

    def is_reasoning_end(self, input_ids: Sequence[int]) -> bool:
        """判断推理阶段是否已结束（非流式）

        对齐 vllm DelegatingParser.is_reasoning_end() 接口。
        检查完整模型输出中是否包含推理结束标记。

        Args:
            input_ids: 完整模型输出的 token IDs

        Returns:
            如果推理已结束返回 True；无 reasoning parser 时返回 False
        """
        if not self._reasoning_parser:
            return False
        return self._reasoning_parser.is_reasoning_end(input_ids)

    def is_reasoning_end_streaming(
        self,
        current_token_ids: Sequence[int],
        delta_token_ids: Sequence[int]
    ) -> bool:
        """判断推理阶段是否在当前 delta 中结束（流式）

        对齐 vllm DelegatingParser.is_reasoning_end_streaming() 接口。
        在流式场景中，根据当前累积 token_ids 和增量 token_ids 判断
        推理结束标记是否出现在最新 delta 中。

        Args:
            current_token_ids: 当前累积的全部 token IDs
            delta_token_ids: 本次增量 token IDs

        Returns:
            如果推理在此 delta 中结束返回 True；无 reasoning parser 时返回 False
        """
        if not self._reasoning_parser:
            return False
        return self._reasoning_parser.is_reasoning_end_streaming(
            current_token_ids, delta_token_ids
        )

    def extract_content_ids(self, input_ids: list) -> list:
        """从 token IDs 中提取推理结束后的内容部分

        参考 vllm ReasoningParser.extract_content_ids() 的设计。
        推理结束时，delta 中可能同时包含推理和正式内容，
        此方法去掉推理部分的 token IDs，只保留 content 部分，
        使 text 和 token_ids 保持一致。

        Args:
            input_ids: 输入 token IDs（可能包含推理标记和推理内容）

        Returns:
            仅包含 content 部分的 token IDs；无 reasoning parser 时原样返回
        """
        if not self._reasoning_parser:
            return list(input_ids)
        return self._reasoning_parser.extract_content_ids(input_ids)

    # ==================== 流式阶段判断 ====================

    def _in_reasoning_phase(self, state: StreamState) -> bool:
        if self._reasoning_parser is None:
            return False
        return not state.reasoning_ended

    def _in_tool_call_phase(self, state: StreamState) -> bool:
        if self._tool_parser is None:
            return False
        return state.reasoning_ended

    # ==================== parse_delta (对齐 vLLM DelegatingParser) ====================

    def parse_delta(
        self,
        delta_text: str,
        delta_token_ids: list,
        request: Union[Dict[str, Any], ChatCompletionRequest],
        prompt_token_ids: Optional[list] = None,
    ) -> DeltaMessage | None:
        """流式解析单个 delta，对齐 vLLM DelegatingParser.parse_delta()。

        状态管理完全在 Parser 内部，按 reasoning → tool → pass-through
        三阶段处理。每次调用后自动更新 previous_text/previous_token_ids。

        对齐 vLLM：parse_delta 不接受外部 state 参数，
        直接使用 self._stream_state（per-request 实例自带，
        无并发竞态风险）。

        Args:
            delta_text: 增量文本
            delta_token_ids: 增量 token IDs
            request: 请求对象
            prompt_token_ids: prompt token IDs（仅首次调用需要，用于判断
                              reasoning 是否已在 prompt 中结束）

        Returns:
            DeltaMessage 或 None（当 chunk 应被跳过时）
        """
        state = self._stream_state

        # Prompt reasoning check（仅首次调用）
        if not state.prompt_reasoning_checked and prompt_token_ids is not None:
            state.prompt_reasoning_checked = True
            if self._reasoning_parser is None or self.is_reasoning_end(
                prompt_token_ids
            ):
                state.reasoning_ended = True

        current_text = state.previous_text + delta_text
        current_token_ids = state.previous_token_ids + delta_token_ids
        delta_message: DeltaMessage | None = None

        # === Reasoning extraction ===
        if self._in_reasoning_phase(state):
            delta_message = self.extract_reasoning_streaming(
                previous_text=state.previous_text,
                current_text=current_text,
                delta_text=delta_text,
                previous_token_ids=state.previous_token_ids,
                current_token_ids=current_token_ids,
                delta_token_ids=delta_token_ids,
            )
            if self.is_reasoning_end_streaming(current_token_ids, delta_token_ids):
                state.reasoning_ended = True
                # 边界 delta：</think> 之后的内容仅保留给 tool parser
                current_token_ids = self.extract_content_ids(delta_token_ids)
                current_text = (
                    delta_message.content
                    if delta_message and delta_message.content
                    else ""
                )
                delta_text = current_text
                delta_token_ids = current_token_ids

        # === Tool call extraction ===
        if self._in_tool_call_phase(state):
            if not state.tool_call_text_started:
                state.tool_call_text_started = True
                state.previous_text = ""
                state.previous_token_ids = []
                delta_text = current_text
                delta_token_ids = current_token_ids

            # 保存 reasoning（边界 delta 可能同时有 reasoning 和 tool_call）
            reasoning = delta_message.reasoning if delta_message else None
            delta_message, state.function_name_returned = (
                self._extract_tool_calls_streaming(
                    previous_text=state.previous_text,
                    current_text=current_text,
                    delta_text=delta_text,
                    previous_token_ids=state.previous_token_ids,
                    current_token_ids=current_token_ids,
                    delta_token_ids=delta_token_ids,
                    request=request,
                    tool_call_idx=state.history_tool_call_cnt,
                    tool_call_id_type=state.tool_call_id_type,
                    function_name_returned=state.function_name_returned,
                )
            )
            if reasoning:
                if not delta_message:
                    delta_message = DeltaMessage()
                delta_message.reasoning = reasoning

            if (
                delta_message
                and delta_message.tool_calls
                and delta_message.tool_calls[0].id is not None
            ):
                state.history_tool_call_cnt += 1

        # === Pass-through: 无活跃阶段时，直接输出为 content ===
        if (
            delta_message is None
            and not self._in_reasoning_phase(state)
            and not self._in_tool_call_phase(state)
        ):
            delta_message = DeltaMessage(content=delta_text)

        # 更新累积状态（对齐 vLLM：使用 current_text/current_token_ids，
        # 因为边界处理后 delta_text/delta_token_ids 可能已被修改）
        state.previous_text = current_text
        state.previous_token_ids = current_token_ids
        return delta_message

    def _get_function_name(
        self, request: ChatCompletionRequest
    ) -> str:
        """从 tool_choice 中提取函数名（对齐 vLLM DelegatingParser._get_function_name）"""
        if request.tool_choice:
            if hasattr(request.tool_choice, 'function'):
                return request.tool_choice.function.name
            if hasattr(request.tool_choice, 'name'):
                return request.tool_choice.name
        return ""

    def _extract_tool_calls_streaming(
        self,
        previous_text: str,
        current_text: str,
        delta_text: str,
        previous_token_ids: list,
        current_token_ids: list,
        delta_token_ids: list,
        request: ChatCompletionRequest,
        tool_call_idx: int = 0,
        tool_call_id_type: str = "random",
        function_name_returned: bool = False,
    ) -> tuple:
        """调用底层 tool parser 的流式提取方法。

        严格对齐 vLLM DelegatingParser._extract_tool_calls_streaming()：
        先检查 supports_required_and_named，不支持的 parser（如 hermes）
        使用标准接口，不传 tool_call_idx 等扩展参数。
        """
        if self._tool_parser is None:
            return None, function_name_returned

        req = self._build_request(request)
        supports_required_and_named = getattr(
            self._tool_parser, 'supports_required_and_named', False
        )

        # Named tool choice（如 tool_choice={"function": {"name": "get_weather"}}）
        if (
            supports_required_and_named
            and req.tool_choice
            and hasattr(req.tool_choice, 'function')
        ):
            from vllm.tool_parsers.streaming import extract_named_tool_call_streaming
            delta_message, function_name_returned = extract_named_tool_call_streaming(
                delta_text=delta_text,
                function_name=self._get_function_name(req),
                function_name_returned=function_name_returned,
                tool_call_idx=tool_call_idx,
                tool_call_id_type=tool_call_id_type,
                tokenizer=getattr(self._tool_parser, 'model_tokenizer', None),
            )
            return delta_message, function_name_returned

        # Required tool choice
        if supports_required_and_named and req.tool_choice == "required":
            from vllm.tool_parsers.streaming import extract_required_tool_call_streaming
            delta_message, function_name_returned = (
                extract_required_tool_call_streaming(
                    previous_text=previous_text,
                    current_text=current_text,
                    delta_text=delta_text,
                    function_name_returned=function_name_returned,
                    tool_call_idx=tool_call_idx,
                    tool_call_id_type=tool_call_id_type,
                )
            )
            return delta_message, function_name_returned

        # 标准接口（hermes 等 parser）：仅传基础参数
        return self.extract_tool_calls_streaming(
            previous_text=previous_text,
            current_text=current_text,
            delta_text=delta_text,
            previous_token_ids=previous_token_ids,
            current_token_ids=current_token_ids,
            delta_token_ids=delta_token_ids,
            request=req,  # type: ignore[arg-type]
        ), function_name_returned

    # ==================== Tool Parser 接口 ====================

    def extract_tool_calls(
        self,
        model_output: str,
        request: Union[Dict[str, Any], ChatCompletionRequest],
    ) -> ExtractedToolCallInformation:
        """提取工具调用（非流式）

        Args:
            model_output: 模型输出文本
            request: dict 格式的请求或 ChatCompletionRequest 对象
        """
        if not self._tool_parser:
            return ExtractedToolCallInformation(
                tools_called=False,
                tool_calls=[],
                content=model_output
            )
        req = self._build_request(request)
        return self._tool_parser.extract_tool_calls(model_output, req)

    def extract_tool_calls_streaming(
        self,
        previous_text: str,
        current_text: str,
        delta_text: str,
        previous_token_ids: Sequence[int],
        current_token_ids: Sequence[int],
        delta_token_ids: Sequence[int],
        request: Union[Dict[str, Any], ChatCompletionRequest],
    ) -> DeltaMessage | None:
        """提取工具调用（流式）

        Args:
            request: dict 格式的请求或 ChatCompletionRequest 对象
        """
        if not self._tool_parser:
            return None
        req = self._build_request(request)
        return self._tool_parser.extract_tool_calls_streaming(
            previous_text=previous_text,
            current_text=current_text,
            delta_text=delta_text,
            previous_token_ids=previous_token_ids,
            current_token_ids=current_token_ids,
            delta_token_ids=delta_token_ids,
            request=req,
        )

    # ==================== Reasoning Parser 接口 ====================

    def extract_reasoning(
        self,
        model_output: str,
        request: Union[Dict[str, Any], ChatCompletionRequest],
    ) -> Tuple[Optional[str], Optional[str]]:
        """提取推理内容（非流式）

        Args:
            model_output: 模型输出文本
            request: dict 格式的请求或 ChatCompletionRequest 对象

        Returns:
            (reasoning, content) 元组
        """
        if not self._reasoning_parser:
            return None, model_output
        req = self._build_request(request)
        return self._reasoning_parser.extract_reasoning(model_output, req)

    def extract_reasoning_streaming(
        self,
        previous_text: str,
        current_text: str,
        delta_text: str,
        previous_token_ids: Sequence[int],
        current_token_ids: Sequence[int],
        delta_token_ids: Sequence[int],
    ) -> DeltaMessage | None:
        """提取推理内容（流式）

        对齐 vllm DelegatingParser.extract_reasoning_streaming():
        无 reasoning parser 时，透传为 content（返回 DeltaMessage(content=delta_text)),
        不返回 None，避免流式场景下 content 丢失。
        """
        if self._reasoning_parser is None:
            return DeltaMessage(content=delta_text)
        return self._reasoning_parser.extract_reasoning_streaming(
            previous_text=previous_text,
            current_text=current_text,
            delta_text=delta_text,
            previous_token_ids=previous_token_ids,
            current_token_ids=current_token_ids,
            delta_token_ids=delta_token_ids,
        )

    # ==================== 属性访问 ====================

    @property
    def has_tool_parser(self) -> bool:
        """是否有 Tool Parser"""
        return self._tool_parser is not None

    @property
    def has_reasoning_parser(self) -> bool:
        """是否有 Reasoning Parser"""
        return self._reasoning_parser is not None

    @property
    def tool_parser(self) -> Optional[ToolParser]:
        """获取底层 Tool Parser（高级用法）"""
        return self._tool_parser

    @property
    def reasoning_parser(self) -> Optional[ReasoningParser]:
        """获取底层 Reasoning Parser（高级用法）"""
        return self._reasoning_parser


class ParserManager:
    """统一的 Parser 管理器

    提供一站式接口获取和创建 Parser 实例。
    支持从自定义目录按需发现和加载 parser。

    使用示例：
        # 获取 parser 类
        tool_parser_cls = ParserManager.get_tool_parser_cls("qwen3_coder")

        # 创建 parser 实例
        tokenizer = AutoTokenizer.from_pretrained("Qwen/Qwen3-Coder-30B-A3B-Instruct")
        tool_parser = tool_parser_cls(tokenizer)

        # 使用 parser
        result = tool_parser.extract_tool_calls(model_output, request)

    自定义 parser 按需发现：
        1. 在 custom_parsers/tool_parsers/ 目录下放置 parser 文件
        2. 文件名即为 parser 名称（如 my_parser.py -> "my_parser"）
        3. 首次请求时自动发现并加载
    """

    @classmethod
    def _try_load_custom_tool_parser(cls, name: str) -> Optional[type]:
        """从自定义目录加载 Tool Parser

        Args:
            name: Parser 名称

        Returns:
            Parser 类，如果未找到则返回 None
        """
        custom_tool_parsers_dir, _ = _get_custom_parsers_dirs()
        parser_file = custom_tool_parsers_dir / f"{name}.py"
        if not parser_file.exists():
            return None

        _logger.info(f"从自定义目录加载 Tool Parser: {parser_file}")

        try:
            spec = importlib.util.spec_from_file_location(f"custom_tool_parser.{name}", parser_file)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[f"custom_tool_parser.{name}"] = module
                spec.loader.exec_module(module)

                # 查找 ToolParser 子类（排除导入的基类）
                # 只查找在当前模块中定义的类，通过检查 __module__ 属性
                expected_module_name = f"custom_tool_parser.{name}"
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if isinstance(attr, type) and issubclass(attr, ToolParser) and attr is not ToolParser:
                        # 检查类是否在当前模块中定义（排除导入的基类）
                        if hasattr(attr, '__module__') and attr.__module__ == expected_module_name:
                            # 注册到管理器
                            ToolParserManager.register_module(name, module=attr)
                            _logger.info(f"成功注册自定义 Tool Parser: {name} -> {attr.__name__}")
                            return attr
        except Exception as e:
            _logger.error(f"加载自定义 Tool Parser 失败: {name}, 错误: {e}")

        return None

    @classmethod
    def _try_load_custom_reasoning_parser(cls, name: str) -> Optional[type]:
        """从自定义目录加载 Reasoning Parser

        Args:
            name: Parser 名称

        Returns:
            Parser 类，如果未找到则返回 None
        """
        _, custom_reasoning_parsers_dir = _get_custom_parsers_dirs()
        parser_file = custom_reasoning_parsers_dir / f"{name}.py"
        if not parser_file.exists():
            return None

        _logger.info(f"从自定义目录加载 Reasoning Parser: {parser_file}")

        try:
            spec = importlib.util.spec_from_file_location(f"custom_reasoning_parser.{name}", parser_file)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[f"custom_reasoning_parser.{name}"] = module
                spec.loader.exec_module(module)

                # 查找 ReasoningParser 子类（排除导入的基类）
                # 只查找在当前模块中定义的类，通过检查 __module__ 属性
                expected_module_name = f"custom_reasoning_parser.{name}"
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if isinstance(attr, type) and issubclass(attr, ReasoningParser) and attr is not ReasoningParser:
                        # 检查类是否在当前模块中定义（排除导入的基类）
                        if hasattr(attr, '__module__') and attr.__module__ == expected_module_name:
                            # 注册到管理器
                            ReasoningParserManager.register_module(name, module=attr)
                            _logger.info(f"成功注册自定义 Reasoning Parser: {name} -> {attr.__name__}")
                            return attr
        except Exception as e:
            _logger.error(f"加载自定义 Reasoning Parser 失败: {name}, 错误: {e}")

        return None

    @classmethod
    def create_parser(
        cls,
        tool_parser_name: Optional[str],
        reasoning_parser_name: Optional[str],
        tokenizer: Any,
    ) -> Tuple[type, Parser]:
        """创建动态子类和共享 Parser 实例（对齐 vllm ParserManager.get_parser）

        对齐 vllm ParserManager.get_parser() 的动态子类化模式：
        通过 type() 创建 Parser 子类，将 reasoning_parser_cls 和
        tool_parser_cls 设置为子类的类属性，与 vllm 的做法完全一致：
        - vllm: _WrappedParser.reasoning_parser_cls = reasoning_parser_cls
        - vllm: _WrappedParser.tool_parser_cls = tool_parser_cls
        TrajProxy 使用 type() 创建子类，每个模型配置有独立的子类，
        避免多个模型共享同一 Parser 基类导致类属性冲突。

        返回 (parser_cls, parser_instance)：
        - parser_cls: 动态创建的 Parser 子类（类引用），供流式路径
          parser_cls(tokenizer, tools, **kwargs) 创建 per-request 实例
        - parser_instance: 共享 Parser 实例，供非流式路径使用

        Args:
            tool_parser_name: Tool Parser 名称（None 或空字符串表示不使用）
            reasoning_parser_name: Reasoning Parser 名称（None 或空字符串表示不使用）
            tokenizer: Tokenizer 实例（transformers.PreTrainedTokenizerBase）

        Returns:
            (parser_cls, parser_instance) 元组：
            - parser_cls: Parser 子类（类引用，含 reasoning_parser_cls /
              tool_parser_cls 类属性）
            - parser_instance: 共享 Parser 实例（非流式路径使用）
        """
        # 确保兼容层已初始化（自动发现和注册）
        ensure_initialized()

        tool_parser_cls = None
        reasoning_parser_cls = None

        if tool_parser_name:
            # 使用 get_tool_parser_cls 以支持按需发现
            tool_parser_cls = cls.get_tool_parser_cls(tool_parser_name)

        if reasoning_parser_name:
            # 使用 get_reasoning_parser_cls 以支持按需发现
            reasoning_parser_cls = cls.get_reasoning_parser_cls(reasoning_parser_name)

        if tool_parser_cls is None and reasoning_parser_cls is None:
            # 无 parser 配置：返回 Parser 基类和空实例
            return Parser, Parser(tokenizer=tokenizer, tools=None)

        # 对齐 vllm ParserManager.get_parser(): 动态创建子类
        # vllm 在 _WrappedParser 上设置类属性（全局共享）
        # TrajProxy 使用 type() 创建子类（每个模型配置独立）
        # 这确保不同模型配置的 parser_cls 不会互相干扰
        reasoning_name = reasoning_parser_name or "NoReasoning"
        tool_name = tool_parser_name or "NoTool"
        class_name = f"{reasoning_name}_{tool_name}_Parser"

        parser_cls = type(class_name, (Parser,), {
            "reasoning_parser_cls": reasoning_parser_cls,
            "tool_parser_cls": tool_parser_cls,
        })

        # 创建共享 Parser 实例（非流式路径使用）
        # __init__ 会从类属性自动实例化子 parser
        parser_instance = parser_cls(tokenizer=tokenizer, tools=None)

        return parser_cls, parser_instance

    @classmethod
    def create_parsers(
        cls,
        tool_parser_name: Optional[str],
        reasoning_parser_name: Optional[str],
        tokenizer: Any,
    ) -> Tuple[Optional[ToolParser], Optional[ReasoningParser]]:
        """创建原始 vLLM Parser 实例（向后兼容）

        注意：推荐使用 create_parser() 获取统一的 Parser 接口。

        Args:
            tool_parser_name: Tool Parser 名称
            reasoning_parser_name: Reasoning Parser 名称
            tokenizer: Tokenizer 实例

        Returns:
            (tool_parser, reasoning_parser) 元组
        """
        _, parser = cls.create_parser(tool_parser_name, reasoning_parser_name, tokenizer)
        return parser.tool_parser, parser.reasoning_parser

    @classmethod
    def get_tool_parser_cls(cls, name: str) -> Optional[type]:
        """获取 Tool Parser 类

        按需发现机制：如果默认注册表中没有，则从自定义目录查找并加载。

        Args:
            name: Parser 名称（如 "qwen3_coder"）

        Returns:
            Parser 类，如果未找到则返回 None
        """
        ensure_initialized()
        try:
            _logger.info(f"get_tool_parser_cls: {name}")
            return ToolParserManager.get_tool_parser(name)
        except KeyError:
            # 默认注册表中没有，尝试从自定义目录加载
            _logger.info(f"get_tool_parser_cls _try_load_custom_tool_parser: {name}")
            return cls._try_load_custom_tool_parser(name)

    @classmethod
    def get_reasoning_parser_cls(cls, name: str) -> Optional[type]:
        """获取 Reasoning Parser 类

        按需发现机制：如果默认注册表中没有，则从自定义目录查找并加载。

        Args:
            name: Parser 名称（如 "qwen3"）

        Returns:
            Parser 类，如果未找到则返回 None
        """
        ensure_initialized()
        try:
            _logger.info(f"get_reasoning_parser_cls: {name}")
            return ReasoningParserManager.get_reasoning_parser(name)
        except KeyError:
            # 默认注册表中没有，尝试从自定义目录加载
            _logger.info(f"get_reasoning_parser_cls _try_load_custom_reasoning_parser: {name}")
            return cls._try_load_custom_reasoning_parser(name)

    @classmethod
    def list_tool_parsers(cls) -> List[str]:
        """列出所有已注册的 Tool Parser 名称"""
        ensure_initialized()
        return ToolParserManager.list_registered()

    @classmethod
    def list_reasoning_parsers(cls) -> List[str]:
        """列出所有已注册的 Reasoning Parser 名称"""
        ensure_initialized()
        return ReasoningParserManager.list_registered()

    @classmethod
    def create_tool_parser(cls, name: str, tokenizer: Any) -> Optional[ToolParser]:
        """创建 Tool Parser 实例

        Args:
            name: Parser 名称
            tokenizer: Tokenizer 实例

        Returns:
            Parser 实例，如果未找到则返回 None
        """
        parser_cls = cls.get_tool_parser_cls(name)
        if parser_cls:
            return parser_cls(tokenizer=tokenizer)
        return None

    @classmethod
    def create_reasoning_parser(cls, name: str, tokenizer: Any) -> Optional[ReasoningParser]:
        """创建 Reasoning Parser 实例

        Args:
            name: Parser 名称
            tokenizer: Tokenizer 实例

        Returns:
            Parser 实例，如果未找到则返回 None
        """
        parser_cls = cls.get_reasoning_parser_cls(name)
        if parser_cls:
            return parser_cls(tokenizer=tokenizer)
        return None


# 便捷函数
get_tool_parser = ParserManager.get_tool_parser_cls
get_reasoning_parser = ParserManager.get_reasoning_parser_cls
create_parser = ParserManager.create_parser
create_tool_parser = ParserManager.create_tool_parser
create_reasoning_parser = ParserManager.create_reasoning_parser
list_tool_parsers = ParserManager.list_tool_parsers
list_reasoning_parsers = ParserManager.list_reasoning_parsers


__all__ = [
    "Parser",
    "ParserManager",
    "ToolParser",
    "ReasoningParser",
    "ToolParserManager",
    "ReasoningParserManager",
    "get_tool_parser",
    "get_reasoning_parser",
    "create_parser",
    "create_tool_parser",
    "create_reasoning_parser",
    "list_tool_parsers",
    "list_reasoning_parsers",
]
