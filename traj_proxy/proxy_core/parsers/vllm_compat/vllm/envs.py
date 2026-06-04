# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project
# Adapted from vllm/envs.py

"""
vLLM 环境变量兼容层

在 proxy 上下文中，大部分 vLLM 环境变量不适用。
仅提供 parser 逻辑需要的少数常量。
"""

# 是否强制严格工具调用（使用 structural tag 进行 guided decoding）
# 在 proxy 上下文中，我们不做 token 生成，因此始终为 False
VLLM_ENFORCE_STRICT_TOOL_CALLING: bool = False

__all__ = ["VLLM_ENFORCE_STRICT_TOOL_CALLING"]