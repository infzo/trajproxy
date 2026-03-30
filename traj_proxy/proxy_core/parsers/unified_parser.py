"""
统一 Parser 实现
参考 vLLM 0.16.0 的 Parser 设计

提供统一的接口，同时支持 reasoning 和 tool 解析。
支持三种模式：
1. 直接继承 Parser 实现自定义解析逻辑
2. 使用 DelegatingParser 组合独立的 reasoning/tool parser
3. 使用 IdentityParser 不做解析直接返回（默认行为）
"""

import json
import uuid
from collections.abc import Sequence
from typing import Optional, List, Any, Type

from .base import (
    BaseParser,
    BaseToolParser,
    BaseReasoningParser,
    ToolCall,
    DeltaMessage,
    DeltaToolCall,
    ExtractedToolCallInfo,
)


def make_tool_call_id(id_type: str = "random", func_name: str = None, idx: int = None) -> str:
    """
    生成工具调用 ID

    Args:
        id_type: ID 类型 ("random" 或 "index")
        func_name: 函数名（可选）
        idx: 索引（用于 index 类型）

    Returns:
        工具调用 ID 字符串
    """
    if id_type == "index" and idx is not None:
        return f"call_{idx}"
    return f"call_{uuid.uuid4().hex[:24]}"


class IdentityParser(BaseParser):
    """
    恒等 Parser（默认行为）

    不做任何解析，直接返回原内容。
    当 reasoning_parser 或 tool_parser 未指定时使用。
    """

    def is_reasoning_end(self, input_ids: List[int]) -> bool:
        """不做解析，返回 True"""
        return True

    def extract_content_ids(self, input_ids: List[int]) -> List[int]:
        """返回原 token IDs"""
        return input_ids

    def extract_reasoning(
        self,
        model_output: str,
        request: Optional[Any] = None
    ) -> tuple[Optional[str], Optional[str]]:
        """不做解析，返回 (None, 原内容)"""
        return None, model_output

    def extract_reasoning_streaming(
        self,
        previous_text: str,
        current_text: str,
        delta_text: str,
        previous_token_ids: Sequence[int],
        current_token_ids: Sequence[int],
        delta_token_ids: Sequence[int]
    ) -> Optional[DeltaMessage]:
        """直接返回原内容"""
        return DeltaMessage(content=delta_text)

    def extract_tool_calls(
        self,
        model_output: str,
        tools: Optional[List[dict]] = None,
        request: Optional[Any] = None
    ) -> ExtractedToolCallInfo:
        """不做解析，返回 (False, [], 原内容)"""
        return ExtractedToolCallInfo(
            tools_called=False,
            tool_calls=[],
            content=model_output
        )

    def extract_tool_calls_streaming(
        self,
        previous_text: str,
        current_text: str,
        delta_text: str,
        previous_token_ids: Sequence[int],
        current_token_ids: Sequence[int],
        delta_token_ids: Sequence[int],
        tools: Optional[List[dict]] = None,
        request: Optional[Any] = None
    ) -> Optional[DeltaMessage]:
        """直接返回原内容"""
        return DeltaMessage(content=delta_text)


class Parser(BaseParser):
    """
    统一 Parser 抽象类

    同时支持 reasoning 提取和 tool call 解析。
    子类可以：
    1. 直接重写抽象方法实现自定义解析逻辑
    2. 设置 reasoning_parser 和 tool_parser 属性委托给现有实现
    """

    def is_reasoning_end(self, input_ids: List[int]) -> bool:
        """检查推理内容是否结束"""
        if self._reasoning_parser is None:
            return True
        return self._reasoning_parser.is_reasoning_end(input_ids)

    def extract_content_ids(self, input_ids: List[int]) -> List[int]:
        """提取非推理内容的 token IDs"""
        if self._reasoning_parser is None:
            return input_ids
        return self._reasoning_parser.extract_content_ids(input_ids)

    def extract_reasoning(
        self,
        model_output: str,
        request: Optional[Any] = None
    ) -> tuple[Optional[str], Optional[str]]:
        """非流式解析推理内容"""
        if self._reasoning_parser is None:
            return None, model_output
        return self._reasoning_parser.extract_reasoning(model_output, request)

    def extract_reasoning_streaming(
        self,
        previous_text: str,
        current_text: str,
        delta_text: str,
        previous_token_ids: Sequence[int],
        current_token_ids: Sequence[int],
        delta_token_ids: Sequence[int]
    ) -> Optional[DeltaMessage]:
        """流式解析推理内容"""
        if self._reasoning_parser is None:
            return DeltaMessage(content=delta_text)
        return self._reasoning_parser.extract_reasoning_streaming(
            previous_text,
            current_text,
            delta_text,
            previous_token_ids,
            current_token_ids,
            delta_token_ids,
        )

    def extract_tool_calls(
        self,
        model_output: str,
        tools: Optional[List[dict]] = None,
        request: Optional[Any] = None
    ) -> ExtractedToolCallInfo:
        """非流式解析工具调用"""
        if self._tool_parser is None:
            return ExtractedToolCallInfo(
                tools_called=False,
                tool_calls=[],
                content=model_output
            )
        return self._tool_parser.extract_tool_calls(model_output, tools, request)

    def extract_tool_calls_streaming(
        self,
        previous_text: str,
        current_text: str,
        delta_text: str,
        previous_token_ids: Sequence[int],
        current_token_ids: Sequence[int],
        delta_token_ids: Sequence[int],
        tools: Optional[List[dict]] = None,
        request: Optional[Any] = None
    ) -> Optional[DeltaMessage]:
        """流式解析工具调用"""
        if self._tool_parser is None:
            return DeltaMessage(content=delta_text)
        return self._tool_parser.extract_tool_calls_streaming(
            previous_text,
            current_text,
            delta_text,
            previous_token_ids,
            current_token_ids,
            delta_token_ids,
            tools,
            request,
        )


