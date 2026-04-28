# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project

"""
Tool Parsers 模块

自动注册所有工具解析器。

添加新 parser 的步骤：
1. 将 vllm 的 parser 文件复制到当前目录
2. 在下面的 _TOOL_PARSERS_TO_REGISTER 字典中添加注册信息
3. 无需修改 parser 文件本身！
"""
from vllm.tool_parsers.abstract_tool_parser import (
    ToolParser,
    ToolParserManager,
)

__all__ = ["ToolParser", "ToolParserManager"]


# 需要注册的 tool parsers
# 格式: "name" -> ("module_path", "ClassName")
# module_path 是相对于 vllm_compat 目录的路径
_TOOL_PARSERS_TO_REGISTER = {
    "qwen3_coder": (
        "tool_parsers.qwen3coder_tool_parser",
        "Qwen3CoderToolParser",
    ),
    "qwen3_xml": (
        "tool_parsers.qwen3xml_tool_parser",
        "Qwen3XMLToolParser",
    ),
    "hermes": (
        "tool_parsers.hermes_tool_parser",
        "Hermes2ProToolParser",
    ),
    # 添加更多 parser 到这里
    # 示例：
    # "deepseek_v3": (
    #     "tool_parsers.deepseekv3_tool_parser",
    #     "DeepSeekV3ToolParser",
    # ),
    # "llama4_pythonic": (
    #     "tool_parsers.llama4_pythonic_tool_parser",
    #     "Llama4PythonicToolParser",
    # ),
}


def register_lazy_tool_parsers():
    """注册所有延迟加载的 tool parsers"""
    for name, (module_path, class_name) in _TOOL_PARSERS_TO_REGISTER.items():
        ToolParserManager.register_lazy_module(name, module_path, class_name)


# 模块加载时自动注册
register_lazy_tool_parsers()
