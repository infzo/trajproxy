"""
MessageConverter - 消息转换器

负责将 OpenAI Messages 转换为模型特定的 PromptText。
"""

from typing import List, Dict, Any, Optional
import json
import copy
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from traj_proxy.proxy_core.converters.base import BaseConverter
from traj_proxy.proxy_core.context import ProcessContext
from traj_proxy.utils.logger import get_logger

logger = get_logger(__name__)


class MessageConverter(BaseConverter):
    """消息转换器 - 将 OpenAI Messages 转换为 PromptText

    使用 transformers 的 AutoTokenizer.apply_chat_template() 方法
    将 OpenAI 格式的 messages 转换为模型特定的 prompt 格式。

    支持自定义 Jinja 模板（TITO 模式），确保多轮对话一致性，
    从而使前缀匹配缓存正常工作。
    """

    def __init__(
        self,
        tokenizer,
        tito_template_path: Optional[str] = None
    ):
        """初始化 MessageConverter

        Args:
            tokenizer: transformers.PreTrainedTokenizerBase 实例
            tito_template_path: TITO 模式使用的 jinja 模板路径，
                用于确保多轮对话一致性。如果提供，将优先使用此模板。
        """
        self.tokenizer = tokenizer
        self.tito_template_path = tito_template_path
        self._tito_template = None

        # 加载 TITO 模板（如果提供）
        if tito_template_path:
            self._tito_template = self._load_jinja_template(tito_template_path)
            logger.info(f"TITO 模板已加载: {tito_template_path}")

    def _load_jinja_template(self, path: str):
        """加载自定义 Jinja 模板

        Args:
            path: 模板文件路径

        Returns:
            Jinja2 Template 对象

        Raises:
            FileNotFoundError: 模板文件不存在
        """
        template_path = Path(path)
        if not template_path.exists():
            raise FileNotFoundError(f"TITO 模板文件不存在: {path}")

        env = Environment(loader=FileSystemLoader(template_path.parent))
        return env.get_template(template_path.name)

    async def convert(
        self,
        messages: List[Dict[str, Any]],
        context: ProcessContext
    ) -> str:
        """将 OpenAI Messages 转换为 PromptText

        TITO 模式下使用自定义 Jinja 模板，确保多轮一致性。
        普通模式下使用 tokenizer 默认模板。

        支持 tools、documents、tool_choice 等参数传递给 chat template。

        Args:
            messages: OpenAI Message 格式的消息列表
            context: 处理上下文

        Returns:
            模型特定的 prompt 文本
        """
        # 预处理消息，解决格式兼容性问题
        processed_messages = self._preprocess_messages(messages)

        # TITO 模式：使用自定义 Jinja 模板
        if self._tito_template:
            return self._render_with_tito_template(processed_messages, context)

        # 普通模式：使用 tokenizer 默认模板
        return self._render_with_default_template(processed_messages, context)

    def _render_with_tito_template(
        self,
        messages: List[Dict[str, Any]],
        context: ProcessContext
    ) -> str:
        """使用 TITO 模板渲染

        Args:
            messages: 预处理后的消息列表
            context: 处理上下文

        Returns:
            渲染后的 prompt 文本
        """
        template_kwargs = {
            "messages": messages,
            "add_generation_prompt": True,
        }

        # 从 request_params 提取 tools 相关参数
        tools = context.request_params.get("tools")
        if tools:
            template_kwargs["tools"] = tools

        # 从 request_params 提取 documents 参数（RAG 场景）
        documents = context.request_params.get("documents")
        if documents:
            template_kwargs["documents"] = documents

        # 从 request_params 提取 tool_choice 参数
        tool_choice = context.request_params.get("tool_choice")
        if tool_choice:
            template_kwargs["tool_choice"] = tool_choice

        return self._tito_template.render(**template_kwargs)

    def _render_with_default_template(
        self,
        messages: List[Dict[str, Any]],
        context: ProcessContext
    ) -> str:
        """使用 tokenizer 默认模板渲染

        Args:
            messages: 预处理后的消息列表
            context: 处理上下文

        Returns:
            渲染后的 prompt 文本
        """
        # 构建 apply_chat_template 参数
        template_kwargs = {
            "tokenize": False,
            "add_generation_prompt": True
        }

        # 从 request_params 提取 tools 相关参数
        tools = context.request_params.get("tools")
        if tools:
            template_kwargs["tools"] = tools

        # 从 request_params 提取 documents 参数（RAG 场景）
        documents = context.request_params.get("documents")
        if documents:
            template_kwargs["documents"] = documents

        # 从 request_params 提取 tool_choice 参数
        # 简单传递策略：依赖模型的 chat template 处理
        tool_choice = context.request_params.get("tool_choice")
        if tool_choice:
            template_kwargs["tool_choice"] = tool_choice

        return self.tokenizer.apply_chat_template(
            messages,
            **template_kwargs
        )

    def _preprocess_messages(
        self,
        messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """预处理消息列表

        解决 OpenAI 格式与 tokenizer chat template 的兼容性问题：
        1. 将 tool_call.function.arguments 从 JSON 字符串解析为字典
           OpenAI API 中 arguments 是 JSON 字符串，但某些模型的 chat template
           期望它已经是字典对象（如 Qwen 模板会调用 |items 过滤器）

        Args:
            messages: 原始消息列表

        Returns:
            预处理后的消息列表
        """
        processed_messages = []

        for message in messages:
            msg = copy.deepcopy(message)

            # 处理 assistant 消息中的 tool_calls
            if msg.get('role') == 'assistant' and msg.get('tool_calls'):
                for tool_call in msg['tool_calls']:
                    if 'function' in tool_call and 'arguments' in tool_call['function']:
                        args = tool_call['function']['arguments']
                        # 如果 arguments 是 JSON 字符串，解析为字典
                        if isinstance(args, str):
                            try:
                                tool_call['function']['arguments'] = json.loads(args)
                            except json.JSONDecodeError:
                                # 解析失败则保持原样
                                pass

            processed_messages.append(msg)

        return processed_messages
