"""
Reasoning Parser 管理器
参考 vLLM 0.16.0 的 ReasoningParserManager 设计

支持两种注册模式：
1. 急切注册：立即加载 parser 类
2. 懒加载注册：延迟导入，首次使用时加载
"""

import importlib
import os
from typing import Type, Callable, Optional, List

from .base import BaseReasoningParser


class ReasoningParserManager:
    """
    Reasoning Parser 中央注册中心

    支持：
    - 急切注册 via register_module
    - 懒加载注册 via register_lazy_module
    - 装饰器注册
    - 动态导入自定义 parser
    """

    # 已加载的 parser 类
    reasoning_parsers: dict[str, Type[BaseReasoningParser]] = {}

    # 懒加载映射：name -> (module_path, class_name)
    lazy_parsers: dict[str, tuple[str, str]] = {}

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
        if name in cls.reasoning_parsers:
            return cls.reasoning_parsers[name]

        if name in cls.lazy_parsers:
            return cls._load_lazy_parser(name)

        registered = ", ".join(cls.list_registered())
        raise KeyError(
            f"Reasoning parser '{name}' not found. Available parsers: {registered}"
        )

    @classmethod
    def _load_lazy_parser(cls, name: str) -> Type[BaseReasoningParser]:
        """
        加载懒加载的 parser

        Args:
            name: parser 名称

        Returns:
            加载的 ReasoningParser 类
        """
        module_path, class_name = cls.lazy_parsers[name]
        try:
            mod = importlib.import_module(module_path)
            parser_cls = getattr(mod, class_name)
            if not issubclass(parser_cls, BaseReasoningParser):
                raise TypeError(
                    f"{class_name} in {module_path} is not a BaseReasoningParser subclass."
                )
            cls.reasoning_parsers[name] = parser_cls  # 缓存
            return parser_cls
        except Exception as e:
            import traceback
            raise ImportError(
                f"Failed to import lazy reasoning parser '{name}' from {module_path}: {e}\n{traceback.format_exc()}"
            ) from e

    @classmethod
    def _register_module(
        cls,
        module: Type[BaseReasoningParser],
        module_name: str | List[str] | None = None,
        force: bool = True,
    ) -> None:
        """
        立即注册 ReasoningParser 类

        Args:
            module: ReasoningParser 类
            module_name: 注册名称（可以是多个）
            force: 是否覆盖已存在的注册
        """
        if not issubclass(module, BaseReasoningParser):
            raise TypeError(
                f"module must be subclass of BaseReasoningParser, but got {type(module)}"
            )

        if module_name is None:
            module_names = [module.__name__]
        elif isinstance(module_name, str):
            module_names = [module_name]
        else:
            module_names = module_name

        for name in module_names:
            if not force and name in cls.reasoning_parsers:
                existed = cls.reasoning_parsers[name]
                raise KeyError(f"{name} is already registered at {existed.__module__}")
            cls.reasoning_parsers[name] = module

    @classmethod
    def register_lazy_module(
        cls,
        name: str,
        module_path: str,
        class_name: str
    ) -> None:
        """
        注册懒加载模块映射

        Args:
            name: parser 名称
            module_path: 模块路径
            class_name: 类名

        Example:
            ReasoningParserManager.register_lazy_module(
                name="qwen3",
                module_path="traj_proxy.proxy_core.parsers.reasoning_parsers.qwen3_reasoning_parser",
                class_name="Qwen3ReasoningParser",
            )
        """
        cls.lazy_parsers[name] = (module_path, class_name)

    @classmethod
    def register_module(
        cls,
        name: str | List[str] | None = None,
        force: bool = True,
        module: Type[BaseReasoningParser] | None = None,
    ) -> Type[BaseReasoningParser] | Callable[[Type[BaseReasoningParser]], Type[BaseReasoningParser]]:
        """
        注册 ReasoningParser 类

        可作为装饰器或直接调用使用。

        Args:
            name: 注册名称
            force: 是否覆盖已存在的注册
            module: ReasoningParser 类（直接调用时使用）

        Returns:
            装饰器或注册的类

        Usage:
            @ReasoningParserManager.register_module("my_parser")
            class MyReasoningParser(BaseReasoningParser):
                ...
        """
        if not isinstance(force, bool):
            raise TypeError(f"force must be a boolean, but got {type(force)}")

        # 直接注册
        if module is not None:
            cls._register_module(module=module, module_name=name, force=force)
            return module

        # 装饰器模式
        def _decorator(obj: Type[BaseReasoningParser]) -> Type[BaseReasoningParser]:
            module_path = obj.__module__
            class_name = obj.__name__

            if isinstance(name, str):
                names = [name]
            elif isinstance(name, list):
                names = name
            else:
                names = [class_name]

            for n in names:
                cls.lazy_parsers[n] = (module_path, class_name)

            return obj

        return _decorator

    @classmethod
    def list_registered(cls) -> List[str]:
        """
        列出所有已注册的 parser 名称

        Returns:
            排序后的 parser 名称列表
        """
        return sorted(set(cls.reasoning_parsers.keys()) | set(cls.lazy_parsers.keys()))

    @classmethod
    def import_parser(cls, plugin_path: str) -> None:
        """
        从文件路径导入自定义 parser

        Args:
            plugin_path: parser 文件的绝对路径
        """
        module_name = os.path.splitext(os.path.basename(plugin_path))[0]
        try:
            spec = importlib.util.spec_from_file_location(module_name, plugin_path)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
        except Exception as e:
            import traceback
            raise ImportError(
                f"Failed to load module '{module_name}' from {plugin_path}: {e}\n{traceback.format_exc()}"
            ) from e

    @classmethod
    def clear(cls) -> None:
        """清空所有注册（用于测试）"""
        cls.reasoning_parsers.clear()
        cls.lazy_parsers.clear()
