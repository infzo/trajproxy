"""
OpenAIResponseBuilder - OpenAI 格式响应构建器

负责构建 OpenAI 格式的完整响应，支持工具调用和推理内容解析。
"""

from typing import Any, Dict, Optional, List, TYPE_CHECKING
import traceback

from traj_proxy.proxy_core.builders.base import BaseResponseBuilder
from traj_proxy.utils.logger import get_logger

if TYPE_CHECKING:
    from traj_proxy.proxy_core.context import ProcessContext
    from traj_proxy.proxy_core.parsers.parser_manager import Parser

logger = get_logger(__name__)


class OpenAIResponseBuilder(BaseResponseBuilder):
    """OpenAI 格式响应构建器

    支持：
    1. 从响应文本解析 tool_calls
    2. 从响应文本解析 reasoning
    3. 构建标准 OpenAI 响应格式
    """

    def __init__(self, model: str, parser: "Parser"):
        """初始化 OpenAIResponseBuilder

        Args:
            model: 模型名称
            parser: Parser 实例（用于解析工具调用和推理内容）
        """
        self.model = model
        self.parser = parser

    def build(
        self,
        content: str,
        context: "ProcessContext"
    ) -> Dict[str, Any]:
        """构建 OpenAI 格式的完整响应

        Args:
            content: 响应内容
            context: 处理上下文

        Returns:
            OpenAI 格式的响应字典
        """
        tool_calls = None
        reasoning = None
        final_content = content
        finish_reason = "stop"

        # 1. 尝试从 token_response 获取预置的 tool_calls
        infer_response = getattr(context, "token_response", None)
        if infer_response:
            choices = infer_response.get("choices", [])
            if choices:
                tool_calls = choices[0].get("tool_calls")
                finish_reason = choices[0].get("finish_reason", "stop")

        # 2. 如果没有预置 tool_calls，尝试从响应文本解析
        if not tool_calls:
            try:
                result = self.parser.extract_tool_calls(
                    content, context.raw_request
                )
                if result.tools_called and result.tool_calls:
                    tool_calls = [
                        {
                            "id": tc.id,
                            "type": tc.type,
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments
                            }
                        }
                        for tc in result.tool_calls
                    ]
                    final_content = result.content
                    finish_reason = "tool_calls"
                    logger.debug(f"[{context.unique_id}] 从文本解析到 {len(tool_calls)} 个工具调用")
            except Exception as e:
                logger.warning(f"[{context.unique_id}] 工具调用解析失败: {e}\n{traceback.format_exc()}")

        # 3. 解析推理内容
        try:
            include_reasoning = context.request_params.get("include_reasoning", True)
            if include_reasoning:
                extracted_reasoning, extracted_content = self.parser.extract_reasoning(
                    final_content or content, context.raw_request
                )
                if extracted_reasoning:
                    reasoning = extracted_reasoning
                    final_content = extracted_content
                    logger.debug(f"[{context.unique_id}] 解析到推理内容，长度: {len(reasoning)}")
        except Exception as e:
            logger.warning(f"[{context.unique_id}] 推理解析失败: {e}\n{traceback.format_exc()}")

        # 构建消息体
        message = {
            "role": "assistant",
            "content": final_content
        }

        if reasoning:
            message["reasoning"] = reasoning

        if tool_calls:
            message["tool_calls"] = tool_calls

        choice = {
            "index": 0,
            "message": message,
            "finish_reason": finish_reason
        }

        # 仅在请求参数中要求时才返回 logprobs 给客户端
        if context.request_params.get('logprobs') and context.token_response:
            choices = context.token_response.get("choices", [])
            if choices and "logprobs" in choices[0]:
                choice["logprobs"] = choices[0]["logprobs"]

        # 仅在请求参数中要求时才返回 token_ids 给客户端
        if context.request_params.get('return_token_ids') and context.response_ids:
            choice["token_ids"] = context.response_ids

        return {
            "id": f"chatcmpl-{context.request_id}",
            "object": "chat.completion",
            "created": int(context.start_time.timestamp()) if context.start_time else 0,
            "model": self.model,
            "choices": [choice],
            "usage": {
                "prompt_tokens": context.prompt_tokens or 0,
                "completion_tokens": context.completion_tokens or 0,
                "total_tokens": context.total_tokens or 0
            }
        }

    def build_chunk(
        self,
        content: str,
        context: "ProcessContext",
        finish_reason: Optional[str] = None,
        tool_calls_delta: Optional[List[Dict[str, Any]]] = None,
        reasoning_delta: Optional[str] = None
    ) -> Dict[str, Any]:
        """构建 OpenAI 格式的流式响应块

        Args:
            content: 本次输出的内容片段
            context: 处理上下文
            finish_reason: 结束原因（仅最后一个 chunk 有值）
            tool_calls_delta: 工具调用的增量数据
            reasoning_delta: 推理内容的增量

        Returns:
            OpenAI 格式的 chunk 字典
        """
        import time

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

    def build_chunk_with_tool_calls(
        self,
        context: "ProcessContext",
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
        import time

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
