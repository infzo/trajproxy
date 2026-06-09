"""
OpenAIResponseBuilder - OpenAI 格式响应构建器

负责构建 OpenAI 格式的完整响应，支持工具调用和推理内容解析。
"""

import json
import uuid
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

        # 1. 从 token_response 获取预置字段
        infer_response = getattr(context, "token_response", None)
        choice_logprobs = None
        choice_token_ids = None
        msg_refusal = None
        msg_audio = None
        msg_function_call = None
        if infer_response:
            choices = infer_response.get("choices", [])
            if choices:
                choice0 = choices[0]
                tool_calls = choice0.get("tool_calls")
                finish_reason = choice0.get("finish_reason", "stop")
                choice_logprobs = choice0.get("logprobs")
                choice_token_ids = choice0.get("token_ids")
                msg0 = choice0.get("message", {}) or {}
                msg_refusal = msg0.get("refusal")
                msg_audio = msg0.get("audio")
                msg_function_call = msg0.get("function_call")

        # 2. 先解析推理内容（对齐 vllm DelegatingParser.extract_response_outputs）
        # vllm 的处理顺序：先 reasoning → 后 tool_calls
        # reasoning parser 先从原始模型输出提取 ─┘ 包裹的推理文本，
        # 返回 reasoning-free 的 content，供 tool parser 在干净文本上解析。
        # 此顺序确保 tool XML 标记（┐┌ 等）不会泄漏到 reasoning_content。
        try:
            include_reasoning = context.request_params.get("include_reasoning", True)
            if include_reasoning:
                extracted_reasoning, extracted_content = self.parser.extract_reasoning(
                    content, context.raw_request
                )
                if extracted_reasoning:
                    reasoning = extracted_reasoning
                    final_content = extracted_content
                    logger.debug(f"[{context.unique_id}] 解析到推理内容，长度: {len(reasoning)}")
        except Exception as e:
            logger.warning(f"[{context.unique_id}] 推理解析失败: {e}\n{traceback.format_exc()}")

        # 3. 从 reasoning-free 的 content 中解析 tool_calls
        # 对齐 vLLM _parse_tool_calls_from_content() 的分支逻辑：
        # - tool_choice=named: 直接取 content 作为 arguments，保留原始格式，
        #   函数名来自请求参数，finish_reason 保留 infer_response 原值
        # - tool_choice=required: 解析 JSON 列表（暂未实现，走 ┐┌ parser）
        # - tool_choice=auto/none: 走 ┐┌ parser，finish_reason 覆盖为 "tool_calls"
        raw_request = context.raw_request or {}
        tool_choice = raw_request.get("tool_choice", "auto")

        if not tool_calls:
            # --- 分支 A: tool_choice=named（ChatCompletionNamedToolChoiceParam）---
            # vLLM 对 named 模式不经过 ┐┌ parser，直接取 content 作为 arguments，
            # 保留原始 JSON 格式（含换行/空格），函数名来自请求。
            # finish_reason 不覆盖（保留 infer_response 的值，通常是 "stop"）。
            is_named_choice = isinstance(tool_choice, dict) and tool_choice.get("type") == "function"
            if is_named_choice:
                func_spec = tool_choice.get("function", {})
                func_name = func_spec.get("name")
                if func_name and final_content:
                    tool_calls = [{
                        "id": f"call_{uuid.uuid4().hex[:24]}",
                        "type": "function",
                        "function": {
                            "name": func_name,
                            "arguments": final_content  # 保留原始格式
                        }
                    }]
                    final_content = None  # 对齐 vLLM: content=None
                    logger.debug(f"[{context.unique_id}] named tool_choice: {func_name}, arguments 保留原始格式")

            # --- 分支 B: tool_choice=required ---
            # vLLM 解析 content 为 JSON 函数列表。
            # 当前 proxy 对 required 也走 ┐┌ parser，测试中结果一致，暂不做特殊处理。
            # 若后续 required 模式出现 arguments 格式不一致，再补充此分支。

            # --- 分支 C: tool_choice=auto/none（默认）---
            if not tool_calls:
                try:
                    result = self.parser.extract_tool_calls(
                        final_content, context.raw_request
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
                        # 仅 auto 模式覆盖 finish_reason
                        # named/required 模式保留 infer_response 原值
                        finish_reason = "tool_calls"
                        logger.debug(f"[{context.unique_id}] 从文本解析到 {len(tool_calls)} 个工具调用")
                except Exception as e:
                    logger.warning(f"[{context.unique_id}] 工具调用解析失败: {e}\n{traceback.format_exc()}")

        # 构建消息体
        # 当有 tool_calls 时，content 至少保留 "" 而非 None
        # 确保 LiteLLM 转换 Claude 格式时生成 text block（对齐 vLLM 三段结构）
        if tool_calls:
            normalized_content = final_content if (final_content and final_content.strip()) else ""
        else:
            # 无 tool_calls 时，保持原有规范化逻辑
            normalized_content = final_content if (final_content and final_content.strip()) else None
        message = {
            "role": "assistant",
            "content": normalized_content,
            "annotations": None,
            "audio": msg_audio,
            "function_call": msg_function_call,
            "refusal": msg_refusal,
        }

        if reasoning:
            message["reasoning"] = reasoning
            message["reasoning_content"] = reasoning

        if tool_calls:
            message["tool_calls"] = tool_calls

        choice = {
            "index": 0,
            "message": message,
            "logprobs": choice_logprobs,
            "token_ids": choice_token_ids,
            "finish_reason": finish_reason,
        }

        raw_usage = (infer_response or {}).get("usage", {}) or {}
        return {
            "id": f"chatcmpl-{context.request_id}",
            "object": "chat.completion",
            "created": int(context.start_time.timestamp()) if context.start_time else 0,
            "model": self.model,
            "choices": [choice],
            "usage": {
                "prompt_tokens": context.prompt_tokens or 0,
                "completion_tokens": context.completion_tokens or 0,
                "total_tokens": context.total_tokens or 0,
                "prompt_tokens_details": raw_usage.get("prompt_tokens_details"),
                "completion_tokens_details": raw_usage.get("completion_tokens_details"),
            },
            "kv_transfer_params": None,
            "prompt_logprobs": None,
            "prompt_token_ids": None,
            "service_tier": None,
            "system_fingerprint": None,
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
