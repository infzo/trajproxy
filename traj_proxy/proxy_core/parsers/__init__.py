"""
Parser 模块入口

提供统一的 Parser 注册和管理接口。
参考 vLLM 0.16.0 的默认行为：
- reasoning_parser 和 tool_parser 默认为空
- 如果不指定，返回 None，不做解析
- 需要用户通过配置文件或动态注册显式指定 parser
"""

from .parser_manager import ParserManager
from .tool_parser_manager import ToolParserManager
from .reasoning_parser_manager import ReasoningParserManager
from .unified_parser import Parser, DelegatingParser, IdentityParser

from .base import (
    ToolCall,
    DeltaToolCall,
    ExtractedToolCallInfo,
    DeltaMessage,
    BaseToolParser,
    BaseReasoningParser,
    BaseParser,
)


# 注册内置 Parser
def _register_parsers():
    """注册所有内置 Parser"""
    # Tool Parsers 在 tool_parsers/__init__.py 中注册
    # Reasoning Parsers 在 reasoning_parsers/__init__.py 中注册
    pass


# 模块加载时注册
_register_parsers()

# 导出
__all__ = [
    # 管理器
    "ParserManager",
    "ToolParserManager",
    "ReasoningParserManager",
    # 统一 Parser
    "Parser",
    "DelegatingParser",
    "IdentityParser",
    # 基类
    "BaseToolParser",
    "BaseReasoningParser",
    "BaseParser",
    # 数据结构
    "ToolCall",
    "DeltaToolCall",
    "ExtractedToolCallInfo",
    "DeltaMessage",
]
