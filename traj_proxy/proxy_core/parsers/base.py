"""
基础数据结构和抽象类
参考 vLLM 0.16.0 接口设计，保持兼容性
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import cached_property
from typing import Optional, List, Any, Dict


# ==================== 数据结构 ====================

@dataclass
class ToolCall:
    """工具调用"""
    id: str
    type: str = "function"
    name: str = ""
    arguments: str = ""


@dataclass
class DeltaToolCall:
    """流式工具调用增量"""
    id: Optional[str] = None
    type: Optional[str] = None
    index: int = 0
    name: Optional[str] = None
    arguments: Optional[str] = None


@dataclass
class ExtractedToolCallInfo:
    """提取的工具调用信息"""
    tools_called: bool
    tool_calls: List[ToolCall]
    content: Optional[str] = None


@dataclass
class DeltaMessage:
    """流式增量消息"""
    role: Optional[str] = None
    content: Optional[str] = None
    reasoning: Optional[str] = None
    tool_calls: List[DeltaToolCall] = field(default_factory=list)

    def __post_init__(self):
        """确保 tool_calls 不为 None"""
        if self.tool_calls is None:
            self.tool_calls = []


# ==================== 抽象基类 ====================

class BaseToolParser(ABC):
    """
    工具解析器基类

    参考vLLM的ToolParser设计，提供工具调用的解析能力。
    支持非流式和流式两种模式。
    """

    def __init__(self, tokenizer=None):
        self.tokenizer = tokenizer
        # 流式状态
        self.prev_tool_call_arr: List[Dict] = []
        self.current_tool_id: int = -1
        self.current_tool_name_sent: bool = False
        self.streamed_args_for_tool: List[str] = []

    @cached_property
    def vocab(self) -> Dict[str, int]:
        """获取词表"""
        if self.tokenizer is None:
            return {}
        if hasattr(self.tokenizer, 'get_vocab'):
            return self.tokenizer.get_vocab()
        return {}

    @abstractmethod
    def extract_tool_calls(
        self,
        model_output: str,
        tools: Optional[List[dict]] = None,
        request: Optional[Any] = None
    ) -> ExtractedToolCallInfo:
        """
        非流式解析工具调用

        Args:
            model_output: 模型输出的完整文本
            tools: 工具定义列表（用于参数类型转换）
            request: 请求对象（兼容vLLM接口）

        Returns:
            ExtractedToolCallInfo
        """
        pass

    def extract_tool_calls_streaming(
        self,
        previous_text: str,
        current_text: str,
        delta_text: str,
        previous_token_ids: Sequence[int],
        current_token_ids: Sequence[int],
        delta_token_ids: Sequence[int],
        tools: Optional[List[dict]] = None,
        request: Optional[Any] = None
    ) -> Optional[DeltaMessage]:
        """
        流式解析工具调用（可选实现）

        Args:
            previous_text: 之前的累积文本
            current_text: 当前的累积文本
            delta_text: 本次增量文本
            previous_token_ids: 之前的 token IDs
            current_token_ids: 当前的 token IDs
            delta_token_ids: 本次增量的 token IDs
            tools: 工具定义列表
            request: 请求对象

        Returns:
            DeltaMessage 或 None
        """
        return None

    def adjust_request(self, request: Any) -> Any:
        """
        调整请求参数（用于结构化输出等场景）

        Args:
            request: 原始请求对象

        Returns:
            调整后的请求对象
        """
        return request

    def reset_streaming_state(self):
        """重置流式状态（每个新请求开始时调用）"""
        self.prev_tool_call_arr = []
        self.current_tool_id = -1
        self.current_tool_name_sent = False
        self.streamed_args_for_tool = []


class BaseReasoningParser(ABC):
    """
    推理内容解析器基类

    参考vLLM的ReasoningParser设计，提供推理内容的解析能力。
    支持提取模型输出中的思维链内容。
    """

    def __init__(self, tokenizer=None, **kwargs):
        self.tokenizer = tokenizer
        self._kwargs = kwargs

    @cached_property
    def vocab(self) -> Dict[str, int]:
        """获取词表"""
        if self.tokenizer is None:
            return {}
        if hasattr(self.tokenizer, 'get_vocab'):
            return self.tokenizer.get_vocab()
        return {}

    @property
    @abstractmethod
    def start_token(self) -> str:
        """推理开始标记"""
        pass

    @property
    @abstractmethod
    def end_token(self) -> str:
        """推理结束标记"""
        pass

    @abstractmethod
    def is_reasoning_end(self, input_ids: Sequence[int]) -> bool:
        """
        检查推理内容是否结束

        Args:
            input_ids: token ID 序列

        Returns:
            True 如果推理已结束
        """
        pass

    @abstractmethod
    def extract_content_ids(self, input_ids: List[int]) -> List[int]:
        """
        提取非推理内容的 token IDs

        Args:
            input_ids: 完整的 token ID 序列

        Returns:
            推理内容之后的 token IDs
        """
        pass

    @abstractmethod
    def extract_reasoning(
        self,
        model_output: str,
        request: Optional[Any] = None
    ) -> tuple[Optional[str], Optional[str]]:
        """
        非流式解析推理内容

        Args:
            model_output: 模型输出的完整文本
            request: 请求对象

        Returns:
            (reasoning, content): 推理内容和剩余内容
        """
        pass

    def extract_reasoning_streaming(
        self,
        previous_text: str,
        current_text: str,
        delta_text: str,
        previous_token_ids: Sequence[int],
        current_token_ids: Sequence[int],
        delta_token_ids: Sequence[int]
    ) -> Optional[DeltaMessage]:
        """
        流式解析推理内容（可选实现）
        """
        return None

    def is_reasoning_end_streaming(
        self,
        input_ids: Sequence[int],
        delta_ids: Sequence[int]
    ) -> bool:
        """
        流式模式下检查推理是否结束

        Args:
            input_ids: 完整的 token ID 序列
            delta_ids: 本次增量的 token IDs

        Returns:
            True 如果推理在本次增量中结束
        """
        return self.is_reasoning_end(input_ids)

    def reset_streaming_state(self):
        """重置流式状态"""
        pass


class BaseParser(ABC):
    """
    统一 Parser 基类

    参考vLLM的Parser设计，同时支持 reasoning 和 tool 解析。
    这是统一接口的核心抽象类。
    """

    def __init__(self, tokenizer=None, **kwargs):
        self.tokenizer = tokenizer
        self._reasoning_parser: Optional[BaseReasoningParser] = None
        self._tool_parser: Optional[BaseToolParser] = None

    @cached_property
    def vocab(self) -> Dict[str, int]:
        """获取词表"""
        if self.tokenizer is None:
            return {}
        if hasattr(self.tokenizer, 'get_vocab'):
            return self.tokenizer.get_vocab()
        return {}

    @property
    def reasoning_parser(self) -> Optional[BaseReasoningParser]:
        """获取底层 reasoning parser"""
        return self._reasoning_parser

    @reasoning_parser.setter
    def reasoning_parser(self, parser: Optional[BaseReasoningParser]):
        self._reasoning_parser = parser

    @property
    def tool_parser(self) -> Optional[BaseToolParser]:
        """获取底层 tool parser"""
        return self._tool_parser

    @tool_parser.setter
    def tool_parser(self, parser: Optional[BaseToolParser]):
        self._tool_parser = parser

    # ==================== Reasoning 方法 ====================

    @abstractmethod
    def is_reasoning_end(self, input_ids: List[int]) -> bool:
        """检查推理内容是否结束"""
        pass

    @abstractmethod
    def extract_content_ids(self, input_ids: List[int]) -> List[int]:
        """提取非推理内容的 token IDs"""
        pass

    @abstractmethod
    def extract_reasoning(
        self,
        model_output: str,
        request: Optional[Any] = None
    ) -> tuple[Optional[str], Optional[str]]:
        """非流式解析推理内容"""
        pass

    @abstractmethod
    def extract_reasoning_streaming(
        self,
        previous_text: str,
        current_text: str,
        delta_text: str,
        previous_token_ids: Sequence[int],
        current_token_ids: Sequence[int],
        delta_token_ids: Sequence[int]
    ) -> Optional[DeltaMessage]:
        """流式解析推理内容"""
        pass

    # ==================== Tool 方法 ====================

    def adjust_request(self, request: Any) -> Any:
        """调整请求参数"""
        if self._tool_parser:
            return self._tool_parser.adjust_request(request)
        return request

    @abstractmethod
    def extract_tool_calls(
        self,
        model_output: str,
        tools: Optional[List[dict]] = None,
        request: Optional[Any] = None
    ) -> ExtractedToolCallInfo:
        """非流式解析工具调用"""
        pass

    @abstractmethod
    def extract_tool_calls_streaming(
        self,
        previous_text: str,
        current_text: str,
        delta_text: str,
        previous_token_ids: Sequence[int],
        current_token_ids: Sequence[int],
        delta_token_ids: Sequence[int],
        tools: Optional[List[dict]] = None,
        request: Optional[Any] = None
    ) -> Optional[DeltaMessage]:
        """流式解析工具调用"""
        pass
