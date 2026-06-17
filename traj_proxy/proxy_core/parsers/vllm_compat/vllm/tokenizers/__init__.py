# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project

"""
vLLM tokenizer 类型适配器

提供 AnyTokenizer、TokenizerLike 类型别名，兼容 vllm 的类型定义。
"""
from typing import Union, Protocol, runtime_checkable

# 使用 transformers 的 tokenizer 类型
try:
    from transformers import PreTrainedTokenizer, PreTrainedTokenizerFast
    AnyTokenizer = Union[PreTrainedTokenizer, PreTrainedTokenizerFast]
except ImportError:
    # 如果 transformers 不可用，使用 Protocol
    @runtime_checkable
    class AnyTokenizer(Protocol):
        def encode(self, text: str, **kwargs) -> list[int]: ...
        def decode(self, ids: list[int], **kwargs) -> str: ...
        def get_vocab(self) -> dict[str, int]: ...


# 为了兼容性，也提供 TokenizerLike 别名
TokenizerLike = AnyTokenizer


__all__ = ["AnyTokenizer", "TokenizerLike"]
