# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project
# Adapted from vllm/tool_parsers/utils.py

"""
工具解析器工具函数

兼容 vllm 0.16.0 接口，提供 find_common_prefix, is_complete_json 等常用工具函数。
"""
import json
from json import JSONDecodeError, JSONDecoder
from typing import Any, Dict, List, Optional, Union

import partial_json_parser
from partial_json_parser.core.options import Allow

from vllm.entrypoints.openai.chat_completion.protocol import (
    ChatCompletionNamedToolsChoiceParam,
    ChatCompletionToolsParam,
)


def find_common_prefix(s1: str, s2: str) -> str:
    """
    查找两个字符串的公共前缀。参数顺序不重要。

    用于从 partial_json_parser 生成的 JSON 中提取信息，
    确保流式输出中不会过早返回 close-quotes/brackets/braces。

    e.g. find_common_prefix('{"fruit": "ap"}', '{"fruit": "apple"}') ->
    '{"fruit": "ap'
    """
    prefix = ""
    min_length = min(len(s1), len(s2))
    for i in range(0, min_length):
        if s1[i] == s2[i]:
            prefix += s1[i]
        else:
            break
    return prefix


def find_common_suffix(s1: str, s2: str) -> str:
    """
    查找两个字符串的公共后缀。参数顺序不重要。
    遇到字母数字字符时停止。

    e.g. find_common_suffix('{"fruit": "ap"}', '{"fruit": "apple"}') -> '"}'
    """
    suffix = ""
    min_length = min(len(s1), len(s2))
    for i in range(1, min_length + 1):
        if s1[-i] == s2[-i] and not s1[-i].isalnum():
            suffix = s1[-i] + suffix
        else:
            break
    return suffix


def extract_intermediate_diff(curr: str, old: str) -> str:
    """
    给定两个具有公共前缀和/或后缀的字符串，提取中间的差异部分。

    参数顺序重要：curr 是新版本的局部 JSON，old 是前一次生成的版本。
    返回应该流式发送给客户端的 tokens。

    e.g. extract_intermediate_diff('{"fruit": "apple"}', '{"fruit": "ap"}')
        -> 'ple'
    """
    suffix = find_common_suffix(curr, old)

    old = old[::-1].replace(suffix[::-1], "", 1)[::-1]
    prefix = find_common_prefix(curr, old)
    diff = curr
    if len(suffix):
        diff = diff[::-1].replace(suffix[::-1], "", 1)[::-1]

    if len(prefix):
        diff = diff.replace(prefix, "", 1)

    return diff


def find_all_indices(string: str, substring: str) -> list[int]:
    """查找子串在字符串中的所有起始位置，用于工具调用提取"""
    indices = []
    index = -1
    while True:
        index = string.find(substring, index + 1)
        if index == -1:
            break
        indices.append(index)
    return indices


def partial_json_loads(input_str: str, flags: Allow) -> tuple[Any, int]:
    """
    解析部分 JSON 字符串。
    partial_json_parser 不支持 extra data，
    JSONDecoder.raw_decode 不支持部分 JSON，两者互补。
    """
    try:
        return (partial_json_parser.loads(input_str, flags), len(input_str))
    except JSONDecodeError as e:
        if "Extra data" in e.msg:
            dec = JSONDecoder()
            return dec.raw_decode(input_str)
        raise


def is_complete_json(input_str: str) -> bool:
    """验证字符串是否为完整的有效 JSON"""
    try:
        json.loads(input_str)
        return True
    except JSONDecodeError:
        return False


def consume_space(i: int, s: str) -> int:
    """跳过字符串中从位置 i 开始的空白字符，返回第一个非空白字符的位置"""
    while i < len(s) and s[i].isspace():
        i += 1
    return i


def _extract_tool_info(
    tool: ChatCompletionToolsParam,
) -> tuple[str, dict[str, Any] | None]:
    """从工具定义中提取函数名和参数 schema"""
    if isinstance(tool, ChatCompletionToolsParam):
        return tool.function.name, tool.function.parameters
    else:
        raise TypeError(f"Unsupported tool type: {type(tool)}")


def _get_tool_schema_from_tool(tool: ChatCompletionToolsParam) -> dict:
    """从单个工具生成 JSON Schema"""
    name, params = _extract_tool_info(tool)
    params = params if params else {"type": "object", "properties": {}}
    return {
        "properties": {
            "name": {"type": "string", "enum": [name]},
            "parameters": params,
        },
        "required": ["name", "parameters"],
    }


def _get_tool_schema_defs(
    tools: list[ChatCompletionToolsParam],
) -> dict:
    """从工具列表中收集所有 $defs 定义"""
    all_defs: dict[str, dict[str, Any]] = {}
    for tool in tools:
        _, params = _extract_tool_info(tool)
        if params is None:
            continue
        defs = params.pop("$defs", {})
        for def_name, def_schema in defs.items():
            if def_name in all_defs and all_defs[def_name] != def_schema:
                raise ValueError(
                    f"Tool definition '{def_name}' has multiple schemas, "
                    "which is not supported."
                )
            all_defs[def_name] = def_schema
    return all_defs


def _get_json_schema_from_tools(
    tools: list[ChatCompletionToolsParam],
) -> dict:
    """从工具列表生成完整的 JSON Schema"""
    json_schema = {
        "type": "array",
        "minItems": 1,
        "items": {
            "type": "object",
            "anyOf": [_get_tool_schema_from_tool(tool) for tool in tools],
        },
    }
    json_schema_defs = _get_tool_schema_defs(tools)
    if json_schema_defs:
        json_schema["$defs"] = json_schema_defs
    return json_schema


def get_json_schema_from_tools(
    tool_choice: Union[str, Dict[str, Any], ChatCompletionNamedToolsChoiceParam, None],
    tools: Optional[List[ChatCompletionToolsParam]]
) -> Optional[Dict[str, Any]]:
    """从工具定义生成 JSON Schema

    Args:
        tool_choice: 工具选择（"auto", "none", "required", 或具体工具名）
        tools: 工具列表

    Returns:
        对应的 JSON Schema，如果无法生成则返回 None
    """
    if not tools:
        return None

    # 处理 "none" 或 None 情况
    if tool_choice in ("none", None):
        return None

    # 处理具体工具选择（ChatCompletionNamedToolChoiceParam）
    if isinstance(tool_choice, ChatCompletionNamedToolsChoiceParam):
        tool_name = tool_choice.function.name
        tool_map = {
            tool.function.name: tool
            for tool in tools
            if isinstance(tool, ChatCompletionToolsParam)
        }
        if tool_name not in tool_map:
            raise ValueError(f"Tool '{tool_name}' has not been passed in `tools`.")
        return tool_map[tool_name].function.parameters

    # 处理 "required" 情况
    if tool_choice == "required":
        return _get_json_schema_from_tools(tools)

    # 处理 "auto" 情况 — auto 不强制 schema
    return None


__all__ = [
    "find_common_prefix",
    "find_common_suffix",
    "extract_intermediate_diff",
    "find_all_indices",
    "partial_json_loads",
    "is_complete_json",
    "consume_space",
    "get_json_schema_from_tools",
]
