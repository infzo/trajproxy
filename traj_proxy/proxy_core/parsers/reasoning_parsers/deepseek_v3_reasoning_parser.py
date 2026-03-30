"""
DeepSeek V3 Reasoning Parser

条件解析器：根据 thinking 参数切换行为
- thinking=True: 使用 DeepSeekR1ReasoningParser
- thinking=False: 使用 IdentityReasoningParser
"""

from collections.abc import Sequence
from typing import Optional, Any, Dict

from ..base import DeltaMessage
from .basic_parsers import BaseThinkingReasoningParser, IdentityReasoningParser


class DeepSeekV3ReasoningParser(BaseThinkingReasoningParser):
    """
    DeepSeek V3 条件推理解析器

    根据 chat_template_kwargs 中的 thinking 参数决定使用哪种解析器：
    - thinking=True (或 enable_thinking=True): 使用 DeepSeekR1ReasoningParser
    - thinking=False: 使用 IdentityReasoningParser
    """

    @property
    def start_token(self) -> str:
        """推理开始标记"""
        if hasattr(self, '_parser') and self._parser:
            return self._parser.start_token
        return "<thinky>"

    @property
    def end_token(self) -> str:
        """推理结束标记"""
        if hasattr(self, '_parser') and self._parser:
            return self._parser.end_token
        return "</thinke>"

    def __init__(self, tokenizer=None, **kwargs):
        # 先初始化基类（但不调用完整的初始化逻辑）
        self.tokenizer = tokenizer
        self._kwargs = kwargs

        # 获取 thinking 参数
        chat_kwargs = kwargs.get("chat_template_kwargs", {}) or {}
        thinking = bool(chat_kwargs.get("thinking", False))
        enable_thinking = bool(chat_kwargs.get("enable_thinking", False))
        thinking = thinking or enable_thinking

        # 选择底层解析器
        if thinking:
            from .deepseek_r1_reasoning_parser import DeepSeekR1ReasoningParser
            self._parser = DeepSeekR1ReasoningParser(tokenizer, **kwargs)
        else:
            self._parser = IdentityReasoningParser(tokenizer, **kwargs)

        # 同步 token IDs
        self._start_token_id = getattr(self._parser, '_start_token_id', None)
        self._end_token_id = getattr(self._parser, '_end_token_id', None)

    def is_reasoning_end(self, input_ids: Sequence[int]) -> bool:
        return self._parser.is_reasoning_end(input_ids)

    def is_reasoning_end_streaming(
        self,
        input_ids: Sequence[int],
        delta_ids: Sequence[int]
    ) -> bool:
        return self._parser.is_reasoning_end_streaming(input_ids, delta_ids)

    def extract_content_ids(self, input_ids: list[int]) -> list[int]:
        return self._parser.extract_content_ids(input_ids)

    def extract_reasoning(
        self,
        model_output: str,
        request: Optional[Any] = None
    ) -> tuple[Optional[str], Optional[str]]:
        return self._parser.extract_reasoning(model_output, request)

    def extract_reasoning_streaming(
        self,
        previous_text: str,
        current_text: str,
        delta_text: str,
        previous_token_ids: Sequence[int],
        current_token_ids: Sequence[int],
        delta_token_ids: Sequence[int]
    ) -> Optional[DeltaMessage]:
        return self._parser.extract_reasoning_streaming(
            previous_text,
            current_text,
            delta_text,
            previous_token_ids,
            current_token_ids,
            delta_token_ids,
        )


class DeepSeekV3ReasoningWithThinkingParser(DeepSeekV3ReasoningParser):
    """
    默认启用 thinking 模式的 DeepSeek V3 解析器

    用于 glm45、holo2、kimi_k2 等复用 DeepSeek V3 格式的模型。
    """

    def __init__(self, tokenizer=None, **kwargs):
        chat_kwargs = kwargs.get("chat_template_kwargs", {}) or {}
        thinking = chat_kwargs.get("thinking", None)
        enable_thinking = chat_kwargs.get("enable_thinking", None)
        if thinking is None and enable_thinking is None:
            chat_kwargs["thinking"] = True
            chat_kwargs["enable_thinking"] = True
            kwargs["chat_template_kwargs"] = chat_kwargs
        super().__init__(tokenizer, **kwargs)
