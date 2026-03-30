"""
Reasoning Parser 管理器
参考 vLLM 0.16.0 的 ReasoningParserManager 设计

继承 BaseParserManager，提供 Reasoning Parser 的注册和管理功能。
"""

from typing import Type

from .base import BaseReasoningParser
from .base_parser_manager import BaseParserManager


class ReasoningParserManager(BaseParserManager[BaseReasoningParser]):
    """
    Reasoning Parser 中央注册中心

    继承 BaseParserManager，提供 Reasoning Parser 的注册和管理功能。
    """

    # 已加载的 parser 类（实例级存储，避免类级别共享）
    _parsers: dict[str, Type[BaseReasoningParser]] = {}

    # 懒加载映射：name -> (module_path, class_name)
    _lazy_parsers: dict[str, tuple[str, str]] = {}

    @classmethod
    def _get_parser_base_class(cls) -> Type[BaseReasoningParser]:
        """获取 Reasoning Parser 基类"""
        return BaseReasoningParser

    @classmethod
    def get_reasoning_parser(cls, name: str) -> Type[BaseReasoningParser]:
        """
        获取已注册的 ReasoningParser 类

        如果是懒加载注册，首次访问时会导入并缓存。

        Args:
            name: parser 名称

        Returns:
            ReasoningParser 类

        Raises:
            KeyError: 未找到指定名称的 parser
        """
        return cls.get_parser(name)
