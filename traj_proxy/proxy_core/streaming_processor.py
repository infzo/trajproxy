"""
StreamingProcessor - 流式请求处理器

专门处理流式 LLM 请求，从 Processor 中分离出来。
"""
from typing import AsyncIterator, Dict, Any, Optional, List
from datetime import datetime
import traceback

from traj_proxy.utils.logger import get_logger
from traj_proxy.proxy_core.context import ProcessContext
from traj_proxy.proxy_core.infer_response_parser import InferResponseParser
from traj_proxy.exceptions import DatabaseError

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


class StreamingProcessor:
    """流式请求处理器

    专门处理流式 LLM 请求，职责单一，与 Processor（非流式）分离。

    支持两种模式：
    - Token-in-Token-out 模式：经过 PromptBuilder 和 TokenBuilder
    - 直接转发模式：直接转发到推理服务的 chat completions 接口

    处理流程：
    直接转发模式 (token_in_token_out=False):
      raw_request → 推理服务 → raw_response

    Token模式 (token_in_token_out=True):
      raw_request → text_request → token_request → 推理服务 → token_response → text_response → raw_response
    """

    def __init__(
        self,
        model: str,
        tokenizer_path: Optional[str] = None,
        prompt_builder=None,
        token_builder=None,
        infer_client=None,
        request_repository=None,
        tool_parser_name: str = "",
        reasoning_parser_name: str = ""
    ):
        """初始化 StreamingProcessor

        Args:
            model: 模型名称
            tokenizer_path: Tokenizer 路径（可选）
            prompt_builder: PromptBuilder 实例（可为 None，表示直接转发模式）
            token_builder: TokenBuilder 实例（可为 None，表示直接转发模式）
            infer_client: InferClient 实例
            request_repository: 请求记录仓库
            tool_parser_name: Tool parser 名称
            reasoning_parser_name: Reasoning parser 名称
        """
        self.model = model
        self.tokenizer_path = tokenizer_path
        self.prompt_builder = prompt_builder
        self.token_builder = token_builder
        self.infer_client = infer_client
        self.request_repository = request_repository
        self.tool_parser_name = tool_parser_name
        self.reasoning_parser_name = reasoning_parser_name

        # Token-in-Token-out 模式由 token_builder 是否存在决定
        self.token_in_token_out = token_builder is not None

    async def process_stream(
        self,
        messages: list,
        request_id: str,
        session_id: Optional[str] = None,
        context_holder: Optional[dict] = None,
        **request_params
    ) -> AsyncIterator[Dict[str, Any]]:
        """流式处理 LLM 请求

        Args:
            messages: OpenAI 格式的消息列表
            request_id: 请求 ID
            session_id: 会话 ID（格式: app_id,sample_id,task_id）
            context_holder: 可选容器，流式完成后存储上下文
            **request_params: 请求参数（如 max_tokens, temperature 等）

        Yields:
            OpenAI 格式的流式响应块
        """
        # 构建 unique_id
        unique_id = f"{session_id},{request_id}" if session_id else request_id

        # 直接转发模式：不经过 PromptBuilder 和 TokenBuilder
        if not self.token_in_token_out:
            async for chunk in self._process_stream_direct(
                messages=messages,
                request_id=request_id,
                session_id=session_id,
                unique_id=unique_id,
                context_holder=context_holder,
                **request_params
            ):
                yield chunk
            return

        # Token-in-Token-out 模式：使用完整的处理流程
        # 初始化上下文
        context = ProcessContext(
            request_id=request_id,
            model=self.model,
            messages=messages,
            request_params=request_params,
            session_id=session_id,
            unique_id=unique_id,
            is_stream=True
        )
        context.start_time = datetime.now()

        # 构建完整请求（阶段1: raw_request）
        context.raw_request = {
            "model": self.model,
            "messages": messages,
            **request_params
        }

        # 流式状态
        previous_text = ""
        previous_token_ids = []

        logger.info(f"[{unique_id}] 开始流式处理请求: model={self.model}, messages_count={len(messages)}")

        try:
            # 1. Message → PromptText 转换
            context.prompt_text = await self.prompt_builder.build_prompt_text(
                messages, context
            )
            logger.info(f"[{unique_id}] PromptText 转换完成: prompt_length={len(context.prompt_text)}")

            # 2. 构建文本推理请求（阶段2: text_request）
            context.text_request = {
                "prompt": context.prompt_text,
                "model": self.model,
                **request_params
            }

            # 3. Token 编码
            context.token_ids = await self.token_builder.encode_text(
                context.prompt_text, context
            )
            context.prompt_tokens = len(context.token_ids)
            prompt_input = context.token_ids
            logger.info(f"[{unique_id}] TokenIds 转换完成: prompt_tokens={context.prompt_tokens}, cache_hit_tokens={context.cache_hit_tokens}")

            # 4. 构建 Token 推理请求（阶段3: token_request）
            context.token_request = {
                "prompt": context.token_ids,
                "model": self.model,
                **request_params
            }

            # 5. 流式请求并处理响应
            # 使用上下文管理器自动管理流式状态
            with self.prompt_builder:
                async for infer_chunk in self.infer_client.send_completion_stream(
                    prompt=prompt_input,
                    model=self.model,
                    **request_params
                ):
                    # 处理单个响应块
                    chunk = await self._process_stream_chunk(
                        infer_chunk, context,
                        previous_text, previous_token_ids
                    )

                    if chunk:
                        # 更新状态
                        previous_text = context.stream_buffer_text
                        previous_token_ids = context.stream_buffer_ids.copy()

                        context.stream_chunk_count += 1
                        yield chunk

                        # 如果是最后一个 chunk，结束流
                        if context.stream_finished:
                            break

            # 6. 流式结束后更新上下文
            await self._finalize_stream(context, context_holder)

        except Exception as e:
            await self._handle_stream_error(context, e, context_holder)
            raise

    async def _process_stream_direct(
        self,
        messages: list,
        request_id: str,
        session_id: Optional[str],
        unique_id: str,
        context_holder: Optional[dict],
        **request_params
    ) -> AsyncIterator[Dict[str, Any]]:
        """直接转发模式处理流式请求

        不经过 PromptBuilder 和 TokenBuilder，直接将 OpenAI 格式请求
        转发到推理服务的 /v1/chat/completions 接口。

        数据流向：
        raw_request → 推理服务 → raw_response

        Args:
            messages: OpenAI 格式的消息列表
            request_id: 请求 ID
            session_id: 会话 ID
            unique_id: 唯一 ID
            context_holder: 上下文存储容器
            **request_params: 请求参数

        Yields:
            OpenAI 格式的流式响应块
        """
        # 初始化上下文
        context = ProcessContext(
            request_id=request_id,
            model=self.model,
            messages=messages,
            request_params=request_params,
            session_id=session_id,
            unique_id=unique_id,
            is_stream=True
        )
        context.start_time = datetime.now()

        # 构建完整请求（阶段1: raw_request）
        context.raw_request = {
            "model": self.model,
            "messages": messages,
            **request_params
        }

        logger.info(f"[{unique_id}] 开始流式处理请求（直接转发模式）: model={self.model}, messages_count={len(messages)}")

        try:
            # 直接转发到推理服务的 chat completions 接口
            async for chunk in self.infer_client.send_chat_completion_stream(
                messages=messages,
                model=self.model,
                **request_params
            ):
                # 累积流式响应中的所有字段
                if "choices" in chunk and chunk["choices"]:
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
                        if fc.get("name"):
                            context.stream_function_call["name"] = fc["name"]
                        if fc.get("arguments"):
                            context.stream_function_call["arguments"] += fc["arguments"]

                    # 6. 累积 logprobs
                    if "logprobs" in choice and choice["logprobs"]:
                        context.stream_logprobs = choice["logprobs"]

                    # 7. 累积 vLLM 扩展字段
                    if "stop_reason" in choice and choice["stop_reason"] is not None:
                        context.stream_stop_reason = choice["stop_reason"]
                    if "token_ids" in choice and choice["token_ids"]:
                        if context.stream_token_ids is None:
                            context.stream_token_ids = []
                        context.stream_token_ids.extend(choice["token_ids"])

                    # 8. 检查是否结束
                    finish_reason = choice.get("finish_reason")
                    if finish_reason:
                        context.stream_finished = True
                        context.stream_finish_reason = finish_reason
                        if "usage" in chunk:
                            context.prompt_tokens = chunk["usage"].get("prompt_tokens", 0)
                            context.completion_tokens = chunk["usage"].get("completion_tokens", 0)
                            context.total_tokens = chunk["usage"].get("total_tokens", 0)

                context.stream_chunk_count += 1
                yield chunk

            # 流式结束后更新上下文
            context.response_text = context.stream_buffer_text
            context.end_time = datetime.now()
            context.processing_duration_ms = (
                context.end_time - context.start_time
            ).total_seconds() * 1000

            # 如果后端服务未返回 usage 信息，估算 token 数量
            if context.completion_tokens == 0 and context.response_text:
                # 估算：假设平均每 4 个字符约 1 个 token
                context.completion_tokens = len(context.response_text) // 4
                context.total_tokens = (context.prompt_tokens or 0) + context.completion_tokens

            # 构建消息体
            message = {
                "role": context.stream_role or "assistant",
                "content": context.response_text or None
            }

            # 添加 reasoning（vLLM 扩展）
            if context.stream_reasoning:
                message["reasoning"] = context.stream_reasoning

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
                "finish_reason": context.stream_finish_reason or "stop"
            }

            # 添加 logprobs
            if context.stream_logprobs:
                choice["logprobs"] = context.stream_logprobs

            # 添加 vLLM 扩展字段
            if context.stream_stop_reason is not None:
                choice["stop_reason"] = context.stream_stop_reason
            if context.stream_token_ids:
                choice["token_ids"] = context.stream_token_ids

            # 构建最终响应（阶段1: raw_response）
            context.raw_response = {
                "id": f"chatcmpl-{request_id}",
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

            logger.info(f"[{unique_id}] 流式处理完成（直接转发模式）: chunks={context.stream_chunk_count}, duration_ms={context.processing_duration_ms:.2f}")

            # 将上下文写入容器
            if context_holder is not None:
                context_holder['context'] = context

        except Exception as e:
            context.error = str(e)
            context.error_traceback = traceback.format_exc()
            context.end_time = datetime.now()
            logger.error(f"[{unique_id}] 流式处理异常: {str(e)}\n{traceback.format_exc()}")

            if context_holder is not None:
                context_holder['context'] = context
            raise

    async def _process_stream_chunk(
        self,
        infer_chunk: Dict[str, Any],
        context: ProcessContext,
        previous_text: str,
        previous_token_ids: list
    ) -> Optional[Dict[str, Any]]:
        """处理单个流式响应块

        Args:
            infer_chunk: Infer 响应块
            context: 处理上下文
            previous_text: 之前的累积文本
            previous_token_ids: 之前的 token IDs

        Returns:
            OpenAI 格式的 chunk 字典，或 None
        """
        # 解析 Infer 响应块
        chunk_content, chunk_token_ids, tool_calls_delta = \
            InferResponseParser.parse_stream_chunk(
                infer_chunk, self.token_in_token_out
            )

        # Token-in-Token-out 模式：增量解码 token IDs
        if self.token_in_token_out and chunk_token_ids:
            chunk_content = self.token_builder.decode_tokens_streaming(
                chunk_token_ids, context
            )

        # 累积响应
        current_text = context.stream_buffer_text
        if chunk_content:
            context.stream_buffer_text += chunk_content
            current_text = context.stream_buffer_text

        current_token_ids = context.stream_buffer_ids.copy()
        if chunk_token_ids:
            context.stream_buffer_ids.extend(chunk_token_ids)
            current_token_ids = context.stream_buffer_ids

        delta_token_ids = chunk_token_ids or []

        # 使用 Parser 进行流式解析
        parsed_content, parsed_tool_calls, reasoning_delta = \
            self.prompt_builder.process_streaming_parse(
                delta_text=chunk_content or "",
                context=context,
                previous_text=previous_text,
                current_text=current_text,
                previous_token_ids=previous_token_ids,
                current_token_ids=current_token_ids,
                delta_token_ids=delta_token_ids
            )

        # 使用解析结果覆盖原始内容
        effective_content = parsed_content
        effective_tool_calls = parsed_tool_calls or tool_calls_delta

        # 检查是否结束
        finish_reason = None
        if InferResponseParser.is_stream_finished(infer_chunk):
            # 尝试从 Parser 获取结束原因
            parser_finish = self.prompt_builder.get_finish_reason_from_parser(context)
            finish_reason = parser_finish or InferResponseParser.get_finish_reason(infer_chunk)
            context.stream_finished = True
            # 保存 usage 信息（如果有）
            if "usage" in infer_chunk:
                context.token_response = {"usage": infer_chunk["usage"]}

        # 构建并发送 OpenAI 流式响应块
        openai_chunk = self.prompt_builder.build_stream_chunk(
            content=effective_content,
            context=context,
            finish_reason=finish_reason,
            tool_calls_delta=effective_tool_calls,
            reasoning_delta=reasoning_delta
        )

        return openai_chunk

    async def _finalize_stream(
        self,
        context: ProcessContext,
        context_holder: Optional[dict]
    ):
        """完成流式处理

        Args:
            context: 处理上下文
            context_holder: 上下文存储容器
        """
        # 更新上下文
        context.response_text = context.stream_buffer_text
        context.response_ids = context.stream_buffer_ids if self.token_in_token_out else None
        logger.debug(f"[{context.unique_id}] _finalize_stream: stream_buffer_text 长度: {len(context.stream_buffer_text)}, stream_buffer_ids 长度: {len(context.stream_buffer_ids)}")
        context.full_conversation_text = context.prompt_text + context.response_text

        if self.token_in_token_out and context.token_ids and context.response_ids:
            context.full_conversation_token_ids = context.token_ids + context.response_ids

        # 构建文本推理响应（阶段2: text_response）
        context.text_response = {
            "response_text": context.response_text,
            "response_ids": context.response_ids
        }

        # 统计信息
        if self.token_in_token_out:
            # Token 模式：优先使用 token_ids 长度，否则估算
            if context.stream_buffer_ids:
                context.completion_tokens = len(context.stream_buffer_ids)
                logger.debug(f"[{context.unique_id}] Token 模式：使用 stream_buffer_ids 长度: {context.completion_tokens}")
            elif context.response_text:
                # 使用字符数估算（粗略估计：4 字符约等于 1 token，至少 1 个 token）
                context.completion_tokens = max(1, len(context.response_text) // 4)
                logger.debug(f"[{context.unique_id}] Token 模式：估算 completion_tokens: {context.completion_tokens} (response_text 长度: {len(context.response_text)})")
            else:
                context.completion_tokens = 0
                logger.warning(f"[{context.unique_id}] Token 模式：无法估算 completion_tokens，stream_buffer_ids 和 response_text 都为空")
        else:
            # Text 模式：尝试从 infer 响应获取，或使用字符估算
            if context.token_response and "usage" in context.token_response:
                usage = context.token_response["usage"]
                context.completion_tokens = usage.get("completion_tokens", 0)
            else:
                # 使用字符数估算（粗略估计：4 字符约等于 1 token）
                context.completion_tokens = len(context.response_text) // 4 if context.response_text else 0
        context.total_tokens = (context.prompt_tokens or 0) + context.completion_tokens

        context.end_time = datetime.now()
        context.processing_duration_ms = (
            context.end_time - context.start_time
        ).total_seconds() * 1000

        # 构建最终响应（阶段1: raw_response）
        context.raw_response = self.prompt_builder.build_openai_response(
            context.response_text, context
        )

        logger.info(f"[{context.unique_id}] 流式处理完成: chunks={context.stream_chunk_count}, duration_ms={context.processing_duration_ms:.2f}")

        # 将上下文写入调用方提供的容器
        if context_holder is not None:
            context_holder['context'] = context

    async def _handle_stream_error(
        self,
        context: ProcessContext,
        error: Exception,
        context_holder: Optional[dict]
    ):
        """处理流式错误

        Args:
            context: 处理上下文
            error: 异常
            context_holder: 上下文存储容器
        """
        context.error = str(error)
        context.error_traceback = traceback.format_exc()
        context.end_time = datetime.now()

        logger.error(f"[{context.unique_id}] 流式处理异常: {str(error)}\n{traceback.format_exc()}")

        # 异常时也写入上下文，以便后台存储
        if context_holder is not None:
            context_holder['context'] = context
