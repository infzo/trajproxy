# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project

"""Mistral tokenizer utilities (stub for trajproxy compat layer)."""

from vllm.tokenizers import TokenizerLike


def is_mistral_tokenizer(obj: TokenizerLike | None) -> bool:
    """Always returns False — Mistral tokenizer not used in trajproxy."""
    return False
