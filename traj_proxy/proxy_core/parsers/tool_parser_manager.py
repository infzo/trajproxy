"""
Tool Parser 管理器
参考 vLLM 0.16.0 的 ToolParserManager 设计

继承 BaseParserManager，提供 Tool Parser 的注册和管理功能。
"""

from typing import Type, Callable, Optional, List

from .base import BaseToolParser
from .base_parser_manager import BaseParserManager


class ToolParserManager(BaseParserManager[BaseToolParser]):
    """
    Tool Parser 中央注册中心

    继承 BaseParserManager，提供 Tool Parser 的注册和管理功能。
    """

    # 已加载的 parser 类（实例级存储，避免类级别共享）
    _parsers: dict[str, Type[BaseToolParser]] = {}

    # 懒加载映射：name -> (module_path, class_name)
    _lazy_parsers: dict[str, tuple[str, str]] = {}

    @classmethod
    def _get_parser_base_class(cls) -> Type[BaseToolParser]:
        """获取 Tool Parser 基类"""
        return BaseToolParser

    @classmethod
    def get_tool_parser(cls, name: str) -> Type[BaseToolParser]:
        """
        获取已注册的 ToolParser 类

        如果是懒加载注册，首次访问时会导入并缓存。

        Args:
            name: parser 名称

        Returns:
            ToolParser 类

        Raises:
            KeyError: 未找到指定名称的 parser
        """
        return cls.get_parser(name)
