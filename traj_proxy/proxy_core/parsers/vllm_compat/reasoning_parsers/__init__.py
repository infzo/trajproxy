# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project

"""
Reasoning Parsers 模块

自动注册所有推理解析器。

添加新 parser 的步骤：
1. 将 vllm 的 parser 文件复制到当前目录
2. 在下面的 _REASONING_PARSERS_TO_REGISTER 字典中添加注册信息
3. 无需修改 parser 文件本身！
"""
from vllm.reasoning.abs_reasoning_parsers import (
    ReasoningParser,
    ReasoningParserManager,
)

__all__ = ["ReasoningParser", "ReasoningParserManager"]


# 需要注册的 reasoning parsers
# 格式: "name" -> ("module_path", "ClassName")
# module_path 是相对于 vllm_compat 目录的路径
_REASONING_PARSERS_TO_REGISTER = {
    "qwen3": (
        "reasoning_parsers.qwen3_reasoning_parser",
        "Qwen3ReasoningParser",
    ),
    "deepseek_r1": (
        "reasoning_parsers.deepseek_r1_reasoning_parser",
        "DeepSeekR1ReasoningParser",
    ),
}


def register_lazy_reasoning_parsers():
    """注册所有延迟加载的 reasoning parsers"""
    for name, (module_path, class_name) in _REASONING_PARSERS_TO_REGISTER.items():
        ReasoningParserManager.register_lazy_module(name, module_path, class_name)


# 模块加载时自动注册
register_lazy_reasoning_parsers()
