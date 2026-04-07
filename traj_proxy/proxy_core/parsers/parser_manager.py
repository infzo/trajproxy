# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project

"""
Parser 管理器

统一管理 Tool Parser 和 Reasoning Parser 的创建和获取。
"""
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


class Parser:
    """Parser 统一接口（适配器模式）

    对外提供统一接口，对内适配 vLLM parser。
    支持 vLLM 原始 parser 无需修改即可使用。

    使用示例：
        parser = ParserManager.create_parser(...)
        with parser:  # 自动重置流式状态
            async for chunk in ...:
                delta = parser.extract_tool_calls_streaming(...)
    """

    def __init__(
        self,
        tool_parser: Optional[ToolParser],
        reasoning_parser: Optional[ReasoningParser],
    ):
        self._tool_parser = tool_parser
        self._reasoning_parser = reasoning_parser

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

    # ==================== 流式状态管理 ====================

    def reset_streaming_state(self):
        """重置流式解析状态（兼容 vLLM 原始 parser）"""
        # 使用 hasattr 检查，适配不同的 vLLM parser 实现
        if self._tool_parser and hasattr(self._tool_parser, 'reset_streaming_state'):
            self._tool_parser.reset_streaming_state()
        if self._reasoning_parser and hasattr(self._reasoning_parser, 'reset_streaming_state'):
            self._reasoning_parser.reset_streaming_state()

    def __enter__(self):
        """进入上下文管理器，自动重置流式状态"""
        self.reset_streaming_state()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出上下文管理器"""
        return False

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
        """提取推理内容（流式）"""
        if not self._reasoning_parser:
            return None
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

    使用示例：
        # 获取 parser 类
        tool_parser_cls = ParserManager.get_tool_parser_cls("qwen3_coder")

        # 创建 parser 实例
        tokenizer = AutoTokenizer.from_pretrained("Qwen/Qwen3-Coder-30B-A3B-Instruct")
        tool_parser = tool_parser_cls(tokenizer)

        # 使用 parser
        result = tool_parser.extract_tool_calls(model_output, request)
    """

    @classmethod
    def create_parser(
        cls,
        tool_parser_name: Optional[str],
        reasoning_parser_name: Optional[str],
        tokenizer: Any,
    ) -> Parser:
        """创建 Parser 实例（统一接口）

        Args:
            tool_parser_name: Tool Parser 名称（None 或空字符串表示不使用）
            reasoning_parser_name: Reasoning Parser 名称（None 或空字符串表示不使用）
            tokenizer: Tokenizer 实例（transformers.PreTrainedTokenizerBase）

        Returns:
            Parser 实例（统一接口，适配 vLLM parser）
        """
        # 确保兼容层已初始化（自动发现和注册）
        ensure_initialized()

        tool_parser = None
        reasoning_parser = None

        if tool_parser_name:
            parser_cls = ToolParserManager.get_tool_parser(tool_parser_name)
            if parser_cls:
                tool_parser = parser_cls(tokenizer=tokenizer)

        if reasoning_parser_name:
            parser_cls = ReasoningParserManager.get_reasoning_parser(reasoning_parser_name)
            if parser_cls:
                reasoning_parser = parser_cls(tokenizer=tokenizer)

        return Parser(tool_parser, reasoning_parser)

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
        parser = cls.create_parser(tool_parser_name, reasoning_parser_name, tokenizer)
        return parser.tool_parser, parser.reasoning_parser

    @classmethod
    def get_tool_parser_cls(cls, name: str) -> Optional[type]:
        """获取 Tool Parser 类

        Args:
            name: Parser 名称（如 "qwen3_coder"）

        Returns:
            Parser 类，如果未找到则返回 None
        """
        ensure_initialized()
        try:
            return ToolParserManager.get_tool_parser(name)
        except KeyError:
            return None

    @classmethod
    def get_reasoning_parser_cls(cls, name: str) -> Optional[type]:
        """获取 Reasoning Parser 类

        Args:
            name: Parser 名称（如 "qwen3"）

        Returns:
            Parser 类，如果未找到则返回 None
        """
        ensure_initialized()
        try:
            return ReasoningParserManager.get_reasoning_parser(name)
        except KeyError:
            return None

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
