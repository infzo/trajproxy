"""
TokenPipeline - Token 模式管道

经过完整的 Message → Text → Token → Infer → Token → Text → Response 处理流程。
支持前缀匹配缓存优化。
"""

import time
from typing import AsyncIterator, Dict, Any, Optional, TYPE_CHECKING

from traj_proxy.proxy_core.pipeline.base import BasePipeline
from traj_proxy.proxy_core.context import ProcessContext
from traj_proxy.proxy_core.infer_response_parser import InferResponseParser
from traj_proxy.utils.logger import get_logger

if TYPE_CHECKING:
    from traj_proxy.proxy_core.infer_client import InferClient
    from traj_proxy.store.request_repository import RequestRepository
    from traj_proxy.proxy_core.converters.message_converter import MessageConverter
    from traj_proxy.proxy_core.converters.token_converter import TokenConverter
    from traj_proxy.proxy_core.builders.openai_builder import OpenAIResponseBuilder
    from traj_proxy.proxy_core.builders.stream_builder import StreamChunkBuilder
    from traj_proxy.proxy_core.parsers.parser_manager import Parser

logger = get_logger(__name__)


class TokenPipeline(BasePipeline):
    """Token 模式管道

    经过完整的处理流程：
    1. Message → PromptText (MessageConverter)
    2. PromptText → TokenIds (TokenConverter, 支持缓存)
    3. TokenIds → 推理服务 (InferClient)
    4. TokenIds → ResponseText (TokenConverter)
    5. ResponseText → OpenAI Response (ResponseBuilder)

    特性：
    - 前缀匹配缓存优化
    - 工具调用解析
    - 推理内容解析
    """

    def __init__(
        self,
        model: str,
        infer_client: "InferClient",
        request_repository: Optional["RequestRepository"] = None,
        message_converter: "MessageConverter" = None,
        token_converter: "TokenConverter" = None,
        response_builder: "OpenAIResponseBuilder" = None,
        stream_builder: "StreamChunkBuilder" = None,
        parser: "Parser" = None,
        tokenizer_path: str = ""
    ):
        """初始化 TokenPipeline

        Args:
            model: 模型名称
            infer_client: 推理服务客户端
            request_repository: 请求记录仓库
            message_converter: 消息转换器
            token_converter: Token 转换器
            response_builder: 响应构建器
            stream_builder: 流式响应构建器
            parser: Parser 实例
            tokenizer_path: Tokenizer 路径
        """
        super().__init__(model, infer_client, request_repository)
        self.message_converter = message_converter
        self.token_converter = token_converter
        self.response_builder = response_builder
        self.stream_builder = stream_builder
        self.parser = parser
        self.tokenizer_path = tokenizer_path

    async def process(
        self,
        messages: list,
        context: ProcessContext
    ) -> ProcessContext:
        """处理非流式请求

        执行完整的 Token 模式处理流程。

        Args:
            messages: OpenAI 格式的消息列表
            context: 处理上下文

        Returns:
            处理后的上下文
        """
        logger.info(
            f"[{context.unique_id}] 开始处理请求（Token 模式）: "
            f"model={self.model}, messages_count={len(messages)}"
        )

        try:
            # 阶段 1: Message → PromptText
            t0 = time.perf_counter()
            context = await self._transform_messages(messages, context)
            context.transform_duration_ms = (time.perf_counter() - t0) * 1000

            # 阶段 2: PromptText → TokenIds (带缓存)
            t0 = time.perf_counter()
            context = await self._encode_text(context)
            context.encode_duration_ms = (time.perf_counter() - t0) * 1000

            # 阶段 3: 推理
            t0 = time.perf_counter()
            context = await self._inference(context)
            context.inference_duration_ms = (time.perf_counter() - t0) * 1000

            # 阶段 4: TokenIds → ResponseText
            t0 = time.perf_counter()
            context = await self._decode_response(context)
            context.decode_duration_ms = (time.perf_counter() - t0) * 1000

            # 阶段 5: 构建响应
            context = self._build_response(context)

            # 更新统计信息
            self._update_stats(context)

            self._update_timing(context)
            logger.info(
                f"[{context.unique_id}] 请求处理完成: "
                f"duration_ms={context.processing_duration_ms:.2f}, "
                f"transform={context.transform_duration_ms:.2f}ms, "
                f"encode={context.encode_duration_ms:.2f}ms, "
                f"inference={context.inference_duration_ms:.2f}ms, "
                f"decode={context.decode_duration_ms:.2f}ms"
            )

            # 存储到数据库
            await self._store_trajectory(context, self.tokenizer_path, run_id=context.run_id)

            return context

        except Exception as e:
            self._handle_error(context, e)
            await self._store_trajectory(context, self.tokenizer_path, run_id=context.run_id)
            raise

    async def process_stream(
        self,
        messages: list,
        context: ProcessContext
    ) -> AsyncIterator[Dict[str, Any]]:
        """处理流式请求

        执行完整的 Token 模式流式处理流程。

        Args:
            messages: OpenAI 格式的消息列表
            context: 处理上下文

        Yields:
            OpenAI 格式的流式响应块
        """
        logger.info(
            f"[{context.unique_id}] 开始流式处理请求（Token 模式）: "
            f"model={self.model}, messages_count={len(messages)}"
        )

        try:
            # 阶段 1: Message → PromptText
            t0 = time.perf_counter()
            context = await self._transform_messages(messages, context)
            context.transform_duration_ms = (time.perf_counter() - t0) * 1000

            # 阶段 2: PromptText → TokenIds (带缓存)
            t0 = time.perf_counter()
            context = await self._encode_text(context)
            context.encode_duration_ms = (time.perf_counter() - t0) * 1000
            prompt_input = context.token_ids

            logger.info(
                f"[{context.unique_id}] TokenIds 转换完成: "
                f"prompt_tokens={context.prompt_tokens}, "
                f"cache_hit_tokens={context.cache_hit_tokens}, "
                f"encode_ms={context.encode_duration_ms:.2f}"
            )

            # 流式状态
            previous_text = ""
            previous_token_ids = []
            first_chunk_received = False
            infer_start_time = time.perf_counter()

            # 使用上下文管理器自动管理流式状态
            with self.parser:
                # 阶段 3-5: 流式推理和响应
                async for infer_chunk in self.infer_client.send_completion_stream(
                    prompt=prompt_input,
                    model=self.model,
                    extra_headers=context.forward_headers,
                    request_id=context.unique_id,
                    **context.request_params
                ):
                    # 处理单个响应块
                    chunk = await self._process_stream_chunk(
                        infer_chunk,
                        context,
                        previous_text,
                        previous_token_ids
                    )

                    if chunk:
                        # 记录TTFT（首Token时间）
                        if not first_chunk_received:
                            context.ttft_ms = (time.perf_counter() - infer_start_time) * 1000
                            first_chunk_received = True
                            logger.info(
                                f"[{context.unique_id}] TTFT: {context.ttft_ms:.2f}ms"
                            )

                        # 更新状态
                        previous_text = context.stream_buffer_text
                        previous_token_ids = context.stream_buffer_ids.copy()

                        context.stream_chunk_count += 1
                        yield chunk

                        # 如果是最后一个 chunk，结束流
                        if context.stream_finished:
                            break

            # 记录推理总耗时（从开始推理到流结束）
            context.inference_duration_ms = (time.perf_counter() - infer_start_time) * 1000

            # 流式结束后完成处理
            await self._finalize_stream(context)

        except Exception as e:
            self._handle_error(context, e)
            raise

    # ==================== 非流式处理阶段 ====================

    async def _transform_messages(
        self,
        messages: list,
        context: ProcessContext
    ) -> ProcessContext:
        """阶段 1: Message → PromptText"""
        context.prompt_text = await self.message_converter.convert(
            messages, context
        )
        logger.debug(
            f"[{context.unique_id}] PromptText 转换完成: "
            f"prompt_length={len(context.prompt_text)}"
        )

        # 构建文本推理请求
        context.text_request = {
            "prompt": context.prompt_text,
            "model": self.model,
            **context.request_params
        }

        return context

    async def _encode_text(self, context: ProcessContext) -> ProcessContext:
        """阶段 2: PromptText → TokenIds"""
        context.token_ids = await self.token_converter.encode(
            context.prompt_text, context
        )
        context.prompt_tokens = len(context.token_ids)

        logger.info(
            f"[{context.unique_id}] TokenIds 转换完成: "
            f"prompt_tokens={context.prompt_tokens}"
        )

        # 构建 Token 推理请求
        context.token_request = {
            "prompt": context.token_ids,
            "model": self.model,
            **context.request_params
        }

        return context

    async def _inference(self, context: ProcessContext) -> ProcessContext:
        """阶段 3: 推理"""
        logger.info(f"[{context.unique_id}] 发送 Infer 请求（Token 模式）")

        context.token_response = await self.infer_client.send_completion(
            prompt=context.token_ids,
            model=self.model,
            extra_headers=context.forward_headers,
            request_id=context.unique_id,
            **context.request_params
        )

        logger.info(f"[{context.unique_id}] Infer 请求完成")

        return context

    async def _decode_response(self, context: ProcessContext) -> ProcessContext:
        """阶段 4: TokenIds → ResponseText"""
        # 从 token_response 解码响应
        text, token_ids = InferResponseParser.parse_text_response(
            context.token_response
        )

        if token_ids:
            # 扩展格式：infer 服务直接返回 token_ids
            context.response_ids = token_ids
            context.response_text = await self.token_converter.decode(
                token_ids, context
            )
        elif text:
            # 标准格式：choices[0].text
            # 在 token-in-token-out 模式下，text 是 token ID 字符串，需要解析
            parsed_ids = InferResponseParser.parse_token_ids_from_text(text)
            if parsed_ids is not None:
                # 成功解析为 token IDs
                context.response_ids = parsed_ids
                context.response_text = await self.token_converter.decode(
                    parsed_ids, context
                )
            else:
                # 解析失败，说明返回的是普通文本
                context.response_text = text
                context.response_ids = None

        logger.info(
            f"[{context.unique_id}] ResponseText 转换完成: "
            f"response_length={len(context.response_text)}"
        )

        # 构建文本推理响应
        context.text_response = {
            "response_text": context.response_text,
            "response_ids": context.response_ids
        }

        return context

    def _build_response(self, context: ProcessContext) -> ProcessContext:
        """阶段 5: 构建响应"""
        # 构建完整对话
        context.full_conversation_text = context.prompt_text + context.response_text

        if context.token_ids and context.response_ids:
            context.full_conversation_token_ids = context.token_ids + context.response_ids
        else:
            context.full_conversation_token_ids = context.token_ids

        logger.debug(
            f"[{context.unique_id}] 完整对话构建完成: "
            f"total_tokens={len(context.full_conversation_token_ids) if context.full_conversation_token_ids else len(context.full_conversation_text)}"
        )

        # 构建最终响应
        context.raw_response = self.response_builder.build(
            context.response_text, context
        )

        logger.debug(
            f"[{context.unique_id}] OpenAI Response 构建完成: "
            f"has_response={context.raw_response is not None}"
        )

        return context

    def _update_stats(self, context: ProcessContext):
        """更新统计信息"""
        if context.token_response and "usage" in context.token_response:
            usage = context.token_response["usage"]
            context.completion_tokens = usage.get("completion_tokens", 0)
            context.total_tokens = usage.get("total_tokens", 0)
        else:
            # 从响应估算
            context.completion_tokens = len(context.response_ids or [])
            context.prompt_tokens = len(context.token_ids or [])
            context.total_tokens = context.prompt_tokens + context.completion_tokens

        logger.info(
            f"[{context.unique_id}] 统计信息: "
            f"completion_tokens={context.completion_tokens}, "
            f"total_tokens={context.total_tokens}"
        )

    # ==================== 流式处理阶段 ====================

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
            InferResponseParser.parse_stream_chunk(infer_chunk, is_token_mode=True)

        # 调试：打印 chunk 结构
        if "choices" in infer_chunk and infer_chunk["choices"]:
            choice = infer_chunk["choices"][0]
            logger.debug(
                f"[{context.unique_id}] stream_chunk keys: {list(infer_chunk.keys())}, "
                f"choice keys: {list(choice.keys())}, "
                f"token_ids: {chunk_token_ids}"
            )

        # Token-in-Token-out 模式：增量解码 token IDs
        if chunk_token_ids:
            chunk_content = self.token_converter.decode_streaming(
                chunk_token_ids, context
            )

        # 注意：decode_streaming() 已经更新了 stream_buffer_ids 和 stream_buffer_text
        # 这里不能重复追加，否则会导致数据翻倍、工具解析失败
        current_text = context.stream_buffer_text
        current_token_ids = context.stream_buffer_ids

        delta_token_ids = chunk_token_ids or []

        # 使用 Parser 进行流式解析
        parsed_content, parsed_tool_calls, reasoning_delta = \
            self._process_streaming_parse(
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

        # 累积 logprobs（每个 chunk 的 choice 中可能携带，需合并数组）
        if "choices" in infer_chunk and infer_chunk["choices"]:
            choice = infer_chunk["choices"][0]
            if "logprobs" in choice and choice["logprobs"]:
                chunk_lp = choice["logprobs"]
                if context.stream_logprobs is None:
                    # 首次：直接赋值（深拷贝避免引用问题）
                    context.stream_logprobs = {k: list(v) if isinstance(v, list) else v
                                               for k, v in chunk_lp.items()}
                else:
                    # 后续：追加数组字段
                    for key in ("tokens", "text_offset", "top_logprobs", "token_logprobs"):
                        if key in chunk_lp and isinstance(chunk_lp[key], list):
                            context.stream_logprobs.setdefault(key, []).extend(chunk_lp[key])

        # 检查是否结束
        finish_reason = None
        if InferResponseParser.is_stream_finished(infer_chunk):
            # 尝试从 Parser 获取结束原因
            parser_finish = self._get_finish_reason_from_parser(context)
            finish_reason = parser_finish or InferResponseParser.get_finish_reason(infer_chunk)
            context.stream_finished = True
            # 保存 usage 信息（如果有）
            if "usage" in infer_chunk:
                context.token_response = {"usage": infer_chunk["usage"]}

        # 跳过空 delta chunk（与 vLLM 行为对齐：parser 返回 None 时不发送）
        # 例外：第一个 chunk 需要包含 role，始终发送
        if (not effective_content
            and effective_tool_calls is None
            and reasoning_delta is None
            and finish_reason is None
            and context.stream_chunk_count > 0):
            return None

        # 构建并发送 OpenAI 流式响应块
        openai_chunk = self.stream_builder.build_chunk(
            content=effective_content,
            context=context,
            finish_reason=finish_reason,
            tool_calls_delta=effective_tool_calls,
            reasoning_delta=reasoning_delta
        )

        return openai_chunk

    def _process_streaming_parse(
        self,
        delta_text: str,
        context: ProcessContext,
        previous_text: str,
        current_text: str,
        previous_token_ids: list,
        current_token_ids: list,
        delta_token_ids: list
    ) -> tuple:
        """处理流式解析

        协调 Tool Parser 和 Reasoning Parser 的流式解析。

        Returns:
            (content_delta, tool_calls_delta, reasoning_delta)
        """
        import traceback

        # 按照 vLLM 的设计: 初始化为 None
        # 只有在明确需要输出时才设置值
        content_delta = None
        tool_calls_delta = None
        reasoning_delta = None

        tools = context.request_params.get("tools")
        include_reasoning = context.request_params.get("include_reasoning", True)

        # 1. 先处理推理解析（可能改变内容归属）
        if self.parser.has_reasoning_parser and include_reasoning:
            try:
                delta_msg = self.parser.extract_reasoning_streaming(
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
                logger.warning(
                    f"[{context.unique_id}] 推理流式解析失败: {e}\n"
                    f"{traceback.format_exc()}"
                )

        # 2. 处理工具调用解析（vLLM 风格）
        if self.parser.has_tool_parser and tools:
            try:
                delta_msg = self.parser.extract_tool_calls_streaming(
                    previous_text=previous_text,
                    current_text=current_text,
                    delta_text=delta_text,
                    previous_token_ids=previous_token_ids,
                    current_token_ids=current_token_ids,
                    delta_token_ids=delta_token_ids,
                    request=context.raw_request
                )
                if delta_msg:
                    # parser 返回了 delta_msg
                    if delta_msg.tool_calls:
                        tool_calls_delta = []
                        for tc in delta_msg.tool_calls:
                            tc_dict = {"index": tc.index}
                            if tc.id is not None:
                                tc_dict["id"] = tc.id
                            if tc.type is not None:
                                tc_dict["type"] = tc.type
                            if tc.function:
                                func_dict = {}
                                if tc.function.name is not None:
                                    func_dict["name"] = tc.function.name
                                if tc.function.arguments is not None:
                                    func_dict["arguments"] = tc.function.arguments
                                if func_dict:
                                    tc_dict["function"] = func_dict
                            tool_calls_delta.append(tc_dict)
                    # 使用 parser 返回的 content（可能是 None 或空字符串）
                    # 注意：这里会覆盖 reasoning_parser 设置的 content_delta
                    content_delta = delta_msg.content

                    logger.debug(
                        f"[{context.unique_id}] tool parse result: "
                        f"content={repr(content_delta)}, tool_calls={len(tool_calls_delta) if tool_calls_delta else 0}"
                    )
                else:
                    # parser 返回 None = "等待更多数据" 或 "不输出"
                    # 按照 vLLM 的设计：如果 parser 返回 None，不输出任何内容
                    # 这意味着 content_delta 保持 None（即使 reasoning_parser 之前设置了值）
                    content_delta = None

            except Exception as e:
                logger.warning(
                    f"[{context.unique_id}] 工具调用流式解析失败: {e}\n"
                    f"{traceback.format_exc()}"
                )
        elif content_delta is None and delta_text:
            # 没有 tool parser 或没有 tools，且 content_delta 还没有被 reasoning_parser 设置
            # 直接输出原始文本（vLLM 风格）
            content_delta = delta_text

        return content_delta, tool_calls_delta, reasoning_delta

    def _get_finish_reason_from_parser(self, context: ProcessContext) -> Optional[str]:
        """从 Parser 获取结束原因"""
        if self.parser.has_tool_parser:
            # 检查是否有工具调用
            tool_parser = self.parser.tool_parser
            if hasattr(tool_parser, 'prev_tool_call_arr'):
                if tool_parser.prev_tool_call_arr:
                    return "tool_calls"
        return None

    async def _finalize_stream(self, context: ProcessContext):
        """完成流式处理"""
        context.response_text = context.stream_buffer_text
        context.response_ids = context.stream_buffer_ids
        logger.debug(
            f"[{context.unique_id}] _finalize_stream: "
            f"stream_buffer_text 长度: {len(context.stream_buffer_text)}, "
            f"stream_buffer_ids 长度: {len(context.stream_buffer_ids)}"
        )

        context.full_conversation_text = context.prompt_text + context.response_text

        if context.token_ids and context.response_ids:
            context.full_conversation_token_ids = context.token_ids + context.response_ids

        # 构建文本推理响应
        context.text_response = {
            "response_text": context.response_text,
            "response_ids": context.response_ids
        }

        # 统计信息
        if context.stream_buffer_ids:
            context.completion_tokens = len(context.stream_buffer_ids)
            logger.debug(
                f"[{context.unique_id}] Token 模式："
                f"使用 stream_buffer_ids 长度: {context.completion_tokens}"
            )
        elif context.response_text:
            # 使用字符数估算
            context.completion_tokens = max(1, len(context.response_text) // 4)
            logger.debug(
                f"[{context.unique_id}] Token 模式：估算 completion_tokens: "
                f"{context.completion_tokens} (response_text 长度: {len(context.response_text)})"
            )
        else:
            context.completion_tokens = 0
            logger.warning(
                f"[{context.unique_id}] Token 模式：无法估算 completion_tokens"
            )

        context.total_tokens = (context.prompt_tokens or 0) + context.completion_tokens

        self._update_timing(context)

        # 统一构建 token_response，保持流式与非流式语义一致
        # 非流式 token_response 结构: {"id", "object", "created", "model",
        #   "choices": [{"index", "text", "token_ids", "logprobs", "prompt_token_ids", ...}],
        #   "usage": {...}}
        # 流式场景需要构建相同结构，确保下游代码和轨迹数据行为一致
        if context.token_response is None:
            context.token_response = {}

        # 保留 infer 服务返回的 usage（如有），否则自行计算
        if "usage" not in context.token_response:
            context.token_response["usage"] = {
                "prompt_tokens": context.prompt_tokens,
                "completion_tokens": context.completion_tokens,
                "total_tokens": context.total_tokens,
            }

        # 补全 choices 结构，与非流式格式对齐
        if "choices" not in context.token_response:
            choice = {
                "text": context.response_text or "",
                "index": 0,
                "finish_reason": "stop",
            }
            # token_response 用于数据库存储，总是包含 token_ids 和 logprobs
            if context.response_ids is not None:
                choice["token_ids"] = context.response_ids
            if context.stream_logprobs:
                choice["logprobs"] = context.stream_logprobs
            if context.token_ids:
                choice["prompt_token_ids"] = context.token_ids
            context.token_response["choices"] = [choice]

        # 补全顶级元数据字段，与非流式对齐
        if "id" not in context.token_response:
            context.token_response["id"] = f"cmpl-{context.unique_id}"
        if "object" not in context.token_response:
            context.token_response["object"] = "text_completion"
        if "created" not in context.token_response:
            context.token_response["created"] = int(time.time())
        if "model" not in context.token_response:
            context.token_response["model"] = self.model

        # 构建最终响应
        context.raw_response = self.response_builder.build(
            context.response_text, context
        )

        ttft_str = f"{context.ttft_ms:.2f}" if context.ttft_ms else "N/A"
        inference_str = f"{context.inference_duration_ms:.2f}" if context.inference_duration_ms else "N/A"
        logger.info(
            f"[{context.unique_id}] 流式处理完成: "
            f"chunks={context.stream_chunk_count}, "
            f"duration_ms={context.processing_duration_ms:.2f}, "
            f"ttft_ms={ttft_str}, inference_ms={inference_str}"
        )

        # 存储到数据库
        await self._store_trajectory(context, self.tokenizer_path, run_id=context.run_id)
