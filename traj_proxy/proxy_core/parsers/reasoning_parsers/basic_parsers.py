"""
基础 Reasoning Parser 实现
参考 vLLM 0.16.0 的 BaseThinkingReasoningParser 设计

提供处理 start_token/end_token 的通用实现。
"""

from abc import abstractmethod
from collections.abc import Sequence
from typing import Optional, List, Any

from ..base import BaseReasoningParser, DeltaMessage


class BaseThinkingReasoningParser(BaseReasoningParser):
    """
    基于 thinking tokens 的 Reasoning Parser 基类

    子类只需实现 start_token 和 end_token 属性。
    适用于使用固定标记包围推理内容的模型（如 <thinky></thinke>）。
    """

    @property
    @abstractmethod
    def start_token(self) -> str:
        """推理开始标记"""
        raise NotImplementedError

    @property
    @abstractmethod
    def end_token(self) -> str:
        """推理结束标记"""
        raise NotImplementedError

    def __init__(self, tokenizer=None, **kwargs):
        super().__init__(tokenizer, **kwargs)

        if self.tokenizer is None:
            # 允许无 tokenizer 运行（用于测试）
            self._start_token_id = None
            self._end_token_id = None
        else:
            # 获取 token IDs
            self._start_token_id = self.vocab.get(self.start_token)
            self._end_token_id = self.vocab.get(self.end_token)

            # 警告但不报错（某些模型可能没有这些 token）
            if self._start_token_id is None or self._end_token_id is None:
                import warnings
                warnings.warn(
                    f"{self.__class__.__name__} reasoning parser could not locate "
                    f"think start/end tokens in the tokenizer. "
                    f"start_token_id={self._start_token_id}, end_token_id={self._end_token_id}"
                )

    @property
    def start_token_id(self) -> Optional[int]:
        """获取开始 token ID"""
        return self._start_token_id

    @property
    def end_token_id(self) -> Optional[int]:
        """获取结束 token ID"""
        return self._end_token_id

    def is_reasoning_end(self, input_ids: Sequence[int]) -> bool:
        """
        检查推理内容是否结束

        从后向前查找，如果先遇到 end_token 则返回 True，
        如果先遇到 start_token 则返回 False。
        """
        if self._end_token_id is None or self._start_token_id is None:
            return False

        for i in range(len(input_ids) - 1, -1, -1):
            if input_ids[i] == self._start_token_id:
                return False
            if input_ids[i] == self._end_token_id:
                return True
        return False

    def is_reasoning_end_streaming(
        self,
        input_ids: Sequence[int],
        delta_ids: Sequence[int]
    ) -> bool:
        """
        流式模式下检查推理是否结束

        检查本次增量中是否包含 end_token。
        """
        if self._end_token_id is None:
            return False
        return self._end_token_id in delta_ids

    def extract_content_ids(self, input_ids: List[int]) -> List[int]:
        """
        提取非推理内容的 token IDs

        返回 end_token 之后的所有 token IDs。
        """
        if self._end_token_id is None or self._end_token_id not in input_ids[:-1]:
            return []
        try:
            end_idx = input_ids.index(self._end_token_id)
            return input_ids[end_idx + 1:]
        except ValueError:
            return []

    def extract_reasoning(
        self,
        model_output: str,
        request: Optional[Any] = None
    ) -> tuple[Optional[str], Optional[str]]:
        """
        非流式解析推理内容

        基本实现：
        1. 移除 start_token 之前的内容
        2. 以 end_token 为界分割 reasoning 和 content

        Returns:
            (reasoning, content): 推理内容和剩余内容
        """
        # 移除 start_token 之前的内容
        if self.start_token in model_output:
            parts = model_output.split(self.start_token, 1)
            model_output = parts[1] if len(parts) > 1 else ""

        # 分割 reasoning 和 content
        if self.end_token not in model_output:
            # 没有结束标记，全部作为推理内容
            return model_output if model_output else None, None
        else:
            reasoning, _, content = model_output.partition(self.end_token)
            return reasoning if reasoning else None, content if content else None

    def extract_reasoning_streaming(
        self,
        previous_text: str,
        current_text: str,
        delta_text: str,
        previous_token_ids: Sequence[int],
        current_token_ids: Sequence[int],
        delta_token_ids: Sequence[int]
    ) -> Optional[DeltaMessage]:
        """
        流式解析推理内容

        使用 token IDs 进行精确判断。
        """
        # 跳过单独的特殊 token
        if len(delta_token_ids) == 1 and (
            delta_token_ids[0] in [self._start_token_id, self._end_token_id]
        ):
            return None

        start_in_previous = (
            self._start_token_id is not None and
            self._start_token_id in previous_token_ids
        )
        start_in_delta = (
            self._start_token_id is not None and
            self._start_token_id in delta_token_ids
        )
        end_in_delta = (
            self._end_token_id is not None and
            self._end_token_id in delta_token_ids
        )
        end_in_previous = (
            self._end_token_id is not None and
            self._end_token_id in previous_token_ids
        )

        if start_in_previous:
            if end_in_delta:
                # start 在之前，end 在本次增量
                end_index = delta_text.find(self.end_token)
                reasoning = delta_text[:end_index]
                content = delta_text[end_index + len(self.end_token):]
                return DeltaMessage(
                    reasoning=reasoning,
                    content=content if content else None
                )
            elif end_in_previous:
                # start 和 end 都在之前，普通内容
                return DeltaMessage(content=delta_text)
            else:
                # 还在推理内容中
                return DeltaMessage(reasoning=delta_text)
        elif start_in_delta:
            if end_in_delta:
                # start 和 end 都在本次增量
                start_index = delta_text.find(self.start_token)
                end_index = delta_text.find(self.end_token)
                reasoning = delta_text[start_index + len(self.start_token):end_index]
                content = delta_text[end_index + len(self.end_token):]
                return DeltaMessage(
                    reasoning=reasoning,
                    content=content if content else None
                )
            else:
                # start 在本次增量，end 未出现
                return DeltaMessage(reasoning=delta_text)
        else:
            # 未进入推理区域
            return DeltaMessage(content=delta_text)


class IdentityReasoningParser(BaseReasoningParser):
    """
    恒等 Reasoning Parser

    不做任何处理，直接返回原内容。
    用于不需要 reasoning 解析的模型。
    """

    @property
    def start_token(self) -> str:
        return ""

    @property
    def end_token(self) -> str:
        return ""

    def is_reasoning_end(self, input_ids: Sequence[int]) -> bool:
        return True

    def extract_content_ids(self, input_ids: List[int]) -> List[int]:
        return input_ids

    def extract_reasoning(
        self,
        model_output: str,
        request: Optional[Any] = None
    ) -> tuple[Optional[str], Optional[str]]:
        return None, model_output
