# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project

"""vllm.config 兼容层 stub。

仅提供 ModelConfig 类型定义，用于 TYPE_CHECKING 导入。
在 TrajProxy 代理上下文中，ModelConfig 实例不会出现（始终为 None）。
"""

from typing import Any

# ModelConfig stub：对齐 vllm 源码 abs_reasoning_parsers.py 的 TYPE_CHECKING 导入
# 实际 vLLM 中 ModelConfig 是一个复杂 dataclass（>2000 行），此处仅需类型占位
ModelConfig = Any
