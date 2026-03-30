"""
PromptBuilder - 消息构建器（增强版）

负责将 OpenAI Messages 转换为 PromptText，以及构建 OpenAI 格式的响应。
支持 Tool Parser 和 Reasoning Parser。
"""

from typing import List, Dict, Any, Optional
import time
import traceback
from transformers import AutoTokenizer

from traj_proxy.proxy_core.context import ProcessContext
from traj_proxy.proxy_core.parsers import ParserManager
from traj_proxy.proxy_core.parsers.base import (
    DeltaMessage, DeltaToolCall, ToolCall, ExtractedToolCallInfo
)
from traj_proxy.utils.logger import get_logger

logger = get_logger(__name__)


class PromptBuilder:
    """消息构建器 - 负责将 OpenAI Messages 转换为 PromptText

    使用 transformers 的 AutoTokenizer.apply_chat_template() 方法
    将 OpenAI 格式的 messages 转换为模型特定的 prompt 格式。

    支持：
    1. 从 infer_response 提取 tool_calls
    2. 从响应文本解析 tool_calls
    3. 从响应文本解析 reasoning
    """

    def __init__(self, model: str, tokenizer_path: str, tool_parser: str = "", reasoning_parser: str = ""):
        """初始化 PromptBuilder

        Args:
            model: 模型名称（用于响应中的 model 字段）
            tokenizer_path: Tokenizer 路径，用于加载模型特定的聊天模板
            tool_parser: Tool parser 名称，空字符串表示不做解析
            reasoning_parser: Reasoning parser 名称，空字符串表示不做解析
        """
        self.model = model
        self.tokenizer_path = tokenizer_path
        self.tokenizer = AutoTokenizer.from_pretrained(tokenizer_path)

        # 初始化 Parser（使用显式指定的 parser 名称）
        self.tool_parser, self.reasoning_parser = ParserManager.create_parsers(
            tool_parser_name=tool_parser,
            reasoning_parser_name=reasoning_parser,
            tokenizer=self.tokenizer
        )

        if self.tool_parser:
            logger.info(f"[{model}] 已加载 Tool Parser: {self.tool_parser.__class__.__name__}")
        if self.reasoning_parser:
            logger.info(f"[{model}] 已加载 Reasoning Parser: {self.reasoning_parser.__class__.__name__}")

    def __enter__(self):
        """进入上下文管理器，自动重置流式状态"""
        self.reset_streaming_state()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出上下文管理器，确保状态重置"""
        self.reset_streaming_state()
        return False

    async def build_prompt_text(
        self,
        messages: List[Dict[str, Any]],
        context: ProcessContext
    ) -> str:
        """将 OpenAI Messages 转换为 PromptText

        使用 tokenizer.apply_chat_template() 方法，
        将 OpenAI 格式的 messages 转换为模型特定的 prompt 格式。

        Args:
            messages: OpenAI Message 格式的消息列表
            context: 处理上下文

        Returns:
            模型特定的 prompt 文本
        """
        return self.tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True
        )

    # ==================== 非流式响应构建 ====================

    def build_openai_response(
        self,
        response_text: str,
        context: ProcessContext,
        model: Optional[str] = None
    ) -> Dict[str, Any]:
        """构建 OpenAI 格式的响应（增强版）

        支持：
        1. 从 infer_response 获取 tool_calls
        2. 从响应文本解析 tool_calls
        3. 从响应文本解析 reasoning

        Args:
            response_text: 模型生成的响应文本
            context: 处理上下文，包含 infer_response 信息
            model: 可选的模型名称，默认使用初始化时指定的 model

        Returns:
            OpenAI 格式的响应字典
        """
        tool_calls = None
        reasoning = None
        content = response_text
        finish_reason = "stop"

        # 1. 尝试从 infer_response 获取 tool_calls（现有逻辑）
        if context.infer_response:
            choices = context.infer_response.get("choices", [])
            if choices:
                tool_calls = choices[0].get("tool_calls")
                finish_reason = choices[0].get("finish_reason", "stop")

        # 2. 如果没有预置 tool_calls，尝试从响应文本解析
        if not tool_calls and self.tool_parser:
            try:
                tools = context.request_params.get("tools")
                extracted = self.tool_parser.extract_tool_calls(
                    response_text,
                    tools=tools
                )
                if extracted.tools_called:
                    tool_calls = [
                        {
                            "id": tc.id,
                            "type": tc.type,
                            "function": {
                                "name": tc.name,
                                "arguments": tc.arguments
                            }
                        }
                        for tc in extracted.tool_calls
                    ]
                    content = extracted.content
                    finish_reason = "tool_calls"
                    logger.debug(f"[{context.unique_id}] 从文本解析到 {len(tool_calls)} 个工具调用")
            except Exception as e:
                logger.warning(f"[{context.unique_id}] 工具调用解析失败: {e}\n{traceback.format_exc()}")

        # 3. 解析推理内容
        if self.reasoning_parser:
            try:
                include_reasoning = context.request_params.get("include_reasoning", True)
                if include_reasoning:
                    extracted_reasoning, extracted_content = self.reasoning_parser.extract_reasoning(
                        content or response_text
                    )
                    if extracted_reasoning:
                        reasoning = extracted_reasoning
                        content = extracted_content
                        logger.debug(f"[{context.unique_id}] 解析到推理内容，长度: {len(reasoning)}")
            except Exception as e:
                logger.warning(f"[{context.unique_id}] 推理解析失败: {e}\n{traceback.format_exc()}")

        # 构建消息
        message = {
            "role": "assistant",
            "content": content
        }

        if reasoning:
            message["reasoning"] = reasoning

        if tool_calls:
            message["tool_calls"] = tool_calls

        response_data = {
            "id": f"chatcmpl-{context.request_id}",
            "object": "chat.completion",
            "created": int(context.start_time.timestamp()) if context.start_time else 0,
            "model": model or self.model,
            "choices": [{
                "index": 0,
                "message": message,
                "finish_reason": finish_reason
            }],
            "usage": {
                "prompt_tokens": context.prompt_tokens or 0,
                "completion_tokens": context.completion_tokens or 0,
                "total_tokens": context.total_tokens or 0
            }
        }

        return response_data

    # ==================== 流式响应构建 ====================

    def build_stream_chunk(
        self,
        content: str,
        context: ProcessContext,
        finish_reason: Optional[str] = None,
        tool_calls_delta: Optional[List[Dict[str, Any]]] = None,
        reasoning_delta: Optional[str] = None
    ) -> Dict[str, Any]:
        """构建 OpenAI 格式的流式响应块（增强版）

        Args:
            content: 本次输出的内容片段
            context: 处理上下文
            finish_reason: 结束原因（仅最后一个 chunk 有值）
            tool_calls_delta: 工具调用的增量数据
            reasoning_delta: 推理内容的增量

        Returns:
            OpenAI 格式的 chunk 字典
        """
        chunk = {
            "id": f"chatcmpl-{context.request_id}",
            "object": "chat.completion.chunk",
            "created": int(context.start_time.timestamp()) if context.start_time else int(time.time()),
            "model": self.model,
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": finish_reason
            }]
        }

        delta = {}

        # 第一个 chunk 包含 role
        if context.stream_chunk_count == 0:
            delta["role"] = "assistant"

        # 添加内容
        if content:
            delta["content"] = content

        # 添加推理内容
        if reasoning_delta:
            delta["reasoning"] = reasoning_delta

        # 添加 tool_calls 增量
        if tool_calls_delta:
            delta["tool_calls"] = tool_calls_delta

        chunk["choices"][0]["delta"] = delta
        return chunk

    def build_stream_chunk_with_tool_calls(
        self,
        context: ProcessContext,
        tool_calls: List[Dict[str, Any]],
        finish_reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """构建包含完整 tool_calls 的流式响应块

        Args:
            context: 处理上下文
            tool_calls: 工具调用列表
            finish_reason: 结束原因

        Returns:
            OpenAI 格式的 chunk 字典
        """
        chunk = {
            "id": f"chatcmpl-{context.request_id}",
            "object": "chat.completion.chunk",
            "created": int(context.start_time.timestamp()) if context.start_time else int(time.time()),
            "model": self.model,
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": finish_reason
            }]
        }

        # 第一个 chunk 包含 role
        if context.stream_chunk_count == 0:
            chunk["choices"][0]["delta"]["role"] = "assistant"

        # 添加 tool_calls
        if tool_calls:
            chunk["choices"][0]["delta"]["tool_calls"] = tool_calls

        return chunk

    # ==================== 流式解析处理 ====================

    def reset_streaming_state(self):
        """重置流式解析状态（每个新请求开始时调用）"""
        if self.tool_parser:
            self.tool_parser.reset_streaming_state()
        if self.reasoning_parser:
            self.reasoning_parser.reset_streaming_state()

    def process_streaming_parse(
        self,
        delta_text: str,
        context: ProcessContext,
        previous_text: str,
        current_text: str,
        previous_token_ids: List[int],
        current_token_ids: List[int],
        delta_token_ids: List[int]
    ) -> tuple[str, Optional[List[Dict]], Optional[str]]:
        """处理流式解析

        协调 Tool Parser 和 Reasoning Parser 的流式解析。

        Args:
            delta_text: 本次增量文本
            context: 处理上下文
            previous_text: 之前的累积文本
            current_text: 当前的累积文本
            previous_token_ids: 之前的 token IDs
            current_token_ids: 当前的 token IDs
            delta_token_ids: 本次增量的 token IDs

        Returns:
            (content_delta, tool_calls_delta, reasoning_delta)
        """
        content_delta = delta_text
        tool_calls_delta = None
        reasoning_delta = None

        tools = context.request_params.get("tools")
        include_reasoning = context.request_params.get("include_reasoning", True)

        # 1. 先处理推理解析（可能改变内容归属）
        if self.reasoning_parser and include_reasoning:
            try:
                delta_msg = self.reasoning_parser.extract_reasoning_streaming(
                    previous_text=previous_text,
                    current_text=current_text,
                    delta_text=delta_text,
                    previous_token_ids=previous_token_ids,
                    current_token_ids=current_token_ids,
                    delta_token_ids=delta_token_ids
                )
                if delta_msg:
                    content_delta = delta_msg.content
                    reasoning_delta = delta_msg.reasoning
            except Exception as e:
                logger.warning(f"[{context.unique_id}] 推理流式解析失败: {e}\n{traceback.format_exc()}")

        # 2. 处理工具调用解析
        if self.tool_parser and tools:
            try:
                delta_msg = self.tool_parser.extract_tool_calls_streaming(
                    previous_text=previous_text,
                    current_text=current_text,
                    delta_text=delta_text,
                    previous_token_ids=previous_token_ids,
                    current_token_ids=current_token_ids,
                    delta_token_ids=delta_token_ids,
                    tools=tools
                )
                if delta_msg:
                    if delta_msg.tool_calls:
                        tool_calls_delta = [
                            {
                                "index": tc.index,
                                "id": tc.id,
                                "type": tc.type,
                                "function": {
                                    "name": tc.name,
                                    "arguments": tc.arguments
                                } if tc.name or tc.arguments else None
                            }
                            for tc in delta_msg.tool_calls
                        ]
                    if delta_msg.content is not None:
                        content_delta = delta_msg.content
            except Exception as e:
                logger.warning(f"[{context.unique_id}] 工具调用流式解析失败: {e}\n{traceback.format_exc()}")

        return content_delta, tool_calls_delta, reasoning_delta

    def get_finish_reason_from_parser(self, context: ProcessContext) -> Optional[str]:
        """从 Parser 获取结束原因

        Args:
            context: 处理上下文

        Returns:
            finish_reason 或 None
        """
        if self.tool_parser:
            # 检查是否有工具调用
            if hasattr(self.tool_parser, 'prev_tool_call_arr'):
                if self.tool_parser.prev_tool_call_arr:
                    return "tool_calls"
        return None
