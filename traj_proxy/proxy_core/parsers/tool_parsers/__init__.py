"""
Tool Parser 模块

注册所有内置 Tool Parser。
"""

from ..tool_parser_manager import ToolParserManager
from ..base import BaseToolParser


# 懒加载注册表
_TOOL_PARSERS_TO_REGISTER = {
    # DeepSeek 系列
    "deepseek_v3": (
        "deepseekv3_tool_parser",
        "DeepSeekV3ToolParser",
    ),
    "deepseek_v31": (
        "deepseekv31_tool_parser",
        "DeepSeekV31ToolParser",
    ),
    "deepseek_v32": (
        "deepseekv32_tool_parser",
        "DeepSeekV32ToolParser",
    ),
    # Qwen 系列
    "qwen3_coder": (
        "qwen3coder_tool_parser",
        "Qwen3CoderToolParser",
    ),
    # GLM 系列
    "glm45": (
        "glm4_moe_tool_parser",
        "Glm4MoeModelToolParser",
    ),
    "glm47": (
        "glm47_moe_tool_parser",
        "Glm47MoeModelToolParser",
    ),
}


def register_lazy_tool_parsers():
    """注册所有懒加载的 Tool Parser"""
    for name, (file_name, class_name) in _TOOL_PARSERS_TO_REGISTER.items():
        module_path = f"traj_proxy.proxy_core.parsers.tool_parsers.{file_name}"
        ToolParserManager.register_lazy_module(name, module_path, class_name)


# 模块加载时注册
register_lazy_tool_parsers()

__all__ = [
    "ToolParserManager",
    "BaseToolParser",
    "register_lazy_tool_parsers",
]
