# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project

"""vllm.entrypoints.mcp.tool_server 兼容层 stub。

仅提供 ToolServer ABC 基类，用于 abs_reasoning_parsers.py 的运行时导入。
TrajProxy 代理上下文中不使用 ToolServer 实例（prepare_structured_tag 的
tool_server 参数始终为 None）。
"""

from abc import ABC, abstractmethod
from contextlib import AbstractAsyncContextManager
from typing import Any


class ToolServer(ABC):
    """ToolServer ABC stub，对齐 vllm 源码 abs_reasoning_parsers.py 的运行时导入。"""

    @abstractmethod
    def has_tool(self, tool_name: str) -> bool:
        """Return True if the tool is supported, False otherwise."""
        pass

    @abstractmethod
    def get_tool_description(
        self, tool_name: str, allowed_tools: list[str] | None = None
    ) -> Any | None:
        """Return the tool description for the given tool name."""
        pass

    @abstractmethod
    def new_session(
        self, tool_name: str, session_id: str, headers: dict[str, str] | None = None
    ) -> AbstractAsyncContextManager[Any]:
        """Create a session for the tool."""
        ...
