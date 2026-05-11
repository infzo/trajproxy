# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project
# Adapted from vllm/entrypoints/openai/chat_completion/protocol.py

"""
ChatCompletion 协议类适配器

提供 ChatCompletionRequest、ChatCompletionToolsParam 等。
"""
from dataclasses import dataclass, field
from typing import Any, Optional, List, Dict, Union, Literal

from vllm.entrypoints.openai.engine.protocol import (
    FunctionDefinition,
    UsageInfo,
    OpenAIBaseModel,
)


@dataclass
class ChatCompletionToolsParam:
    """工具参数定义"""
    type: Literal["function"] = "function"
    function: Optional[FunctionDefinition] = None


@dataclass
class ChatCompletionNamedFunction:
    """命名函数"""
    name: str = ""


@dataclass
class ChatCompletionNamedToolChoiceParam:
    """命名工具选择参数"""
    function: ChatCompletionNamedFunction = field(default_factory=ChatCompletionNamedFunction)
    type: Literal["function"] = "function"


# 别名：兼容 vllm 内部引用（vllm 中有时用 NamedToolsChoiceParam）
ChatCompletionNamedToolsChoiceParam = ChatCompletionNamedToolChoiceParam


# 工具选择类型
ToolChoice = Union[
    Literal["none"],
    Literal["auto"],
    Literal["required"],
    ChatCompletionNamedToolChoiceParam,
]


@dataclass
class ChatCompletionRequest(OpenAIBaseModel):
    """ChatCompletion 请求（简化版，仅包含 parser 需要的字段）"""
    messages: List[Dict[str, Any]] = field(default_factory=list)
    model: Optional[str] = None
    tools: Optional[List[ChatCompletionToolsParam]] = None
    tool_choice: ToolChoice = "auto"
    parallel_tool_calls: Optional[bool] = True

    # 以下字段用于 parser 逻辑
    response_format: Optional[Dict[str, Any]] = None
    structured_outputs: Optional[Any] = None
    skip_special_tokens: Optional[bool] = True

    # 流式选项
    stream: Optional[bool] = False
    stream_options: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        """后处理，确保 tools 列表正确初始化"""
        if self.tools is None:
            self.tools = []


@dataclass
class ChatMessage:
    """聊天消息"""
    role: str = ""
    content: Optional[str] = None
    tool_calls: List[Any] = field(default_factory=list)
    reasoning: Optional[str] = None


@dataclass
class ChatCompletionResponseChoice:
    """聊天完成响应选择"""
    index: int = 0
    message: ChatMessage = field(default_factory=ChatMessage)
    finish_reason: Optional[str] = "stop"


@dataclass
class ChatCompletionResponse:
    """聊天完成响应"""
    id: str = ""
    object: Literal["chat.completion"] = "chat.completion"
    created: int = 0
    model: str = ""
    choices: List[ChatCompletionResponseChoice] = field(default_factory=list)
    usage: Optional[UsageInfo] = None