class DelegatingParser(Parser):
    """
    委托模式 Parser

    将 reasoning 和 tool 解析委托给独立的 parser 实例。
    这是创建模型特定 parser 的推荐基类。

    子类应在 __init__ 中设置 self._reasoning_parser 和 self._tool_parser。
    如果任一 parser 为 None，对应方法将返回默认值（不做解析）。
    """

    def extract_reasoning(
        self,
        model_output: str,
        request: Optional[Any] = None
    ) -> tuple[Optional[str], Optional[str]]:
        if self._reasoning_parser is None:
            return None, model_output
        return self._reasoning_parser.extract_reasoning(model_output, request)

    def extract_tool_calls(
        self,
        model_output: str,
        tools: Optional[List[dict]] = None,
        request: Optional[Any] = None
    ) -> ExtractedToolCallInfo:
        if self._tool_parser is None:
            return ExtractedToolCallInfo(
                tools_called=False,
                tool_calls=[],
                content=model_output
            )
        return self._tool_parser.extract_tool_calls(model_output, tools, request)

    def extract_reasoning_streaming(
        self,
        previous_text: str,
        current_text: str,
        delta_text: str,
        previous_token_ids: Sequence[int],
        current_token_ids: Sequence[int],
        delta_token_ids: Sequence[int]
    ) -> Optional[DeltaMessage]:
        if self._reasoning_parser is None:
            return DeltaMessage(content=delta_text)
        return self._reasoning_parser.extract_reasoning_streaming(
            previous_text,
            current_text,
            delta_text,
            previous_token_ids,
            current_token_ids,
            delta_token_ids,
        )

    def extract_tool_calls_streaming(
        self,
        previous_text: str,
        current_text: str,
        delta_text: str,
        previous_token_ids: Sequence[int],
        current_token_ids: Sequence[int],
        delta_token_ids: Sequence[int],
        tools: Optional[List[dict]] = None,
        request: Optional[Any] = None
    ) -> Optional[DeltaMessage]:
        if self._tool_parser is None:
            return DeltaMessage(content=delta_text)
        return self._tool_parser.extract_tool_calls_streaming(
            previous_text,
            current_text,
            delta_text,
            previous_token_ids,
            current_token_ids,
            delta_token_ids,
            tools,
            request,
        )


class _WrappedParser(DelegatingParser):
    """
    动态包装 Parser

    通过构造函数参数接收底层 parser 类，避免类属性的并发安全问题。
    用于动态创建组合 reasoning 和 tool parser 的 parser。

    注意：不应直接使用此类，应通过 ParserManager.create_parser 创建。
    """

    def __init__(
        self,
        tokenizer=None,
        reasoning_parser_cls: Optional[Type[BaseReasoningParser]] = None,
        tool_parser_cls: Optional[Type[BaseToolParser]] = None,
        **kwargs
    ):
        """
        初始化动态包装 Parser

        Args:
            tokenizer: tokenizer 实例
            reasoning_parser_cls: Reasoning Parser 类（可选）
            tool_parser_cls: Tool Parser 类（可选）
            **kwargs: 其他参数
        """
        super().__init__(tokenizer, **kwargs)
        # 从参数实例化底层 parser（避免类属性的并发问题）
        if reasoning_parser_cls is not None:
            self._reasoning_parser = reasoning_parser_cls(tokenizer, **kwargs)
        if tool_parser_cls is not None:
            self._tool_parser = tool_parser_cls(tokenizer, **kwargs)
