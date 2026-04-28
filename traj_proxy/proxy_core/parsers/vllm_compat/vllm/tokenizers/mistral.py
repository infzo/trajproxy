# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project

"""
Mistral Tokenizer 兼容层

提供 MistralTokenizer 类占位符。
"""


class MistralTokenizer:
    """Mistral Tokenizer 占位符类

    用于类型检查，实际功能由 transformers tokenizer 提供。
    """

    def __init__(self, tokenizer=None):
        self.tokenizer = tokenizer


__all__ = ["MistralTokenizer"]