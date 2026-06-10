# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project
# Adapted from vllm/tool_parsers/streaming.py

"""
流式工具调用提取函数

对齐 vLLM 最新版 vllm/tool_parsers/streaming.py 接口，
提供 extract_named_tool_call_streaming 和 extract_required_tool_call_streaming。

旧版 vLLM 中这两个函数位于 vllm.parser.utils，
新版已迁移到 vllm.tool_parsers.streaming。
本兼容层使用新路径。
"""

import json
from typing import TYPE_CHECKING

import partial_json_parser
import regex as re
from partial_json_parser.core.options import Allow

from vllm.entrypoints.chat_utils import make_tool_call_id
from vllm.entrypoints.openai.engine.protocol import (
    DeltaFunctionCall,
    DeltaMessage,
    DeltaToolCall,
)
from vllm.tool_parsers.mistral_tool_parser import MistralToolCall
from vllm.tool_parsers.utils import partial_json_loads
from vllm.utils.mistral import is_mistral_tokenizer

if TYPE_CHECKING:
    from vllm.tokenizers import TokenizerLike
else:
    TokenizerLike = object


def _bracket_level(s: str, opening: str = "{", closing: str = "}") -> int:
    """计算字符串中嵌套括号的当前层级"""
    level = 0
    for char in s:
        if char == opening:
            level += 1
        elif char == closing:
            level -= 1
    return level


def filter_delta_text(
    delta_text: str,
    previous_text: str,
) -> tuple[str, bool]:
    """Trim trailing tool-list delimiters from required-tool streaming text."""
    bracket_level = _bracket_level(previous_text)
    updated_delta = ""
    passed_zero = False
    for char in delta_text:
        if char == "{":
            bracket_level += 1
            passed_zero = bracket_level == 0
        elif char == "}":
            bracket_level -= 1
            passed_zero = bracket_level == 0

        if bracket_level != 0:
            updated_delta += char
        else:
            if char == ",":
                break
    return updated_delta, passed_zero


def extract_named_tool_call_streaming(
    *,
    delta_text: str,
    function_name: str,
    function_name_returned: bool,
    tool_call_idx: int | None,
    tool_call_id_type: str,
    tokenizer: "TokenizerLike",
    tool_call_array_index: int = 0,
) -> tuple[DeltaMessage | None, bool]:
    """Build a streaming tool-call delta for forced named tool choice.

    对齐 vLLM DelegatingParser._extract_tool_calls_streaming() 中
    Named tool choice 分支（tool_choice={"function": {"name": ...}}）。

    Args:
        delta_text: 当前增量文本
        function_name: 强制调用的函数名
        function_name_returned: 函数名是否已返回
        tool_call_idx: 工具调用索引
        tool_call_id_type: 工具调用 ID 类型
        tokenizer: tokenizer 实例（用于 Mistral 模型判断）
        tool_call_array_index: 工具调用在数组中的索引

    Returns:
        (DeltaMessage, function_name_returned) 元组
    """
    if function_name_returned:
        delta_tool_call = DeltaToolCall(
            function=DeltaFunctionCall(arguments=delta_text),
            index=tool_call_array_index,
        )
    else:
        if is_mistral_tokenizer(tokenizer):
            tool_call_id = MistralToolCall.generate_random_id()
        else:
            tool_call_id = make_tool_call_id(
                id_type=tool_call_id_type,
                func_name=function_name,
                idx=tool_call_idx,
            )
        delta_tool_call = DeltaToolCall(
            id=tool_call_id,
            type="function",
            function=DeltaFunctionCall(
                name=function_name,
                arguments=delta_text,
            ),
            index=tool_call_array_index,
        )
        function_name_returned = True
    return (
        DeltaMessage(tool_calls=[delta_tool_call]),
        function_name_returned,
    )


def extract_required_tool_call_streaming(
    *,
    previous_text: str,
    current_text: str | None,
    delta_text: str,
    function_name_returned: bool,
    tool_call_idx: int | None,
    tool_call_id_type: str,
) -> tuple[DeltaMessage | None, bool]:
    """Build a streaming tool-call delta for required tool choice.

    对齐 vLLM DelegatingParser._extract_tool_calls_streaming() 中
    Required tool choice 分支（tool_choice="required"）。

    Args:
        previous_text: 之前累积的完整文本
        current_text: 当前累积的完整文本（含本次 delta）
        delta_text: 本次增量文本
        function_name_returned: 函数名是否已返回
        tool_call_idx: 工具调用索引
        tool_call_id_type: 工具调用 ID 类型

    Returns:
        (DeltaMessage, function_name_returned) 元组
    """
    if current_text is None or current_text == "":
        return None, function_name_returned
    try:
        flags = Allow.ALL
        obj, _ = partial_json_loads(current_text, flags)
    except (
        partial_json_parser.core.exceptions.MalformedJSON,
        json.JSONDecodeError,
    ):
        obj = None

    # 检查当前文本是否是包含部分工具调用对象的合法数组
    if obj is None or not isinstance(obj, list) or not len(obj) > 0:
        function_name_returned = False
        delta_message = None
    else:
        _, finishes_previous_tool = filter_delta_text(delta_text, previous_text)
        # 取最后一个工具调用
        current_tool_call = obj[-1]

        # 一旦生成了 parameters，name 也已完成
        if not finishes_previous_tool and (
            "name" not in current_tool_call or "parameters" not in current_tool_call
        ):
            function_name_returned = False
            delta_message = None
        else:
            if not function_name_returned:
                # 从最新的工具调用中获取部分生成的参数
                param_match = re.search(
                    r'.*"parameters":\s*(.*)', current_text, re.DOTALL
                )
                arguments = param_match.group(1) if param_match else ""
                arguments, _ = filter_delta_text(arguments, previous_text)

                # 如果本次迭代完成了前一个工具调用但新的不完整工具已生成，
                # 取列表中的倒数第二个
                if finishes_previous_tool and "parameters" not in current_tool_call:
                    current_tool_call = obj[-2]

                function_name_returned = True
                tool_call_id = make_tool_call_id(
                    id_type=tool_call_id_type,
                    func_name=current_tool_call["name"],
                    idx=tool_call_idx,
                )
                delta_message = DeltaMessage(
                    tool_calls=[
                        DeltaToolCall(
                            id=tool_call_id,
                            function=DeltaFunctionCall(
                                name=current_tool_call["name"], arguments=arguments
                            ),
                            index=len(obj) - 1,
                            type="function",
                        )
                    ]
                )

            else:
                delta_text, _ = filter_delta_text(delta_text, previous_text)

                if delta_text != "":
                    delta_message = DeltaMessage(
                        tool_calls=[
                            DeltaToolCall(
                                function=DeltaFunctionCall(
                                    name=None,
                                    arguments=delta_text,
                                ),
                                index=len(obj) - 1,
                            )
                        ]
                    )
                else:
                    delta_message = None

    return delta_message, function_name_returned


__all__ = [
    "extract_named_tool_call_streaming",
    "extract_required_tool_call_streaming",
    "filter_delta_text",
]