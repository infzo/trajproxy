# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project

from collections.abc import Sequence

from vllm.entrypoints.openai.engine.protocol import DeltaMessage
from vllm.reasoning.basic_parsers import BaseThinkingReasoningParser


class DeepSeekR1ReasoningParser(BaseThinkingReasoningParser):
    """
    Reasoning parser for DeepSeek R1 and compatible models (e.g., Qwen3).

    Uses <think>...</think> tokens to denote reasoning text within model output.
    Non-streaming extraction uses base class logic, which correctly handles
    truncated output (when </think> is missing due to max_tokens limit).
    """

    @property
    def start_token(self) -> str:
        """The token that starts reasoning content."""
        return "<think>"

    @property
    def end_token(self) -> str:
        """The token that ends reasoning content."""
        return "</think>"

    def extract_reasoning_streaming(
        self,
        previous_text: str,
        current_text: str,
        delta_text: str,
        previous_token_ids: Sequence[int],
        current_token_ids: Sequence[int],
        delta_token_ids: Sequence[int],
    ) -> DeltaMessage | None:
        # Delegate to base class first (same logic as vLLM upstream)
        ret = super().extract_reasoning_streaming(
            previous_text,
            current_text,
            delta_text,
            previous_token_ids,
            current_token_ids,
            delta_token_ids,
        )

        # Additional handling for cases where base class returns None
        # (single start/end tokens skipped by base class), matching vLLM upstream
        if (
            ret is not None
            and self.start_token_id not in previous_token_ids
            and self.start_token_id not in delta_token_ids
        ):
            if self.end_token_id in delta_token_ids:
                # end token in delta with more tokens,
                # extract reasoning content and content
                end_index = delta_text.find(self.end_token)
                reasoning = delta_text[:end_index]
                content = delta_text[end_index + len(self.end_token) :]
                return DeltaMessage(
                    reasoning=reasoning,
                    content=content if content else None,
                )
            elif self.end_token_id in previous_token_ids:
                # end token in previous, thinking content ends
                return DeltaMessage(content=delta_text)
            else:
                # no end token in previous or delta, reasoning content continues
                return DeltaMessage(reasoning=delta_text)

        # Strip <think> prefix from reasoning delta when start token was in this delta
        # (base class returns DeltaMessage(reasoning=delta_text) which includes <think>)
        if (
            ret is not None
            and ret.reasoning
            and self.start_token_id in delta_token_ids
        ):
            clean = ret.reasoning
            if self.start_token in clean:
                clean = clean[clean.find(self.start_token) + len(self.start_token) :]
            ret = DeltaMessage(reasoning=clean, content=ret.content)

        return ret
