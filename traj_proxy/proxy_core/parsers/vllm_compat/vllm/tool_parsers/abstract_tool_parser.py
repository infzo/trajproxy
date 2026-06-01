# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project
# Adapted from vllm/tool_parsers/abstract_tool_parser.py

"""
vLLM ToolParser 基类适配器

提供 ToolParser 基类和 ToolParserManager 注册器。
"""
import importlib
import os
from abc import abstractmethod
from collections.abc import Callable, Sequence
from functools import cached_property
from typing import Any, Optional

from vllm.entrypoints.openai.chat_completion.protocol import (
    ChatCompletionRequest,
)
from vllm.entrypoints.openai.engine.protocol import (
    DeltaMessage,
    ExtractedToolCallInformation,
)
from vllm.logger import init_logger
from vllm.tokenizers import TokenizerLike
from vllm.tool_parsers.utils import Tool, get_json_schema_from_tools
from vllm.utils.collection_utils import is_list_of
from vllm.utils.import_utils import import_from_path

logger = init_logger(__name__)

__all__ = ["Tool", "ToolParser", "ToolParserManager"]


class ToolParser:
    """
    抽象 ToolParser 基类，不应直接使用。
    提供的属性和方法应在子类中使用。
    """

    def __init__(self, tokenizer: TokenizerLike, tools: list[Tool] | None = None):
        self.prev_tool_call_arr: list[dict] = []
        # 当前正在解析的工具调用索引
        self.current_tool_id: int = -1
        self.current_tool_name_sent: bool = False
        self.streamed_args_for_tool: list[str] = []
        self.tools = tools

        self.model_tokenizer = tokenizer

    @cached_property
    def vocab(self) -> dict[str, int]:
        # 注意：只有 PreTrainedTokenizerFast 保证有 .vocab
        # 但所有 tokenizer 都有 .get_vocab()
        return self.model_tokenizer.get_vocab()

    def adjust_request(self, request: ChatCompletionRequest) -> ChatCompletionRequest:
        """
        调整请求参数的静态方法。
        """
        if not request.tools:
            return request

        json_schema_from_tool = get_json_schema_from_tools(
            tool_choice=request.tool_choice, tools=request.tools
        )

        # 设置结构化输出参数以支持工具调用
        if json_schema_from_tool is not None:
            # 简化实现：直接设置 response_format
            request.response_format = json_schema_from_tool

        return request

    @abstractmethod
    def extract_tool_calls(
        self, model_output: str, request: ChatCompletionRequest
    ) -> ExtractedToolCallInformation:
        """
        从完整的模型输出中提取工具调用。
        用于非流式响应，我们有完整的模型响应可用。
        静态方法，因为它是无状态的。
        """
        raise NotImplementedError(
            "AbstractToolParser.extract_tool_calls has not been implemented!"
        )

    @abstractmethod
    def extract_tool_calls_streaming(
        self,
        previous_text: str,
        current_text: str,
        delta_text: str,
        previous_token_ids: Sequence[int],
        current_token_ids: Sequence[int],
        delta_token_ids: Sequence[int],
        request: ChatCompletionRequest,
    ) -> DeltaMessage | None:
        """
        从不完整的响应中提取工具调用；
        用于处理工具调用和流式输出。
        必须是实例方法，因为它需要状态 -
        当前的 tokens/diffs，以及之前解析和提取的信息（见构造函数）
        """
        raise NotImplementedError(
            "AbstractToolParser.extract_tool_calls_streaming has not been implemented!"
        )


