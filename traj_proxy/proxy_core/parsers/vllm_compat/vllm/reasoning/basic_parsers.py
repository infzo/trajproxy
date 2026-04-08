# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project
# Adapted from vllm/reasoning/basic_parsers.py

"""
基础推理解析器

提供 BaseThinkingReasoningParser 基类，用于使用思考标记的模型。
"""
from abc import abstractmethod
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from vllm.entrypoints.openai.engine.protocol import DeltaMessage
from vllm.reasoning.abs_reasoning_parsers import ReasoningParser
from vllm.tokenizers import TokenizerLike

if TYPE_CHECKING:
    from vllm.entrypoints.openai.chat_completion.protocol import (
        ChatCompletionRequest,
    )
    from vllm.entrypoints.openai.responses.protocol import (
        ResponsesRequest,
    )
else:
    ChatCompletionRequest = Any
    ResponsesRequest = Any


class BaseThinkingReasoningParser(ReasoningParser):
    """
    使用思考标记的推理解析器的基类。

    此类为使用开始和结束标记来界定推理内容的解析器提供通用功能
    （例如 思索...最终答案, <seed:think>...</seed:think>）。

    子类必须通过抽象属性实现 start_token 和 end_token。
    """

    @property
    @abstractmethod
    def start_token(self) -> str:
        """开始推理内容的标记"""
        raise NotImplementedError

    @property
    @abstractmethod
    def end_token(self) -> str:
        """结束推理内容的标记"""
        raise NotImplementedError

    def __init__(self, tokenizer: TokenizerLike, *args, **kwargs):
        super().__init__(tokenizer, *args, **kwargs)

        if not self.model_tokenizer:
            raise ValueError(
                "The model tokenizer must be passed to the ReasoningParser "
                "constructor during construction."
            )

        if not self.start_token or not self.end_token:
            raise ValueError("start_token and end_token must be defined in subclasses")

        # 尝试多种方式获取 token ID
        # 方式 1: 使用 added_tokens_encoder（对于 special tokens 最可靠）
        # 但需要注意：added_tokens_encoder 中的 key 可能使用不同的编码
        self.start_token_id = None
        self.end_token_id = None

        if hasattr(self.model_tokenizer, 'added_tokens_encoder'):
            self.start_token_id = self.model_tokenizer.added_tokens_encoder.get(
                self.start_token
            )
            self.end_token_id = self.model_tokenizer.added_tokens_encoder.get(
                self.end_token
            )

        # 方式 2: 使用 convert_tokens_to_ids 方法
        if self.start_token_id is None and hasattr(
            self.model_tokenizer, 'convert_tokens_to_ids'
        ):
            try:
                token_id = self.model_tokenizer.convert_tokens_to_ids(self.start_token)
                if token_id is not None and token_id != self.model_tokenizer.unk_token_id:
                    self.start_token_id = token_id
            except Exception:
                pass

        if self.end_token_id is None and hasattr(
            self.model_tokenizer, 'convert_tokens_to_ids'
        ):
            try:
                token_id = self.model_tokenizer.convert_tokens_to_ids(self.end_token)
                if token_id is not None and token_id != self.model_tokenizer.unk_token_id:
                    self.end_token_id = token_id
            except Exception:
                pass

        # 方式 3: 从 vocab 获取
        if self.start_token_id is None:
            self.start_token_id = self.vocab.get(self.start_token)
        if self.end_token_id is None:
            self.end_token_id = self.vocab.get(self.end_token)

        # 方式 4: 使用 encode/decode 循环获取正确编码的字符串，然后在 vocab 中查找
        # 这是为了处理某些 tokenizer 的 vocab key 编码问题
        if self.start_token_id is None:
            try:
                # 先编码再解码，获取 tokenizer 内部使用的正确字符串形式
                encoded = self.model_tokenizer.encode(
                    self.start_token, add_special_tokens=False
                )
                if encoded:
                    decoded = self.model_tokenizer.decode(encoded)
                    # 使用解码后的字符串在 vocab 中查找
                    self.start_token_id = self.vocab.get(decoded)
                    # 如果 vocab 中也没有，直接使用 encode 的结果（如果只有一个 token）
                    if self.start_token_id is None and len(encoded) == 1:
                        self.start_token_id = encoded[0]
            except Exception:
                pass

        if self.end_token_id is None:
            try:
                # 先编码再解码，获取 tokenizer 内部使用的正确字符串形式
                encoded = self.model_tokenizer.encode(
                    self.end_token, add_special_tokens=False
                )
                if encoded:
                    decoded = self.model_tokenizer.decode(encoded)
                    # 使用解码后的字符串在 vocab 中查找
                    self.end_token_id = self.vocab.get(decoded)
                    # 如果 vocab 中也没有，直接使用 encode 的结果（如果只有一个 token）
                    if self.end_token_id is None and len(encoded) == 1:
                        self.end_token_id = encoded[0]
            except Exception:
                pass

        if self.start_token_id is None or self.end_token_id is None:
            raise RuntimeError(
                f"{self.__class__.__name__} reasoning parser could not locate "
                "think start/end tokens in the tokenizer!"
            )

    def is_reasoning_end(self, input_ids: Sequence[int]) -> bool:
        start_token_id = self.start_token_id
        end_token_id = self.end_token_id

        for i in range(len(input_ids) - 1, -1, -1):
            if input_ids[i] == start_token_id:
                return False
            if input_ids[i] == end_token_id:
                return True
        return False

    def is_reasoning_end_streaming(
        self, input_ids: Sequence[int], delta_ids: Sequence[int]
    ) -> bool:
        end_token_id = self.end_token_id
        return end_token_id in delta_ids

    def extract_content_ids(self, input_ids: list[int]) -> list[int]:
        """
        提取结束标记之后的内容
        """
        if self.end_token_id not in input_ids[:-1]:
            return []
        else:
            return input_ids[input_ids.index(self.end_token_id) + 1 :]

    def extract_reasoning_streaming(
        self,
        previous_text: str,
        current_text: str,
        delta_text: str,
        previous_token_ids: Sequence[int],
        current_token_ids: Sequence[int],
        delta_token_ids: Sequence[int],
    ) -> DeltaMessage | None:
        """
        从增量消息中提取推理内容。
        处理流式输出，其中 previous + delta = current。
        使用 token IDs 进行更快的处理，当 token_ids 为空时回退到文本判断。
        """
        # 当 token_ids 为空时，使用基于文本的判断逻辑
        if not delta_token_ids and not previous_token_ids:
            return self._extract_reasoning_streaming_by_text(
                previous_text, current_text, delta_text
            )

        # 基于 token_ids 的判断逻辑
        # 跳过单个结束标记，但不要跳过开始标记
        # 开始标记需要触发推理模式的开始
        if len(delta_token_ids) == 1 and delta_token_ids[0] == self.end_token_id:
            return None

        # 检查开始标记是否存在于 previous 或 delta 中
        # 保持与不生成开始标记的模型的兼容性
        if self.start_token_id in previous_token_ids:
            if self.end_token_id in delta_token_ids:
                # 开始标记在 previous 中，结束标记在 delta 中
                # 提取推理内容
                end_index = delta_text.find(self.end_token)
                reasoning = delta_text[:end_index]
                content = delta_text[end_index + len(self.end_token) :]
                return DeltaMessage(
                    reasoning=reasoning, content=content if content else None
                )
            elif self.end_token_id in previous_token_ids:
                # 开始标记在 previous 中，结束标记在 previous 中
                # 推理内容继续
                return DeltaMessage(content=delta_text)
            else:
                # 开始标记在 previous 中，previous 或 delta 中没有结束标记
                # 推理内容继续
                return DeltaMessage(reasoning=delta_text)
        elif self.start_token_id in delta_token_ids:
            if self.end_token_id in delta_token_ids:
                # 开始标记在 delta 中，结束标记在 delta 中
                # 提取推理内容
                start_index = delta_text.find(self.start_token)
                end_index = delta_text.find(self.end_token)
                reasoning = delta_text[start_index + len(self.start_token) : end_index]
                content = delta_text[end_index + len(self.end_token) :]
                return DeltaMessage(
                    reasoning=reasoning, content=content if content else None
                )
            else:
                # 开始标记在 delta 中，delta 中没有结束标记
                # 推理内容继续
                return DeltaMessage(reasoning=delta_text)
        else:
            # 未找到思考开始标记
            return DeltaMessage(content=delta_text)

    def _extract_reasoning_streaming_by_text(
        self,
        previous_text: str,
        current_text: str,
        delta_text: str
    ) -> DeltaMessage | None:
        """
        基于文本内容的流式推理解析（当 token_ids 不可用时的回退方案）
        """
        # 检查 previous_text 中是否包含开始标记
        start_in_previous = self.start_token in previous_text
        start_in_delta = self.start_token in delta_text
        end_in_previous = self.end_token in previous_text
        end_in_delta = self.end_token in delta_text

        # 检查 current_text 中是否包含开始标记（包括 previous + delta 的累积）
        start_in_current = self.start_token in current_text
        end_in_current = self.end_token in current_text

        # 计算 current_text 中 start_token 和 end_token 的相对位置
        if start_in_current and end_in_current:
            start_pos = current_text.find(self.start_token)
            end_pos = current_text.find(self.end_token)
            # 如果开始标记在结束标记前面，说明正在进行推理
            in_reasoning_mode = start_pos < end_pos
        elif start_in_current:
            # 有开始标记但没有结束标记，说明还在推理中
            in_reasoning_mode = True
        elif end_in_current or end_in_previous:
            # 有结束标记，说明已经退出推理
            in_reasoning_mode = False
        else:
            # 既没有开始也没有结束标记
            in_reasoning_mode = False

        # 如果 delta 只是开始标记本身（或开始标记的一部分）
        # 这种情况下不应该返回任何内容
        if delta_text == self.start_token:
            return None

        # 如果 delta 以开始标记开头
        if delta_text.startswith(self.start_token):
            # 提取开始标记之后的内容作为推理
            reasoning = delta_text[len(self.start_token):]
            return DeltaMessage(reasoning=reasoning if reasoning else None)

        # 如果 previous 中有开始标记
        if start_in_previous or (start_in_current and not start_in_delta):
            if end_in_delta:
                # 开始标记在 previous/current 中，结束标记在 delta 中
                end_index = delta_text.find(self.end_token)
                reasoning = delta_text[:end_index]
                content = delta_text[end_index + len(self.end_token):]
                return DeltaMessage(
                    reasoning=reasoning, content=content if content else None
                )
            elif end_in_previous:
                # 开始和结束标记都在 previous 中，当前是普通内容
                return DeltaMessage(content=delta_text)
            else:
                # 只有开始标记，还在推理内容中
                return DeltaMessage(reasoning=delta_text)

        # 如果 delta 中包含开始标记
        elif start_in_delta:
            if end_in_delta:
                # 开始和结束标记都在 delta 中
                start_index = delta_text.find(self.start_token)
                end_index = delta_text.find(self.end_token)
                reasoning = delta_text[start_index + len(self.start_token):end_index]
                content = delta_text[end_index + len(self.end_token):]
                return DeltaMessage(
                    reasoning=reasoning, content=content if content else None
                )
            else:
                # 只有开始标记，推理内容开始
                # 提取开始标记之后的内容作为推理
                start_index = delta_text.find(self.start_token)
                reasoning = delta_text[start_index + len(self.start_token):]
                return DeltaMessage(reasoning=reasoning if reasoning else None)

        # 如果 previous 或者 current 中已经有结束标记
        elif end_in_previous or (end_in_current and not end_in_delta):
            # 已经在普通内容区域
            return DeltaMessage(content=delta_text)

        # 未找到任何推理标记
        else:
            return DeltaMessage(content=delta_text)

    def extract_reasoning(
        self, model_output: str, request: ChatCompletionRequest | ResponsesRequest
    ) -> tuple[str | None, str | None]:
        """
        从模型输出中提取推理内容。

        这是适用于大多数模型的基础实现。
        子类可以覆盖此方法以实现特定行为。
        """
        # 检查开始标记是否存在于模型输出中，如果存在则移除
        model_output_parts = model_output.partition(self.start_token)
        model_output = (
            model_output_parts[2] if model_output_parts[1] else model_output_parts[0]
        )

        # 对于可能不生成开始标记的模型
        # 假设推理内容总是在开头
        if self.end_token not in model_output:
            return model_output, None
        else:
            reasoning, _, content = model_output.partition(self.end_token)
            # 如果生成在思考结束后立即停止，返回空内容
            final_content = content or None
            return reasoning, final_content


__all__ = ["BaseThinkingReasoningParser"]
