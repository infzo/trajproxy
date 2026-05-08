"""
StreamChunkBuilder - 流式响应块构建器

专门处理流式场景下的增量响应构建。
"""

from typing import Any, Dict, Optional, List, TYPE_CHECKING
import time

from traj_proxy.proxy_core.builders.base import BaseResponseBuilder
from traj_proxy.utils.logger import get_logger

if TYPE_CHECKING:
    from traj_proxy.proxy_core.context import ProcessContext
    from traj_proxy.proxy_core.parsers.parser_manager import Parser

logger = get_logger(__name__)


class StreamChunkBuilder(BaseResponseBuilder):
    """流式响应块构建器

    专门处理流式场景，支持：
    1. 增量内容构建
    2. 工具调用增量构建
    3. 推理内容增量构建
    """

    def __init__(self, model: str, parser: "Parser"):
        """初始化 StreamChunkBuilder

        Args:
            model: 模型名称
            parser: Parser 实例
        """
        self.model = model
        self.parser = parser

    def build(
        self,
        content: str,
        context: "ProcessContext"
    ) -> Dict[str, Any]:
        """构建完整响应（流式场景下通常不使用，但需要实现接口）

        Args:
            content: 响应内容
            context: 处理上下文

        Returns:
            OpenAI 格式的响应字典
        """
        # 流式场景下，build 方法构建最终汇总的响应
        message = {
            "role": "assistant",
            "content": content or None
        }

        # 添加 reasoning
        if context.stream_reasoning:
            message["reasoning"] = context.stream_reasoning

        # 添加 tool_calls
        if context.stream_tool_calls:
            message["tool_calls"] = self._merge_stream_tool_calls(context.stream_tool_calls)

        # 添加 function_call（旧版兼容）
        if context.stream_function_call:
            message["function_call"] = context.stream_function_call

        # 构建选择项
        choice = {
            "index": 0,
            "message": message,
            "finish_reason": context.stream_finish_reason or "stop"
        }

        # 仅在请求参数中要求时才返回 logprobs 给客户端
        if context.stream_logprobs and context.request_params.get('logprobs'):
            choice["logprobs"] = context.stream_logprobs

        # 添加 vLLM 扩展字段（stop_reason 总是返回）
        if context.stream_stop_reason is not None:
            choice["stop_reason"] = context.stream_stop_reason
        # 仅在请求参数中要求时才返回 token_ids 给客户端
        if context.stream_token_ids and context.request_params.get('return_token_ids'):
            choice["token_ids"] = context.stream_token_ids

        return {
            "id": f"chatcmpl-{context.request_id}",
            "object": "chat.completion",
            "created": int(context.start_time.timestamp()),
            "model": self.model,
            "choices": [choice],
            "usage": {
                "prompt_tokens": context.prompt_tokens,
                "completion_tokens": context.completion_tokens,
                "total_tokens": context.total_tokens
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
        """构建流式响应块

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

        # 添加推理内容（支持 reasoning 和 reasoning_content 两种格式）
        if reasoning_delta:
            delta["reasoning"] = reasoning_delta
            delta["reasoning_content"] = reasoning_delta  # Backwards compatibility

        # 添加 tool_calls 增量
        if tool_calls_delta:
            delta["tool_calls"] = tool_calls_delta

        chunk["choices"][0]["delta"] = delta
        return chunk

    def build_from_delta(
        self,
        delta_content: str,
        delta_tool_calls: Optional[List[Dict[str, Any]]],
        delta_reasoning: Optional[str],
        context: "ProcessContext",
        finish_reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """从解析结果构建流式响应块

        Args:
            delta_content: 增量内容
            delta_tool_calls: 增量工具调用
            delta_reasoning: 增量推理内容
            context: 处理上下文
            finish_reason: 结束原因

        Returns:
            OpenAI 格式的 chunk 字典
        """
        return self.build_chunk(
            content=delta_content,
            context=context,
            finish_reason=finish_reason,
            tool_calls_delta=delta_tool_calls,
            reasoning_delta=delta_reasoning
        )

    def _merge_stream_tool_calls(
        self,
        tool_calls: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """合并流式返回的增量 tool_calls

        流式响应中，同一个 tool_call 会分成多个 chunk 返回，
        需要按 index 合并。

        Args:
            tool_calls: 流式累积的 tool_calls 列表

        Returns:
            合并后的 tool_calls 列表
        """
        if not tool_calls:
            return []

        merged = {}
        for tc in tool_calls:
            if not tc:  # 跳过空字典
                continue

            idx = tc.get("index", 0)
            if idx not in merged:
                merged[idx] = {
                    "id": tc.get("id", ""),
                    "type": tc.get("type", "function"),
                    "function": {"name": "", "arguments": ""}
                }
            # 更新 id 和 type
            if tc.get("id"):
                merged[idx]["id"] = tc["id"]
            if tc.get("type"):
                merged[idx]["type"] = tc["type"]
            # 合并 function
            func = tc.get("function")
            if func:
                if func.get("name"):
                    merged[idx]["function"]["name"] = func["name"]
                if func.get("arguments"):
                    merged[idx]["function"]["arguments"] += func["arguments"]

        return list(merged.values())
