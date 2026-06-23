"""
TokenPipeline - Token 模式管道

经过完整的 Message → Text → Token → Infer → Token → Text → Response 处理流程。
支持前缀匹配缓存优化。
"""

import asyncio
import time
from datetime import datetime, timezone
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

# 已知顶级字段（来自 /v1/completions 响应，choices 和 usage 单独处理）
TOKEN_KNOWN_TOP_KEYS: frozenset[str] = frozenset({
    "id",
    "object",
    "created",
    "model",
    "choices",
    "usage",
})

# 已知 choice 级字段（来自 /v1/completions response choices 列表）
TOKEN_KNOWN_CHOICE_KEYS: frozenset[str] = frozenset({
    "text",
    "token_ids",
    "output_token_ids",
    "index",
    "finish_reason",
    "logprobs",
    "tool_calls",
})


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
        parser_cls: type = None,
        tokenizer_path: str = "",
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
            parser: 共享 Parser 实例（非流式路径使用）
            parser_cls: Parser 子类（类引用，流式路径 per-request 实例化使用）
            tokenizer_path: Tokenizer 路径
        """
        super().__init__(model, infer_client, request_repository)
        self.message_converter = message_converter
        self.token_converter = token_converter
        self.response_builder = response_builder
        self.stream_builder = stream_builder
        self.parser = parser
        self.parser_cls = parser_cls
        self.tokenizer_path = tokenizer_path

    def _create_context(
        self,
        *args,
        **kwargs,
    ) -> "ProcessContext":
        """创建处理上下文，标记管道模式为 tito"""
        ctx = super()._create_context(*args, **kwargs)
        ctx.pipeline_mode = "tito"
        return ctx

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

            # 更新统计信息（必须在 _build_response 之前，因为 builder 需要 usage 信息）
            self._update_stats(context)

            # 对齐 vLLM：非流式路径也创建 per-request Parser 实例
            # vLLM serving_chat.py 的非流式路径（chat_completion_full_generator）：
            #   - reasoning_parser: 每次请求新建实例（serving.py:253-258）
            #     reasoning_parser = self.reasoning_parser_cls(
            #         tokenizer, chat_template_kwargs=chat_template_kwargs)
            #   - tool_parser: 在 _parse_tool_calls_from_content 中每次请求新建实例（serving.py:544-545）
            #     tool_parser = tool_parser_cls(tokenizer, request.tools)
            # TrajProxy 采用相同策略：使用 parser_cls 创建 per-request 实例，
            # 确保并发请求之间状态完全隔离。
            from traj_proxy.proxy_core.parsers.parser_manager import Parser
            req = Parser._build_request(context.raw_request) if isinstance(context.raw_request, dict) else context.raw_request
            chat_kwargs = {}
            if isinstance(context.raw_request, dict):
                chat_kwargs = context.raw_request.get("chat_template_kwargs", {}) or {}
            elif hasattr(context.raw_request, 'chat_template_kwargs') and context.raw_request.chat_template_kwargs:
                chat_kwargs = context.raw_request.chat_template_kwargs
            request_parser = self.parser_cls(
                tokenizer=self.token_converter.tokenizer,
                tools=req.tools if req.tools else None,
                **chat_kwargs,
            )

            # 阶段 5: 构建响应（使用 per-request Parser）
            context = self._build_response(context, parser=request_parser)

            self._update_timing(context)

            cache_db = f"{context.cache_db_query_ms:.2f}" if context.cache_db_query_ms else "N/A"
            cache_match = f"{context.cache_prefix_match_ms:.2f}" if context.cache_prefix_match_ms else "N/A"
            logger.info(
                f"[{context.unique_id}] 请求处理完成: "
                f"duration_ms={context.processing_duration_ms:.2f}, "
                f"transform={context.transform_duration_ms:.2f}ms, "
                f"encode={context.encode_duration_ms:.2f}ms "
                f"(cache_db={cache_db}ms, match={cache_match}ms), "
                f"inference={context.inference_duration_ms:.2f}ms, "
                f"decode={context.decode_duration_ms:.2f}ms"
            )

            # 存储到数据库
            await self._store_trajectory(context, self.tokenizer_path, run_id=context.run_id)

            return context

        except Exception as e:
            self._handle_error(context, e)
            try:
                await self._store_trajectory(context, self.tokenizer_path, run_id=context.run_id)
            except Exception as store_err:
                from traj_proxy.observability.event_bus import emit
                from traj_proxy.observability.events import EVENT_TRAJECTORY_STORE_ERROR
                emit(
                    EVENT_TRAJECTORY_STORE_ERROR,
                    model=context.model,
                    error_type=type(store_err).__name__,
                    error_message=str(store_err)[:200],
                    run_id=context.run_id or "",
                )
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

            # 对齐 vLLM：每个流式请求创建全新的 Parser 实例
            # vLLM serving_chat.py:467-478 为每个请求新建 Parser，
            # 传入 request.tools 和 chat_template_kwargs：
            #   self.parser_cls(tokenizer, request.tools, chat_template_kwargs=...)
            # TrajProxy 采用相同策略：使用 parser_cls（动态子类）创建
            # per-request 实例，确保并发流式请求之间状态完全隔离。
            # Parser.__init__ 从类属性 reasoning_parser_cls / tool_parser_cls
            # 自动实例化子 parser，与 vLLM _WrappedParser.__init__ 一致。
            # 提取 chat_template_kwargs
            from traj_proxy.proxy_core.parsers.parser_manager import Parser
            req = Parser._build_request(context.raw_request) if isinstance(context.raw_request, dict) else context.raw_request
            chat_kwargs = {}
            if isinstance(context.raw_request, dict):
                chat_kwargs = context.raw_request.get("chat_template_kwargs", {}) or {}
            elif hasattr(context.raw_request, 'chat_template_kwargs') and context.raw_request.chat_template_kwargs:
                chat_kwargs = context.raw_request.chat_template_kwargs
            request_parser = self.parser_cls(
                tokenizer=self.token_converter.tokenizer,
                tools=req.tools if req.tools else None,
                **chat_kwargs,
            )

            # per-request Parser 实例在创建时已传入请求的 tools 和
            # chat_template_kwargs，与 vLLM _WrappedParser.__init__ 一致，
            # 无需额外调用 update_for_request 或 reset_streaming_state
            # （update_for_request 已移除，vLLM 中不存在此方法）

            # 流式状态
            first_chunk_received = False
            infer_start_time = time.perf_counter()

            # 重置推理阶段状态（三阶段状态机）
            # 对齐 vLLM DelegatingParser.parse_delta() 的 prompt reasoning check：
            # 如果没有 reasoning parser，或 prompt token_ids 中已包含 reasoning
            # 结束标记，则 reasoning_ended 立即为 True，使 tool parser 可以工作。
            if not request_parser.has_reasoning_parser:
                context.reasoning_ended = True
            elif context.token_ids and request_parser.is_reasoning_end(context.token_ids):
                context.reasoning_ended = True
            else:
                context.reasoning_ended = False

            # per-request Parser 实例自带 _stream_state（在 Parser.__init__ 中创建），
            # 对齐 vLLM：parse_delta() 直接使用 self._stream_state，
            # 无需额外创建 StreamState 对象或传入 state 参数

            # 阶段 3-5: 流式推理和响应
            async for infer_chunk in self.infer_client.send_completion_stream(
                prompt=prompt_input,
                model=self.model,
                extra_headers=context.forward_headers,
                request_id=context.unique_id,
                **context.request_params
            ):
                # 处理单个响应块（状态管理在 per-request parser 内部）
                chunk = await self._process_stream_chunk(
                    infer_chunk,
                    context,
                    request_parser=request_parser,
                )

                if chunk:
                    # 记录TTFT（首Token时间）
                    if not first_chunk_received:
                        context.ttft_ms = (time.perf_counter() - infer_start_time) * 1000
                        first_chunk_received = True

                    context.stream_chunk_count += 1
                    yield chunk

                # 如果是最后一个 chunk，结束流
                if context.stream_finished:
                    break

            # 记录推理总耗时（从开始推理到流结束）
            context.inference_duration_ms = (time.perf_counter() - infer_start_time) * 1000

            # 流式结束后完成处理
            await self._finalize_stream(context)

            # 追加 yield 一个独立的 usage chunk（对齐 vLLM stream_options.include_usage 行为）
            # 确保下游（如 litellm）能获取到正确的 completion_tokens，
            # 尤其是纯推理输出（无 content delta）场景下下游无法从 text 估算的情况。
            if context.completion_tokens:
                usage_chunk = {
                    "id": f"chatcmpl-{context.request_id}",
                    "object": "chat.completion.chunk",
                    "created": int(context.start_time.timestamp()) if context.start_time else int(time.time()),
                    "model": self.model,
                    "choices": [],
                    "usage": {
                        "prompt_tokens": context.prompt_tokens or 0,
                        "completion_tokens": context.completion_tokens,
                        "total_tokens": context.total_tokens or 0,
                    },
                }
                yield usage_chunk

        except asyncio.CancelledError:
            # 客户端断连或任务取消：确保轨迹数据被存储
            logger.warning(
                f"[{context.unique_id}] 流式处理被取消（客户端可能已断连）"
            )
            try:
                await self._finalize_stream(context)
            except Exception as finalize_err:
                logger.error(
                    f"[{context.unique_id}] CancelledError 场景下轨迹存储失败: {finalize_err}"
                )
            from traj_proxy.observability.event_bus import emit
            from traj_proxy.observability.events import EVENT_STREAM_CLIENT_DISCONNECT
            emit(EVENT_STREAM_CLIENT_DISCONNECT, model=context.model,
                  chunk_count=context.stream_chunk_count,
                  duration_ms=(datetime.now(timezone.utc) - context.start_time).total_seconds() * 1000
                  if context.start_time else 0)
            raise
        except Exception as e:
            self._handle_error(context, e)
            from traj_proxy.observability.event_bus import emit
            from traj_proxy.observability.events import EVENT_STREAM_CLIENT_DISCONNECT
            emit(EVENT_STREAM_CLIENT_DISCONNECT, model=context.model,
                  chunk_count=context.stream_chunk_count,
                  duration_ms=(datetime.now(timezone.utc) - context.start_time).total_seconds() * 1000
                  if context.start_time else 0)
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

    def _build_response(
        self,
        context: ProcessContext,
        parser: "Parser" = None,
    ) -> ProcessContext:
        """阶段 5: 构建响应

        Args:
            context: 处理上下文
            parser: per-request Parser 实例（非流式路径使用，
                流式路径不需要此参数）
        """
        # 1. 构建用户可见响应（skip_special_tokens=True，不含 EOS 等特殊 token）
        if context.response_ids:
            clean_response_text = self.token_converter.tokenizer.decode(
                context.response_ids, skip_special_tokens=True
            )
        else:
            clean_response_text = context.response_text or ""
        # 传入 per-request Parser，确保 builder 使用请求专属的 parser 实例
        context.raw_response = self.response_builder.build(
            clean_response_text, context, parser=parser
        )

        # 2. re-decode response，保留 EOS，与 response_ids 一致（用于存储和前缀缓存）
        if context.response_ids:
            context.response_text = self.token_converter.tokenizer.decode(
                context.response_ids, skip_special_tokens=False
            )

        # 3. full_conversation 自然一致（text 和 token_ids 都含 EOS）
        context.full_conversation_text = context.prompt_text + context.response_text
        if context.token_ids and context.response_ids:
            context.full_conversation_token_ids = context.token_ids + context.response_ids
        else:
            context.full_conversation_token_ids = context.token_ids

        logger.debug(
            f"[{context.unique_id}] 完整对话构建完成: "
            f"total_tokens={len(context.full_conversation_token_ids) if context.full_conversation_token_ids else len(context.full_conversation_text)}"
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
        request_parser: "Parser" = None,
    ) -> Optional[Dict[str, Any]]:
        """处理单个流式响应块

        对齐 vLLM DelegatingParser.parse_delta() 设计：
        状态管理完全在 parser 内部（self._stream_state），
        pipeline 层只负责解码和转发，不传入外部 state。

        Args:
            infer_chunk: Infer 响应块
            context: 处理上下文
            request_parser: per-request 的 Parser 实例（流式请求使用，
                非流式路径不需要此参数）

        Returns:
            OpenAI 格式的 chunk 字典，或 None
        """
        # ========== 捕获自定义字段（先于业务处理） ==========
        # 1. 捕获顶级自定义字段（last wins，null 值跳过）
        if context.stream_infer_metadata is None:
            context.stream_infer_metadata = {}
        for key, value in infer_chunk.items():
            if key not in TOKEN_KNOWN_TOP_KEYS and value is not None:
                context.stream_infer_metadata[key] = value

        # 2. 捕获 usage（每 chunk 检查，不局限于 finish chunk，last wins）
        if "usage" in infer_chunk and infer_chunk["usage"]:
            if context.token_response is None:
                context.token_response = {}
            context.token_response["usage"] = infer_chunk["usage"]

        # 3. 捕获 choice 级自定义字段（last wins，null 值跳过）
        if "choices" in infer_chunk and infer_chunk["choices"]:
            infer_choice = infer_chunk["choices"][0]
            choice_extras = {
                k: v for k, v in infer_choice.items()
                if k not in TOKEN_KNOWN_CHOICE_KEYS and v is not None
            }
            if choice_extras:
                if context.stream_infer_choice_extras is None:
                    context.stream_infer_choice_extras = {}
                context.stream_infer_choice_extras.update(choice_extras)

        # 解析 Infer 响应块
        chunk_content, chunk_token_ids, tool_calls_delta = \
            InferResponseParser.parse_stream_chunk(infer_chunk, is_token_mode=True)

        # Token-in-Token-out 模式：增量解码 token IDs
        if chunk_token_ids:
            chunk_content = self.token_converter.decode_streaming(
                chunk_token_ids, context
            )

        delta_token_ids = chunk_token_ids or []

        # 使用 per-request Parser.parse_delta() 进行流式解析
        # 对齐 vLLM DelegatingParser.parse_delta() 的推理→工具→内容三段式
        # 流式请求使用 request_parser（per-request 实例），避免并发竞态；
        # 若未提供 request_parser（非流式路径），回退到共享 Parser
        parser = request_parser or self.parser
        delta_msg = parser.parse_delta(
            delta_text=chunk_content or "",
            delta_token_ids=delta_token_ids,
            request=context.raw_request,
            prompt_token_ids=context.token_ids if context.stream_chunk_count == 0 else None,
        )

        if delta_msg:
            effective_content = delta_msg.content
            reasoning_delta = delta_msg.reasoning
            parsed_tool_calls = None
            if delta_msg.tool_calls:
                parsed_tool_calls = []
                for tc in delta_msg.tool_calls:
                    tc_dict = {"index": tc.index}
                    if tc.id is not None:
                        tc_dict["id"] = tc.id
                    if tc.type is not None:
                        tc_dict["type"] = tc.type
                    if tc.function:
                        if isinstance(tc.function, dict):
                            func_name = tc.function.get("name")
                            func_args = tc.function.get("arguments")
                        else:
                            func_name = tc.function.name
                            func_args = tc.function.arguments
                        func_dict = {}
                        if func_name is not None:
                            func_dict["name"] = func_name
                        if func_args is not None:
                            func_dict["arguments"] = func_args
                        if func_dict:
                            tc_dict["function"] = func_dict
                    parsed_tool_calls.append(tc_dict)
        else:
            effective_content = None
            reasoning_delta = None
            parsed_tool_calls = None

        effective_tool_calls = parsed_tool_calls or tool_calls_delta
        # 同步 per-request parser 内部状态到 context（供 pipeline 层判断）
        # 对齐 vLLM：parse_delta 使用 parser 自带的 _stream_state
        context.reasoning_ended = parser._stream_state.reasoning_ended

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
            # 尝试从 per-request Parser 获取结束原因
            parser_finish = self._get_finish_reason_from_parser(context, parser)
            finish_reason = parser_finish or InferResponseParser.get_finish_reason(infer_chunk)
            context.stream_finished = True

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

    def _get_finish_reason_from_parser(
        self,
        context: ProcessContext,
        parser: "Parser" = None,
    ) -> Optional[str]:
        """从 Parser 获取结束原因

        Args:
            context: 处理上下文
            parser: Parser 实例（流式请求使用 per-request 实例，
                非流式路径使用共享 Parser）
        """
        parser = parser or self.parser
        if parser.has_tool_parser:
            # 检查是否有工具调用
            tool_parser = parser.tool_parser
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

        # 统计信息（在 re-decode 前计算，使用原始 token 数量）
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
        if context.token_response is None:
            context.token_response = {}

        # 保留 infer 服务返回的 usage（如有），否则自行计算
        if "usage" not in context.token_response:
            context.token_response["usage"] = {
                "prompt_tokens": context.prompt_tokens,
                "completion_tokens": context.completion_tokens,
                "total_tokens": context.total_tokens,
            }

        # 1. 构建用户可见响应（skip_special_tokens=True，不含 EOS 等特殊 token）
        if context.response_ids:
            clean_user_text = self.token_converter.tokenizer.decode(
                context.response_ids, skip_special_tokens=True
            )
        else:
            clean_user_text = context.response_text or ""
        if "choices" not in context.token_response:
            choice = {
                "text": clean_user_text,
                "index": 0,
                "finish_reason": "stop",
            }
            if context.response_ids is not None:
                choice["token_ids"] = context.response_ids
            if context.stream_logprobs:
                choice["logprobs"] = context.stream_logprobs
            if context.token_ids:
                choice["prompt_token_ids"] = context.token_ids
            context.token_response["choices"] = [choice]

        # 合并顶级自定义字段（last wins 作为 base，setdefault 确保不覆盖 pipeline 生成的字段）
        if context.stream_infer_metadata:
            for k, v in context.stream_infer_metadata.items():
                context.token_response.setdefault(k, v)

        # 补全顶级元数据字段，与非流式对齐
        if "id" not in context.token_response:
            context.token_response["id"] = f"cmpl-{context.unique_id}"
        if "object" not in context.token_response:
            context.token_response["object"] = "text_completion"
        if "created" not in context.token_response:
            context.token_response["created"] = int(time.time())
        if "model" not in context.token_response:
            context.token_response["model"] = self.model

        # 合并 choice 级自定义字段（setdefault 确保不覆盖 pipeline 生成的字段）
        if context.stream_infer_choice_extras and "choices" in context.token_response:
            for k, v in context.stream_infer_choice_extras.items():
                context.token_response["choices"][0].setdefault(k, v)

        # 构建用户响应（不含 EOS）
        context.raw_response = self.response_builder.build(clean_user_text, context)

        # 补全顶级默认字段
        for key in ("kv_transfer_params", "prompt_logprobs",
                    "prompt_token_ids", "service_tier", "system_fingerprint"):
            context.raw_response.setdefault(key, None)

        # 2. re-decode response，保留 EOS，与 response_ids 一致（用于存储和前缀缓存）
        if context.response_ids:
            context.response_text = self.token_converter.tokenizer.decode(
                context.response_ids, skip_special_tokens=False
            )

        # 3. full_conversation 自然一致（text 和 token_ids 都含 EOS）
        context.full_conversation_text = context.prompt_text + context.response_text
        if context.token_ids and context.response_ids:
            context.full_conversation_token_ids = context.token_ids + context.response_ids

        # 更新 text_response（使用 re-decode 后的 response_text，含 EOS，与 response_ids 一致）
        context.text_response = {
            "response_text": context.response_text,
            "response_ids": context.response_ids
        }

        ttft_str = f"{context.ttft_ms:.2f}" if context.ttft_ms else "N/A"
        inference_str = f"{context.inference_duration_ms:.2f}" if context.inference_duration_ms else "N/A"
        transform_str = f"{context.transform_duration_ms:.2f}" if context.transform_duration_ms else "N/A"
        encode_str = f"{context.encode_duration_ms:.2f}" if context.encode_duration_ms else "N/A"
        cache_db = f"{context.cache_db_query_ms:.2f}" if context.cache_db_query_ms else "N/A"
        cache_match = f"{context.cache_prefix_match_ms:.2f}" if context.cache_prefix_match_ms else "N/A"
        logger.info(
            f"[{context.unique_id}] 流式处理完成: "
            f"chunks={context.stream_chunk_count}, "
            f"duration_ms={context.processing_duration_ms:.2f}, "
            f"transform_ms={transform_str}, encode_ms={encode_str} "
            f"(cache_db={cache_db}ms, match={cache_match}ms), "
            f"ttft_ms={ttft_str}, inference_ms={inference_str}"
        )

        # 存储到数据库
        await self._store_trajectory(context, self.tokenizer_path, run_id=context.run_id)
