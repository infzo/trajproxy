# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project

"""
Chat 工具函数兼容层

提供 make_tool_call_id 函数。
"""
import uuid


def random_uuid() -> str:
    """生成随机 UUID"""
    return str(uuid.uuid4())


def make_tool_call_id(id_type: str = "random", func_name=None, idx=None):
    """生成工具调用 ID

    Args:
        id_type: ID 类型（"random" 或 "kimi_k2"）
        func_name: 函数名称（用于 kimi_k2 类型）
        idx: 索引（用于 kimi_k2 类型）

    Returns:
        工具调用 ID 字符串
    """
    if id_type == "kimi_k2":
        return f"functions.{func_name}:{idx}"
    else:
        # by default return random
        return f"chatcmpl-tool-{random_uuid()}"


__all__ = ["make_tool_call_id", "random_uuid"]