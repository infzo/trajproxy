"""
统一 Parser 管理器
参考 vLLM 0.16.0 的 ParserManager 设计

委托给 ToolParserManager 和 ReasoningParserManager，
并提供统一的 Parser 获取接口。

默认行为（与 vLLM 一致）：
- reasoning_parser 和 tool_parser 默认为空字符串
- 如果不指定，返回 None，不做解析
- 需要用户显式指定 parser（通过配置文件或动态注册）
"""

import os
import traceback
from typing import Type, Optional, Dict, Any, List

from .base import BaseToolParser, BaseReasoningParser, BaseParser
from .tool_parser_manager import ToolParserManager
from .reasoning_parser_manager import ReasoningParserManager
from .unified_parser import _WrappedParser, IdentityParser


class ParserManager:
    """
    统一 Parser 管理器

    提供：
    - 统一的 Parser 获取接口
    - 动态导入自定义 parser

    默认行为：
    - 如果 reasoning_parser_name 为空，不做 reasoning 解析
    - 如果 tool_parser_name 为空，不做 tool 解析
    - 如果都为空，返回 None
    """

    @classmethod
    def get_tool_parser_cls(cls, name: str) -> Optional[Type[BaseToolParser]]:
        """
        获取 ToolParser 类

        Args:
            name: parser 名称，空字符串返回 None

        Returns:
            ToolParser 类或 None
        """
        # 空字符串或 None 表示不做解析
        if not name:
            return None
        try:
            return ToolParserManager.get_tool_parser(name)
        except KeyError:
            return None

    @classmethod
    def get_reasoning_parser_cls(cls, name: str) -> Optional[Type[BaseReasoningParser]]:
        """
        获取 ReasoningParser 类

        Args:
            name: parser 名称，空字符串返回 None

        Returns:
            ReasoningParser 类或 None
        """
        # 空字符串或 None 表示不做解析
        if not name:
            return None
        try:
            return ReasoningParserManager.get_reasoning_parser(name)
        except KeyError:
            return None

    @classmethod
    def get_parser(
        cls,
        tool_parser_name: Optional[str] = None,
        reasoning_parser_name: Optional[str] = None,
    ) -> Optional[Type[BaseParser]]:
        """
        获取统一的 Parser 类

        参考 vLLM 的行为：
        - 如果 tool_parser_name 和 reasoning_parser_name 都为空，返回 None
        - 否则创建动态 Parser 类组合独立的 parser

        Args:
            tool_parser_name: tool parser 名称，空字符串表示不做解析
            reasoning_parser_name: reasoning parser 名称，空字符串表示不做解析

        Returns:
            Parser 类或 None（都不指定时）

        注意：返回的类在实例化时会捕获当前的 parser 类，
        避免了类属性修改的并发安全问题。
        """
        # 都不指定，返回 None（不做任何解析）
        if not tool_parser_name and not reasoning_parser_name:
            return None

        # 获取 parser 类
        reasoning_parser_cls = cls.get_reasoning_parser_cls(reasoning_parser_name)
        tool_parser_cls = cls.get_tool_parser_cls(tool_parser_name)

        # 如果都获取不到，返回 None
        if reasoning_parser_cls is None and tool_parser_cls is None:
            return None

        # 创建动态子类，通过闭包捕获 parser 类（避免类属性并发问题）
        # 使用工厂函数创建类，确保每个调用都有独立的配置
        def create_parser_class(
            reasoning_cls: Optional[Type[BaseReasoningParser]],
            tool_cls: Optional[Type[BaseToolParser]]
        ) -> Type[_WrappedParser]:
            class DynamicWrappedParser(_WrappedParser):
                """动态创建的包装 Parser，捕获特定的 parser 类"""
                pass

            # 保存原始 __init__ 签名用于类型提示
            def __init__(self, tokenizer=None, **kwargs):
                super(DynamicWrappedParser, self).__init__(
                    tokenizer=tokenizer,
                    reasoning_parser_cls=reasoning_cls,
                    tool_parser_cls=tool_cls,
                    **kwargs
                )

            DynamicWrappedParser.__init__ = __init__
            DynamicWrappedParser.__name__ = f"DynamicWrappedParser_{reasoning_parser_name or 'none'}_{tool_parser_name or 'none'}"

            return DynamicWrappedParser

        return create_parser_class(reasoning_parser_cls, tool_parser_cls)

    @classmethod
    def get_identity_parser(cls) -> Type[BaseParser]:
        """
        获取恒等 Parser（不做解析）

        Returns:
            IdentityParser 类
        """
        return IdentityParser

    @classmethod
    def create_parsers(
        cls,
        tool_parser_name: Optional[str] = None,
        reasoning_parser_name: Optional[str] = None,
        tokenizer=None
    ) -> tuple[Optional[BaseToolParser], Optional[BaseReasoningParser]]:
        """
        创建 Parser 实例

        Args:
            tool_parser_name: tool parser 名称，None/空字符串表示不做解析
            reasoning_parser_name: reasoning parser 名称，None/空字符串表示不做解析
            tokenizer: tokenizer 实例

        Returns:
            (tool_parser, reasoning_parser) 元组，未指定的返回 None
        """
        tool_parser = None
        reasoning_parser = None

        if tool_parser_name:
            parser_cls = cls.get_tool_parser_cls(tool_parser_name)
            if parser_cls:
                tool_parser = parser_cls(tokenizer)

        if reasoning_parser_name:
            parser_cls = cls.get_reasoning_parser_cls(reasoning_parser_name)
            if parser_cls:
                reasoning_parser = parser_cls(tokenizer)

        return tool_parser, reasoning_parser

    @classmethod
    def create_parser(
        cls,
        tool_parser_name: Optional[str] = None,
        reasoning_parser_name: Optional[str] = None,
        tokenizer=None
    ) -> Optional[BaseParser]:
        """
        创建统一的 Parser 实例

        Args:
            tool_parser_name: tool parser 名称
            reasoning_parser_name: reasoning parser 名称
            tokenizer: tokenizer 实例

        Returns:
            Parser 实例或 None（都不指定时）
        """
        parser_cls = cls.get_parser(tool_parser_name, reasoning_parser_name)
        if parser_cls:
            return parser_cls(tokenizer)
        # 都不指定，返回 None（不做解析）
        return None

    @classmethod
    def list_tool_parsers(cls) -> List[str]:
        """列出所有注册的工具解析器"""
        return ToolParserManager.list_registered()

    @classmethod
    def list_reasoning_parsers(cls) -> List[str]:
        """列出所有注册的推理解析器"""
        return ReasoningParserManager.list_registered()

    @classmethod
    def import_parser(cls, plugin_path: str) -> None:
        """
        从文件路径导入自定义 parser

        Args:
            plugin_path: parser 文件的绝对路径
        """
        module_name = os.path.splitext(os.path.basename(plugin_path))[0]
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(module_name, plugin_path)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
        except Exception as e:
            raise ImportError(
                f"Failed to load module '{module_name}' from {plugin_path}: {e}\n{traceback.format_exc()}"
            ) from e

    @classmethod
    def clear(cls) -> None:
        """清空所有注册（用于测试）"""
        ToolParserManager.clear()
        ReasoningParserManager.clear()
