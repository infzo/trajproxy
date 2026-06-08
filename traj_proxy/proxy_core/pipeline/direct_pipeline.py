"""
DirectPipeline - 直接转发管道

直接将请求转发到推理服务，不经过 token 编码/解码流程。
"""

from typing import AsyncIterator, Dict, Any, Optional, List, TYPE_CHECKING
import time

from traj_proxy.proxy_core.pipeline.base import BasePipeline
from traj_proxy.proxy_core.context import ProcessContext
from traj_proxy.utils.logger import get_logger

if TYPE_CHECKING:
    from traj_proxy.proxy_core.infer_client import InferClient
    from traj_proxy.store.request_repository import RequestRepository

logger = get_logger(__name__)


def _merge_stream_tool_calls(tool_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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
        if tc.get("id", None):
            merged[idx]["id"] = tc["id"]
        if tc.get("type", None):
            merged[idx]["type"] = tc["type"]
        # 合并 function
        func = tc.get("function", None)
        if func:
            if func.get("name", None):
                merged[idx]["function"]["name"] = func["name"]
            if func.get("arguments", None):
                merged[idx]["function"]["arguments"] += func["arguments"]

    return list(merged.values())


class DirectPipeline(BasePipeline):
    """直接转发管道

    直接将 OpenAI 格式请求
    转发到推理服务的 /v1/chat/completions 接口。

    处理流程：
    raw_request → infer_client → raw_response
    """

    def __init__(
        self,
        model: str,
        infer_client: "InferClient",
        request_repository: Optional["RequestRepository"] = None
    ):
        """初始化 DirectPipeline

        Args:
            model: 模型名称
            infer_client: 推理服务客户端
            request_repository: 请求记录仓库（可选）
        """
        super().__init__(model, infer_client, request_repository)

    async def process(
        self,
        messages: list,
        context: ProcessContext
    ) -> ProcessContext:
        """处理非流式请求

        直接转发到推理服务的 chat completions 接口。

        Args:
            messages: OpenAI 格式的消息列表
            context: 处理上下文

        Returns:
            处理后的上下文
        """
        logger.info(
            f"[{context.unique_id}] 开始处理请求（直接转发模式）: "
            f"model={self.model}, messages_count={len(messages)}"
        )

        try:
            # 直接转发到推理服务
            t0 = time.perf_counter()
            context.raw_response = await self.infer_client.send_chat_completion(
                messages=messages,
                model=self.model,
                extra_headers=context.forward_headers,
                **context.request_params
            )
            context.inference_duration_ms = (time.perf_counter() - t0) * 1000

            # 提取响应信息用于存储
            if "choices" in context.raw_response and context.raw_response["choices"]:
                choice = context.raw_response["choices"][0]
                message = choice.get("message", {})
                context.response_text = message.get("content", "")

                # 提取 vLLM 扩展的 token_ids 字段，用于轨迹记录
                # vLLM 在 return_token_ids=True 时会在 choices[0] 中返回 token_ids
                if choice.get("token_ids") is not None:
                    context.response_ids = choice["token_ids"]

            # 提取顶级 prompt_token_ids（vLLM 扩展字段）
            # 用于轨迹记录的 token_ids 列
            if context.raw_response.get("prompt_token_ids") is not None:
                context.token_ids = context.raw_response["prompt_token_ids"]

            # 拼接完整对话 token ids（prompt + response）
            if context.token_ids and context.response_ids:
                context.full_conversation_token_ids = context.token_ids + context.response_ids

            # 提取 usage 信息
            if "usage" in context.raw_response:
                usage = context.raw_response["usage"]
                context.prompt_tokens = usage.get("prompt_tokens", 0)
                context.completion_tokens = usage.get("completion_tokens", 0)
                context.total_tokens = usage.get("total_tokens", 0)

            self._update_timing(context)
            logger.info(
                f"[{context.unique_id}] 直接转发请求完成: "
                f"duration_ms={context.processing_duration_ms:.2f}, "
                f"inference_ms={context.inference_duration_ms:.2f}"
            )

            # 存储到数据库（保留完整的 logprobs/token_ids）
            await self._store_trajectory(context, run_id=context.run_id)

            # 确保响应中所有可选字段有默认值
            self._ensure_response_defaults(context.raw_response)

            return context

        except Exception as e:
            self._handle_error(context, e)
            # 即使出错也尝试存储
            await self._store_trajectory(context, run_id=context.run_id)
            raise

    async def process_stream(
        self,
        messages: list,
        context: ProcessContext
    ) -> AsyncIterator[Dict[str, Any]]:
        """处理流式请求

        直接转发到推理服务的 chat completions 流式接口。

        Args:
            messages: OpenAI 格式的消息列表
            context: 处理上下文

        Yields:
            OpenAI 格式的流式响应块
        """
        logger.info(
            f"[{context.unique_id}] 开始流式处理请求（直接转发模式）: "
            f"model={self.model}, messages_count={len(messages)}"
        )

        try:
            # 直接转发到推理服务的流式接口
            first_chunk_received = False
            infer_start_time = time.perf_counter()

            async for chunk in self.infer_client.send_chat_completion_stream(
                messages=messages,
                model=self.model,
                extra_headers=context.forward_headers,
                **context.request_params
            ):
                # 记录TTFT（首Token时间）
                if not first_chunk_received:
                    context.ttft_ms = (time.perf_counter() - infer_start_time) * 1000
                    first_chunk_received = True

                # 累积流式响应中的所有字段
                self._accumulate_stream_fields(context, chunk)
                context.stream_chunk_count += 1
                yield chunk

            # 记录推理总耗时
            context.inference_duration_ms = (time.perf_counter() - infer_start_time) * 1000

            # 流式结束后更新上下文
            await self._finalize_stream(context)

        except Exception as e:
            self._handle_error(context, e)
            raise

    def _accumulate_stream_fields(
        self,
        context: ProcessContext,
        chunk: Dict[str, Any]
    ):
        """累积流式响应字段

        同时捕获顶级响应元数据（id, model, created, system_fingerprint 等）
        和完整的 usage 对象，用于在 _finalize_stream 中构建与非流式一致的 raw_response。

        Args:
            context: 处理上下文
            chunk: 流式响应块
        """
        # 累积顶级响应元数据（排除 choices、usage 和 prompt_token_ids，这三个单独处理）
        # 从每个 chunk 中捕获非 choices/usage/prompt_token_ids 的字段，用于构建 raw_response
        if context.stream_response_metadata is None:
            context.stream_response_metadata = {}
        for key, value in chunk.items():
            if key not in ("choices", "usage", "prompt_token_ids") and value is not None:
                context.stream_response_metadata[key] = value

        # 累积 prompt_token_ids（vLLM 扩展字段，顶级字段，通常在首chunk中返回完整的prompt token列表）
        # 与 usage 类似，单独处理而非依赖通用元数据循环，确保即使后续chunk覆盖也不会丢失
        if "prompt_token_ids" in chunk and chunk["prompt_token_ids"] is not None:
            # 首chunk包含完整列表，直接赋值；后续chunk不含此字段，不会覆盖
            context.stream_prompt_token_ids = chunk["prompt_token_ids"]

        # 先处理 usage 信息（可能在没有 choices 的单独 chunk 中）
        # vLLM 在流式结束时发送一个只包含 usage 的 chunk
        if "usage" in chunk and chunk["usage"]:
            # 捕获完整的 usage 对象，保留所有子字段（如 prompt_tokens_details 等）
            context.stream_usage_full = chunk["usage"]
            # 仍然提取个别字段用于 context 快捷访问
            usage = chunk["usage"]
            if usage.get("prompt_tokens", None) is not None:
                context.prompt_tokens = usage["prompt_tokens"]
            if usage.get("completion_tokens", None) is not None:
                context.completion_tokens = usage["completion_tokens"]
            if usage.get("total_tokens", None) is not None:
                context.total_tokens = usage["total_tokens"]

        if "choices" not in chunk or not chunk["choices"]:
            return

        choice = chunk["choices"][0]
        delta = choice.get("delta", {})

        # 1. 累积 role
        if "role" in delta and delta["role"]:
            context.stream_role = delta["role"]

        # 2. 累积 content
        if "content" in delta and delta["content"]:
            context.stream_buffer_text += delta["content"]

        # 3. 累积 reasoning（vLLM 扩展）
        if "reasoning" in delta and delta["reasoning"]:
            context.stream_reasoning += delta["reasoning"]
        elif "reasoning_content" in delta and delta["reasoning_content"]:
            context.stream_reasoning += delta["reasoning_content"]

        # 4. 累积 tool_calls
        if "tool_calls" in delta and delta["tool_calls"]:
            if context.stream_tool_calls is None:
                context.stream_tool_calls = []
            context.stream_tool_calls.extend(delta["tool_calls"])

        # 5. 累积 function_call（旧版格式兼容）
        if "function_call" in delta and delta["function_call"]:
            fc = delta["function_call"]
            if context.stream_function_call is None:
                context.stream_function_call = {"name": "", "arguments": ""}
            if fc.get("name", None):
                context.stream_function_call["name"] = fc["name"]
            if fc.get("arguments", None):
                context.stream_function_call["arguments"] += fc["arguments"]

        # 6. 累积 logprobs（流式模式下每个 chunk 是增量 delta，需追加 content 列表）
        if "logprobs" in choice and choice["logprobs"]:
            chunk_logprobs = choice["logprobs"]
            if context.stream_logprobs is None:
                context.stream_logprobs = chunk_logprobs
            else:
                # 合并 content 列表（增量追加，与 token_ids 的 extend 逻辑一致）
                if chunk_logprobs.get("content"):
                    if context.stream_logprobs.get("content") is None:
                        context.stream_logprobs["content"] = []
                    context.stream_logprobs["content"].extend(chunk_logprobs["content"])

        # 7. 累积 vLLM 扩展字段
        if "stop_reason" in choice and choice["stop_reason"] is not None:
            context.stream_stop_reason = choice["stop_reason"]
        if "token_ids" in choice and choice["token_ids"]:
            if context.stream_token_ids is None:
                context.stream_token_ids = []
            context.stream_token_ids.extend(choice["token_ids"])

        # 8. 检查是否结束
        finish_reason = choice.get("finish_reason", None)
        if finish_reason:
            context.stream_finished = True
            context.stream_finish_reason = finish_reason

    async def _finalize_stream(self, context: ProcessContext):
        """完成流式处理

        Args:
            context: 处理上下文
        """
        context.response_text = context.stream_buffer_text
        self._update_timing(context)

        # 如果后端服务未返回 usage 信息，估算 token 数量
        if not context.completion_tokens and context.response_text:
            # 估算：假设平均每 4 个字符约 1 个 token
            context.completion_tokens = len(context.response_text) // 4
            context.total_tokens = (context.prompt_tokens or 0) + context.completion_tokens

        # 构建最终响应
        message = {
            "role": context.stream_role or "assistant",
            "content": context.response_text or None,
            "annotations": None,
            "audio": None,
            "function_call": context.stream_function_call,
            "refusal": None,
        }

        # 添加 reasoning（vLLM 扩展）
        if context.stream_reasoning:
            message["reasoning"] = context.stream_reasoning
            message["reasoning_content"] = context.stream_reasoning

        # 添加 tool_calls
        if context.stream_tool_calls:
            message["tool_calls"] = _merge_stream_tool_calls(context.stream_tool_calls)

        # 添加 function_call（旧版兼容）
        if context.stream_function_call:
            message["function_call"] = context.stream_function_call

        # 构建选择项
        choice = {
            "index": 0,
            "message": message,
            "logprobs": context.stream_logprobs,
            "token_ids": context.stream_token_ids,
            "finish_reason": context.stream_finish_reason or "stop"
        }

        # logprobs/token_ids 已在 choice 中
        # 添加 vLLM 扩展字段（stop_reason 总是返回）
        if context.stream_stop_reason is not None:
            choice["stop_reason"] = context.stream_stop_reason

        # 使用累积的响应元数据构建 raw_response，保持与非流式结构一致
        # 流式 chunk 的 object 是 "chat.completion.chunk"，需改为非流式格式
        if context.stream_response_metadata:
            context.raw_response = dict(context.stream_response_metadata)
            context.raw_response["object"] = "chat.completion"
        else:
            # 如果没有捕获元数据（不应发生），使用合成值兜底
            context.raw_response = {
                "id": f"chatcmpl-{context.request_id}",
                "object": "chat.completion",
                "created": int(context.start_time.timestamp()),
                "model": self.model,
            }

        # 替换 choices（用累积数据重建）
        context.raw_response["choices"] = [choice]

        # 替换 usage（优先使用后端返回的完整 usage 对象）
        if context.stream_usage_full:
            context.raw_response["usage"] = context.stream_usage_full
        else:
            context.raw_response["usage"] = {
                "prompt_tokens": context.prompt_tokens,
                "completion_tokens": context.completion_tokens,
                "total_tokens": context.total_tokens
            }

        # 确保顶级字段默认值
        for key in ("kv_transfer_params", "prompt_logprobs",
                    "prompt_token_ids", "service_tier", "system_fingerprint"):
            context.raw_response.setdefault(key, None)

        # 确保 usage 子字段默认值
        usage_obj = context.raw_response.get("usage", {}) or {}
        usage_obj.setdefault("prompt_tokens_details", None)
        usage_obj.setdefault("completion_tokens_details", None)
        context.raw_response["usage"] = usage_obj

        # 提取累积的 token_ids 字段到 context，用于轨迹记录独立列
        if context.stream_prompt_token_ids is not None:
            context.token_ids = context.stream_prompt_token_ids
        if context.stream_token_ids is not None:
            context.response_ids = context.stream_token_ids
        # 拼接完整对话 token ids（prompt + response）
        if context.token_ids and context.response_ids:
            context.full_conversation_token_ids = context.token_ids + context.response_ids

        # 直接转发模式下不构建 token_response（与非流式保持一致）
        # logprobs 和 token_ids 已包含在 raw_response 的 choices 中

        ttft_str = f"{context.ttft_ms:.2f}" if context.ttft_ms else "N/A"
        inference_str = f"{context.inference_duration_ms:.2f}" if context.inference_duration_ms else "N/A"
        logger.info(
            f"[{context.unique_id}] 流式处理完成（直接转发模式）: "
            f"chunks={context.stream_chunk_count}, "
            f"duration_ms={context.processing_duration_ms:.2f}, "
            f"ttft_ms={ttft_str}, inference_ms={inference_str}"
        )

        # 存储到数据库
        await self._store_trajectory(context, run_id=context.run_id)

    @staticmethod
    def _ensure_response_defaults(response: Dict[str, Any]) -> None:
        """确保响应中所有可选字段有默认值（原地修改）"""
        if not response or "choices" not in response:
            return
        for choice in response.get("choices", []):
            choice.setdefault("logprobs", None)
            choice.setdefault("token_ids", None)
            msg = choice.get("message", {}) or {}
            msg.setdefault("annotations", None)
            msg.setdefault("audio", None)
            msg.setdefault("function_call", None)
            msg.setdefault("refusal", None)
        for key in ("kv_transfer_params", "prompt_logprobs",
                    "prompt_token_ids", "service_tier", "system_fingerprint"):
            response.setdefault(key, None)
        usage_obj = response.get("usage", {}) or {}
        usage_obj.setdefault("prompt_tokens_details", None)
        usage_obj.setdefault("completion_tokens_details", None)
        response["usage"] = usage_obj
