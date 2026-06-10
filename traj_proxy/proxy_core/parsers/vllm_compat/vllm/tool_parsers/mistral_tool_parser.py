# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project

"""
Mistral 工具解析器 stub

仅提供 MistralToolCall.generate_random_id() 接口，
供 streaming.py 的 extract_named_tool_call_streaming 使用。
完整 Mistral 解析器不在 trajproxy 中使用。
"""


class MistralToolCall:
    """Mistral 工具调用 stub"""

    @staticmethod
    def generate_random_id() -> str:
        """生成随机工具调用 ID"""
        import uuid
        return f"chatcmpl-tool-{uuid.uuid4()}"