"""
Reasoning Parser 模块

注册所有内置 Reasoning Parser。
"""

from ..reasoning_parser_manager import ReasoningParserManager
from ..base import BaseReasoningParser


# 懒加载注册表
_REASONING_PARSERS_TO_REGISTER = {
    # DeepSeek 系列
    "deepseek_r1": (
        "deepseek_r1_reasoning_parser",
        "DeepSeekR1ReasoningParser",
    ),
    "deepseek_v3": (
        "deepseek_v3_reasoning_parser",
        "DeepSeekV3ReasoningParser",
    ),
    # Qwen 系列
    "qwen3": (
        "qwen3_reasoning_parser",
        "Qwen3ReasoningParser",
    ),
    # GLM 系列（复用 DeepSeek V3）
    "glm45": (
        "deepseek_v3_reasoning_parser",
        "DeepSeekV3ReasoningWithThinkingParser",
    ),
}


def register_lazy_reasoning_parsers():
    """注册所有懒加载的 Reasoning Parser"""
    for name, (file_name, class_name) in _REASONING_PARSERS_TO_REGISTER.items():
        module_path = f"traj_proxy.proxy_core.parsers.reasoning_parsers.{file_name}"
        ReasoningParserManager.register_lazy_module(name, module_path, class_name)


# 模块加载时注册
register_lazy_reasoning_parsers()

__all__ = [
    "ReasoningParserManager",
    "BaseReasoningParser",
    "register_lazy_reasoning_parsers",
]
