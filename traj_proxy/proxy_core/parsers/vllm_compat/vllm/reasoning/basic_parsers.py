# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project
# Adapted from vllm/reasoning/basic_parsers.py

"""
基础推理解析器

对齐 vllm 最新版本接口，保留 proxy 上下文特有的文本回退逻辑。
"""
from abc import abstractmethod
from collections.abc import Iterable, Sequence
from itertools import islice
from typing import TYPE_CHECKING, Any

from vllm.entrypoints.openai.engine.protocol import DeltaMessage
from vllm.reasoning.abs_reasoning_parsers import ReasoningParser
from vllm.tokenizers import TokenizerLike
from vllm.logger import init_logger

if TYPE_CHECKING:
    from vllm.entrypoints.openai.chat_completion.protocol import ChatCompletionRequest
    from vllm.entrypoints.openai.responses.protocol import ResponsesRequest
else:
    ChatCompletionRequest = Any
    ResponsesRequest = Any

logger = init_logger(__name__)


class BaseThinkingReasoningParser(ReasoningParser):
    """
    Base class for reasoning parsers that use thinking tokens.

    This class provides common functionality for parsers that use start and end
    tokens to delimit reasoning content (
        e.g., 索索...最终答案, <seed:think>...</seed:think>).

    Subclasses must implement the start and end tokens via abstract
    properties.
    """

    @property
    @abstractmethod
    def start_token(self) -> str:
        """The token that starts reasoning content."""
        raise NotImplementedError

    @property
    @abstractmethod
    def end_token(self) -> str:
        """The token that ends reasoning content."""
        raise NotImplementedError

    @property
    def reasoning_start_str(self) -> str:
        return self.start_token

    @property
    def reasoning_end_str(self) -> str:
        return self.end_token

    def __init__(self, tokenizer: TokenizerLike, *args, **kwargs):
        super().__init__(tokenizer, *args, **kwargs)

        if not self.model_tokenizer:
            raise ValueError(
                "The model tokenizer must be passed to the ReasoningParser "
                "constructor during construction."
            )

        if not self.start_token or not self.end_token:
            raise ValueError("start_token and end_token must be defined in subclasses")

        # Token ID lookup: try multiple methods for maximum compatibility
        # This is a proxy-specific enhancement over vllm upstream, which only
        # uses vocab.get(). In the proxy context, tokenizers may come from
        # various sources, so we need more robust ID lookup.
        self.start_token_id = None
        self.end_token_id = None

        # Method 1: vocab.get (vllm upstream standard)
        self.start_token_id = self.vocab.get(self.start_token)
        self.end_token_id = self.vocab.get(self.end_token)

        # Method 2: added_tokens_encoder (most reliable for special tokens)
        if self.start_token_id is None and hasattr(
            self.model_tokenizer, 'added_tokens_encoder'
        ):
            self.start_token_id = self.model_tokenizer.added_tokens_encoder.get(
                self.start_token
            )
            self.end_token_id = self.model_tokenizer.added_tokens_encoder.get(
                self.end_token
            )

        # Method 3: convert_tokens_to_ids
        if self.start_token_id is None and hasattr(
            self.model_tokenizer, 'convert_tokens_to_ids'
        ):
            try:
                token_id = self.model_tokenizer.convert_tokens_to_ids(
                    self.start_token
                )
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

        # Method 4: encode/decode cycle for correct string encoding
        if self.start_token_id is None:
            try:
                encoded = self.model_tokenizer.encode(
                    self.start_token, add_special_tokens=False
                )
                if encoded:
                    decoded = self.model_tokenizer.decode(encoded)
                    self.start_token_id = self.vocab.get(decoded)
                    if self.start_token_id is None and len(encoded) == 1:
                        self.start_token_id = encoded[0]
            except Exception:
                pass

        if self.end_token_id is None:
            try:
                encoded = self.model_tokenizer.encode(
                    self.end_token, add_special_tokens=False
                )
                if encoded:
                    decoded = self.model_tokenizer.decode(encoded)
                    self.end_token_id = self.vocab.get(decoded)
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
        self, input_ids: Sequence[int], delta_ids: Iterable[int]
    ) -> bool:
        end_token_id = self.end_token_id
        return end_token_id in delta_ids

    def extract_content_ids(self, input_ids: list[int]) -> list[int]:
        """
        Extract the content after the end tokens
        """
        if self.end_token_id not in islice(input_ids, 0, max(0, len(input_ids) - 1)):
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
        Extract reasoning content from a delta message.
        Handles streaming output where previous + delta = current.
        Uses token IDs for faster processing.
        """
        # When token_ids are empty, fall back to text-based logic
        # This is a proxy-specific enhancement: in the proxy context,
        # we may receive chunks without token_ids
        if not delta_token_ids and not previous_token_ids:
            return self._extract_reasoning_streaming_by_text(
                previous_text, current_text, delta_text
            )

        # Skip single special tokens (aligned with vllm upstream)
        if len(delta_token_ids) == 1 and (
            delta_token_ids[0] in [self.start_token_id, self.end_token_id]
        ):
            return None

        # Check if start token is present in previous or delta.
        # Keep compatibility with models that don't generate start tokens.
        if self.start_token_id in previous_token_ids:
            if self.end_token_id in delta_token_ids:
                # start token in previous, end token in delta,
                # extract reasoning content
                end_index = delta_text.find(self.end_token)
                reasoning = delta_text[:end_index]
                content = delta_text[end_index + len(self.end_token) :]
                return DeltaMessage(
                    reasoning=reasoning, content=content if content else None
                )
            elif self.end_token_id in previous_token_ids:
                # start token in previous, end token in previous,
                # reasoning content continues
                return DeltaMessage(content=delta_text)
            else:
                # start token in previous, no end token in previous or delta,
                # reasoning content continues
                return DeltaMessage(reasoning=delta_text)
        elif self.start_token_id in delta_token_ids:
            if self.end_token_id in delta_token_ids:
                # start token in delta, end token in delta,
                # extract reasoning content
                start_index = delta_text.find(self.start_token)
                end_index = delta_text.find(self.end_token)
                reasoning = delta_text[start_index + len(self.start_token) : end_index]
                content = delta_text[end_index + len(self.end_token) :]
                return DeltaMessage(
                    reasoning=reasoning, content=content if content else None
                )
            else:
                # start token in delta, no end token in delta,
                # reasoning content continues
                return DeltaMessage(reasoning=delta_text)
        else:
            # not find thinking start token
            return DeltaMessage(content=delta_text)

    def _extract_reasoning_streaming_by_text(
        self,
        previous_text: str,
        current_text: str,
        delta_text: str,
    ) -> DeltaMessage | None:
        """
        Text-based reasoning streaming extraction (proxy fallback).

        In the proxy context, we may receive streaming chunks without
        token_ids. This method provides a fallback that uses text
        matching to determine reasoning vs content boundaries.

        NOTE: This method does not exist in vllm upstream. It is a
        proxy-specific addition to handle cases where token_ids are
        unavailable.
        """
        start_in_previous = self.start_token in previous_text
        start_in_delta = self.start_token in delta_text
        end_in_previous = self.end_token in previous_text
        end_in_delta = self.end_token in delta_text

        start_in_current = self.start_token in current_text
        end_in_current = self.end_token in current_text

        # Determine reasoning mode based on relative positions
        if start_in_current and end_in_current:
            start_pos = current_text.find(self.start_token)
            end_pos = current_text.find(self.end_token)
            in_reasoning_mode = start_pos < end_pos
        elif start_in_current:
            in_reasoning_mode = True
        elif end_in_current or end_in_previous:
            in_reasoning_mode = False
        else:
            in_reasoning_mode = False

        # Delta is just the start token itself
        if delta_text == self.start_token:
            return None

        # Delta starts with start token
        if delta_text.startswith(self.start_token):
            reasoning = delta_text[len(self.start_token):]
            return DeltaMessage(reasoning=reasoning if reasoning else None)

        # Start token in previous or current
        if start_in_previous or (start_in_current and not start_in_delta):
            if end_in_delta:
                end_index = delta_text.find(self.end_token)
                reasoning = delta_text[:end_index]
                content = delta_text[end_index + len(self.end_token):]
                return DeltaMessage(
                    reasoning=reasoning, content=content if content else None
                )
            elif end_in_previous:
                return DeltaMessage(content=delta_text)
            else:
                return DeltaMessage(reasoning=delta_text)

        elif start_in_delta:
            if end_in_delta:
                start_index = delta_text.find(self.start_token)
                end_index = delta_text.find(self.end_token)
                reasoning = delta_text[start_index + len(self.start_token):end_index]
                content = delta_text[end_index + len(self.end_token):]
                return DeltaMessage(
                    reasoning=reasoning, content=content if content else None
                )
            else:
                start_index = delta_text.find(self.start_token)
                reasoning = delta_text[start_index + len(self.start_token):]
                return DeltaMessage(reasoning=reasoning if reasoning else None)

        elif end_in_previous or (end_in_current and not end_in_delta):
            return DeltaMessage(content=delta_text)

        else:
            return DeltaMessage(content=delta_text)

    def extract_reasoning(
        self, model_output: str, request: ChatCompletionRequest | ResponsesRequest
    ) -> tuple[str | None, str | None]:
        """
        Extract reasoning content from the model output.

        This is the base implementation that works for most models.
        Subclasses can override this method for specific behavior.
        """
        # Check if the start token is present in the model output, remove it
        # if it is present.
        model_output_parts = model_output.partition(self.start_token)
        model_output = (
            model_output_parts[2] if model_output_parts[1] else model_output_parts[0]
        )

        # For models that may not generate start token,
        # assume the reasoning content is always at the start.
        if self.end_token not in model_output:
            return model_output, None
        else:
            reasoning, _, content = model_output.partition(self.end_token)
            # If generation stops right after end-of-think, return null content
            final_content = content or None
            return reasoning, final_content

    def count_reasoning_tokens(self, token_ids: Sequence[int]) -> int:
        """Count tokens that fall within start/end thinking markers.

        Uses a depth counter so nested spans are handled safely and stray end
        tokens do not drive the counter negative.
        """
        count = 0
        depth = 0
        for token_id in token_ids:
            if token_id == self.start_token_id:
                depth += 1
                continue
            if token_id == self.end_token_id:
                if depth > 0:
                    depth -= 1
                continue
            if depth > 0:
                count += 1
        return count


__all__ = ["BaseThinkingReasoningParser"]