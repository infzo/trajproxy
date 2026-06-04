# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project
# Adapted from vllm/sampling_params.py

"""
vLLM SamplingParams 兼容层

在 proxy 上下文中，仅提供 StructuredOutputsParams 简化版本，
用于 adjust_request 中设置结构化输出参数。
"""
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class StructuredOutputsParams:
    """结构化输出参数（简化版）

    对齐 vllm 的 StructuredOutputsParams 接口，
    但仅保留 parser 逻辑需要的字段。
    """
    json: Optional[dict[str, Any]] = None
    structural_tag: Optional[str] = None


__all__ = ["StructuredOutputsParams"]