class ToolParserManager:
    """
    ToolParser 实现的中心注册表。

    支持两种模式：
      - 通过 `register_module` 立即（即时）注册
      - 通过 `register_lazy_module` 延迟注册
    """

    tool_parsers: dict[str, type[ToolParser]] = {}
    lazy_parsers: dict[str, tuple[str, str]] = {}  # name -> (module_path, class_name)

    @classmethod
    def get_tool_parser(cls, name: str) -> type[ToolParser]:
        """
        获取已注册或延迟注册的 ToolParser 类。

        如果 parser 是延迟注册的，它将在首次访问时导入并缓存。
        如果未找到则抛出 KeyError。
        """
        if name in cls.tool_parsers:
            return cls.tool_parsers[name]

        if name in cls.lazy_parsers:
            return cls._load_lazy_parser(name)

        raise KeyError(f"Tool parser '{name}' not found.")

    @classmethod
    def _load_lazy_parser(cls, name: str) -> type[ToolParser]:
        """导入并注册延迟加载的 parser"""
        module_path, class_name = cls.lazy_parsers[name]
        try:
            mod = importlib.import_module(module_path)
            parser_cls = getattr(mod, class_name)
            if not issubclass(parser_cls, ToolParser):
                raise TypeError(
                    f"{class_name} in {module_path} is not a ToolParser subclass."
                )
            cls.tool_parsers[name] = parser_cls  # 缓存
            return parser_cls
        except Exception as e:
            logger.exception(
                "Failed to import lazy tool parser '%s' from %s: %s",
                name,
                module_path,
                e,
            )
            raise

    @classmethod
    def _register_module(
        cls,
        module: type[ToolParser],
        module_name: str | list[str] | None = None,
        force: bool = True,
    ) -> None:
        """立即注册 ToolParser 类"""
        if not issubclass(module, ToolParser):
            raise TypeError(
                f"module must be subclass of ToolParser, but got {type(module)}"
            )

        if module_name is None:
            module_name = module.__name__

        if isinstance(module_name, str):
            module_names = [module_name]
        elif is_list_of(module_name, str):
            module_names = module_name
        else:
            raise TypeError("module_name must be str, list[str], or None.")

        for name in module_names:
            if not force and name in cls.tool_parsers:
                existed = cls.tool_parsers[name]
                raise KeyError(f"{name} is already registered at {existed.__module__}")
            cls.tool_parsers[name] = module

    @classmethod
    def register_lazy_module(cls, name: str, module_path: str, class_name: str) -> None:
        """
        注册延迟模块映射。

        示例：
            ToolParserManager.register_lazy_module(
                name="qwen3_coder",
                module_path="vllm.tool_parsers.qwen3coder_tool_parser",
                class_name="Qwen3CoderToolParser",
            )
        """
        cls.lazy_parsers[name] = (module_path, class_name)

    @classmethod
    def register_module(
        cls,
        name: str | list[str] | None = None,
        force: bool = True,
        module: type[ToolParser] | None = None,
    ) -> type[ToolParser] | Callable[[type[ToolParser]], type[ToolParser]]:
        """
        立即注册模块或作为装饰器延迟注册。

        用法：
            @ToolParserManager.register_module("qwen3_coder")
            class Qwen3CoderToolParser(ToolParser):
                ...

        或者：
            ToolParserManager.register_module(module=SomeToolParser)
        """
        if not isinstance(force, bool):
            raise TypeError(f"force must be a boolean, but got {type(force)}")

        # 立即注册
        if module is not None:
            cls._register_module(module=module, module_name=name, force=force)
            return module

        # 装饰器用法 - 延迟注册
        def _decorator(obj: type[ToolParser]) -> type[ToolParser]:
            module_path = obj.__module__
            class_name = obj.__name__

            if isinstance(name, str):
                names = [name]
            elif name is not None and is_list_of(name, str):
                names = name
            else:
                names = [class_name]

            for n in names:
                # 延迟映射：现在不导入
                cls.lazy_parsers[n] = (module_path, class_name)

            return obj

        return _decorator

    @classmethod
    def list_registered(cls) -> list[str]:
        """返回所有已注册和延迟注册的 tool parser 名称"""
        return sorted(set(cls.tool_parsers.keys()) | set(cls.lazy_parsers.keys()))

    @classmethod
    def import_tool_parser(cls, plugin_path: str) -> None:
        """从任意路径导入用户定义的 parser 文件"""

        module_name = os.path.splitext(os.path.basename(plugin_path))[0]
        try:
            import_from_path(module_name, plugin_path)
        except Exception:
            logger.exception(
                "Failed to load module '%s' from %s.", module_name, plugin_path
            )


# 别名，方便使用
get_tool_parser = ToolParserManager.get_tool_parser
register_tool_parser = ToolParserManager.register_module
list_tool_parsers = ToolParserManager.list_registered

__all__ = [
    "ToolParser",
    "ToolParserManager",
    "get_tool_parser",
    "register_tool_parser",
    "list_tool_parsers",
]
