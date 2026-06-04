# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project
# Adapted from vllm/tool_parsers/structural_tag_registry.py

"""
vLLM Structural Tag Registry 兼容层（stub）

在 proxy 上下文中，我们不做 guided decoding / token 生成，
因此 structural tag 功能不需要实现。
提供 stub 函数以确保 parser 代码可以正常导入和运行。
"""
from typing import Any

from vllm.entrypoints.openai.chat_completion.protocol import (
    ChatCompletionNamedToolChoiceParam,
    ChatCompletionToolsParam,
)


def get_model_structural_tag(
    model: str,
    tools: list[ChatCompletionToolsParam] | None,
    tool_choice: Any,
    reasoning: bool,
) -> Any:
    """Stub: proxy 不做 guided decoding，始终返回 None"""
    return None


def get_enable_structured_outputs_in_reasoning() -> bool:
    """Stub: proxy 上下文中始终返回 False"""
    return False


def set_enable_structured_outputs_in_reasoning(enabled: bool) -> None:
    """Stub: 无操作"""
    pass


__all__ = [
    "get_model_structural_tag",
    "get_enable_structured_outputs_in_reasoning",
    "set_enable_structured_outputs_in_reasoning",
]