# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project
# Adapted from vllm/entrypoints/openai/engine/protocol.py

"""
vLLM 协议类适配器

提供 DeltaMessage、ToolCall、ExtractedToolCallInformation 等核心数据结构。
这些类与 vllm 的接口兼容，但使用纯 Python 实现，无 GPU 依赖。
"""
from dataclasses import dataclass, field
from typing import Any, Optional, List

from vllm.entrypoints.chat_utils import make_tool_call_id


@dataclass
class FunctionCall:
    """函数调用"""
    name: str = ""
    arguments: str = ""


@dataclass
class ToolCall:
    """工具调用"""
    id: str = field(default_factory=make_tool_call_id)
    type: str = "function"
    function: Optional[FunctionCall] = None


@dataclass
class DeltaFunctionCall:
    """流式增量函数调用"""
    name: Optional[str] = None
    arguments: Optional[str] = None

    def model_dump(self, exclude_none: bool = False) -> dict:
        """兼容 Pydantic BaseModel.model_dump 接口"""
        result = {"name": self.name, "arguments": self.arguments}
        if exclude_none:
            result = {k: v for k, v in result.items() if v is not None}
        return result


@dataclass
class DeltaToolCall:
    """流式增量工具调用"""
    id: Optional[str] = None
    type: Optional[str] = None
    index: int = 0
    function: Optional[DeltaFunctionCall] = None

    def model_dump(self, exclude_none: bool = False) -> dict:
        """兼容 Pydantic BaseModel.model_dump 接口"""
        result = {
            "id": self.id,
            "type": self.type,
            "index": self.index,
            "function": self.function.model_dump(exclude_none=exclude_none) if self.function else None,
        }
        if exclude_none:
            result = {k: v for k, v in result.items() if v is not None}
        return result


@dataclass
class DeltaMessage:
    """流式增量消息"""
    role: Optional[str] = None
    content: Optional[str] = None
    reasoning: Optional[str] = None
    tool_calls: List[DeltaToolCall] = field(default_factory=list)


@dataclass
class ExtractedToolCallInformation:
    """提取的工具调用信息"""
    tools_called: bool
    tool_calls: List[ToolCall]
    content: Optional[str] = None


@dataclass
class FunctionDefinition:
    """函数定义"""
    name: str = ""
    description: Optional[str] = None
    parameters: Optional[dict] = None


@dataclass
class StreamOptions:
    """流式选项"""
    include_usage: bool = True
    continuous_usage_stats: bool = False


@dataclass
class UsageInfo:
    """使用信息"""
    prompt_tokens: int = 0
    total_tokens: int = 0
    completion_tokens: Optional[int] = 0


class OpenAIBaseModel:
    """OpenAI 基础模型（简化版）- 用于类型兼容"""
    pass


# 别名，方便使用
BaseModel = OpenAIBaseModel
