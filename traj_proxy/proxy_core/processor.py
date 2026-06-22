"""
Processor - 统一请求处理器

根据配置选择不同的 Pipeline 处理请求。
提供统一的非流式和流式处理接口。
"""

from typing import Optional, Dict, Any, AsyncIterator
from datetime import datetime
import traceback
from pathlib import Path

from traj_proxy.proxy_core.context import ProcessContext
from traj_proxy.proxy_core.pipeline.base import BasePipeline
from traj_proxy.proxy_core.pipeline.direct_pipeline import DirectPipeline
from traj_proxy.proxy_core.pipeline.token_pipeline import TokenPipeline
from traj_proxy.proxy_core.converters.message_converter import MessageConverter
from traj_proxy.proxy_core.converters.token_converter import TokenConverter
from traj_proxy.proxy_core.builders.openai_builder import OpenAIResponseBuilder
from traj_proxy.proxy_core.builders.stream_builder import StreamChunkBuilder
from traj_proxy.proxy_core.cache.prefix_cache import PrefixMatchCache
from traj_proxy.proxy_core.filters import ContentSanitizer
from traj_proxy.proxy_core.parsers import ParserManager
from traj_proxy.store.request_repository import RequestRepository
from traj_proxy.exceptions import DatabaseError
from traj_proxy.utils.logger import get_logger
from traj_proxy.observability.event_bus import emit
from traj_proxy.observability.events import EVENT_REQUEST_STARTED, EVENT_REQUEST_COMPLETED
from traj_proxy.utils.config import get_max_concurrent_requests

logger = get_logger(__name__)


class Processor:
    """统一请求处理器

    根据配置选择不同的 Pipeline 处理请求：
    - 直接转发模式 (token_in_token_out=False): DirectPipeline
    - Token 模式 (token_in_token_out=True): TokenPipeline

    特性：
    - 前缀匹配缓存优化
    - 工具调用解析
    - 推理内容解析
    - 统一的非流式和流式处理接口
    """

    def __init__(
        self,
        model: str,
        tokenizer_path: Optional[str] = None,
        request_repository: RequestRepository = None,
        infer_client=None,
        config: Optional[Dict[str, Any]] = None,
        run_id: str = "",
        tool_parser: str = "",
        reasoning_parser: str = "",
        updated_at: Optional[datetime] = None,
        tokenizer=None
    ):
        """初始化 Processor

        Args:
            model: 模型名称
            tokenizer_path: Tokenizer 路径（token_in_token_out=True 时必需）
            request_repository: 请求记录仓库，用于存储轨迹记录
            infer_client: Infer 服务客户端
            config: 完整配置字典（由 worker 传入）
            run_id: 运行ID，空字符串表示全局模型
            tool_parser: Tool parser 名称
            reasoning_parser: Reasoning parser 名称
            updated_at: 模型注册/更新时间
            tokenizer: Tokenizer 实例（由 ProcessorManager 通过 TokenizerCache 提供）
        """
        self.model = model
        self.run_id = run_id
        self.tokenizer_path = tokenizer_path
        self.request_repository = request_repository
        self.infer_client = infer_client
        self.tool_parser_name = tool_parser
        self.updated_at = updated_at
        self.reasoning_parser_name = reasoning_parser
        self._tokenizer = tokenizer

        # 从传入的配置读取 token_in_token_out
        if config:
            self.token_in_token_out = config.get("token_in_token_out", False)
        else:
            self.token_in_token_out = False

        # 根据模式创建 Pipeline
        if self.token_in_token_out:
            self._pipeline = self._create_token_pipeline()
        else:
            self._pipeline = self._create_direct_pipeline()

    def _create_direct_pipeline(self) -> BasePipeline:
        """创建直接转发 Pipeline"""
        return DirectPipeline(
            model=self.model,
            infer_client=self.infer_client,
            request_repository=self.request_repository
        )

    def _create_token_pipeline(self) -> BasePipeline:
        """创建 Token 模式 Pipeline 及其依赖"""
        if self._tokenizer is None:
            raise ValueError("token_in_token_out=True 时，tokenizer 必须由 ProcessorManager 通过 TokenizerCache 提供")
        tokenizer = self._tokenizer

        # 推断 TITO 模板路径
        tito_template_path = self._get_tito_template_path()

        # 创建 Parser（对齐 vllm serving_chat.py 的 parser_cls 设计）
        # ParserManager.create_parser 返回 (parser_cls, parser_instance)：
        # - parser_cls: 动态子类（类引用），供流式路径 per-request 实例化
        # - parser_instance: 共享 Parser 实例，供非流式路径使用
        # 对齐 vllm serving_chat.py: self.parser_cls = ParserManager.get_parser(...)
        # 然后 self.parser_cls(tokenizer, request.tools, chat_template_kwargs=...)
        parser_cls, parser = ParserManager.create_parser(
            tool_parser_name=self.tool_parser_name,
            reasoning_parser_name=self.reasoning_parser_name,
            tokenizer=tokenizer
        )

        # 创建缓存策略
        cache_strategy = PrefixMatchCache(self.request_repository)

        # 创建内容净化器（TITO 模式专用）
        # 归一化 system message 中的动态变化字段（如 cch），保障前缀缓存命中率。
        # DirectPipeline 不注入此对象，因此直接转发模式完全不受影响。
        content_sanitizer = ContentSanitizer()

        # 创建转换器（传入 TITO 模板路径 + 净化器）
        message_converter = MessageConverter(
            tokenizer, tito_template_path, content_sanitizer=content_sanitizer
        )
        token_converter = TokenConverter(tokenizer, cache_strategy)

        # 创建响应构建器
        response_builder = OpenAIResponseBuilder(self.model, parser)
        stream_builder = StreamChunkBuilder(self.model, parser)

        return TokenPipeline(
            model=self.model,
            infer_client=self.infer_client,
            request_repository=self.request_repository,
            message_converter=message_converter,
            token_converter=token_converter,
            response_builder=response_builder,
            stream_builder=stream_builder,
            parser=parser,
            parser_cls=parser_cls,
            tokenizer_path=self.tokenizer_path,
        )

    def _get_tito_template_path(self) -> Optional[str]:
        """获取 TITO 模板路径

        自动推断 tokenizer 目录下的 chat_template_tito.jinja 文件。
        该模板确保多轮对话一致性，使前缀匹配缓存正常工作。

        Returns:
            TITO 模板路径，如果不存在则返回 None
        """
        if not self.tokenizer_path:
            return None

        tokenizer_dir = Path(self.tokenizer_path)
        tito_template = tokenizer_dir / "chat_template_tito.jinja"

        if tito_template.exists():
            logger.info(f"找到 TITO 模板: {tito_template}")
            return str(tito_template)

        logger.warning(
            f"TITO 模板不存在: {tito_template}，将使用默认模板。"
            f"前缀匹配缓存可能无法正常工作。"
        )
        return None

    @staticmethod
    def _warn_unsupported_params(context: "ProcessContext", request_params: Dict[str, Any]) -> None:
        """警告：logprobs/return_token_ids 由 proxy 强制覆盖，不回传给客户端

        无论客户端是否请求，proxy 都会向推理服务强制请求 logprobs=1 和
        return_token_ids=True 用于轨迹记录，但不会将这些数据返回给客户端，
        以确保直接转发模式与 TITO 模式的行为一致。
        """
        overridden = []
        if request_params.get("logprobs"):
            overridden.append("logprobs")
        if request_params.get("return_token_ids"):
            overridden.append("return_token_ids")

        if overridden:
            logger.warning(
                f"[{context.unique_id}] 客户端请求了以下字段: {', '.join(overridden)}。"
                "proxy 会强制向推理服务请求这些数据用于轨迹记录，但不会在客户端响应中返回。"
            )

    # ==================== 非流式处理接口 ====================

    async def process_request(
        self,
        messages: list,
        request_id: str,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        forward_headers: Optional[Dict[str, str]] = None,
        **request_params
    ) -> ProcessContext:
        """处理完整的非流式 LLM 请求

        Args:
            messages: OpenAI 格式的消息列表
            request_id: 请求 ID
            session_id: 原始会话 ID（不再自动补充前缀）
            run_id: 运行 ID（可选）
            forward_headers: 需要转发到推理服务的 header（可选）
            **request_params: 请求参数（如 max_tokens, temperature 等）

        Returns:
            处理上下文，包含完整的处理结果和响应

        Raises:
            各种异常，包括 DatabaseError、InferServiceError 等
        """
        # 创建上下文
        context = self._pipeline._create_context(
            request_id=request_id,
            session_id=session_id,
            run_id=run_id,
            messages=messages,
            request_params=request_params,
            is_stream=False,
            forward_headers=forward_headers or {}
        )

        logger.info(
            f"[{context.unique_id}] 开始处理请求: "
            f"model={self.model}, messages_count={len(messages)}, "
            f"token_mode={self.token_in_token_out}"
        )

        self._warn_unsupported_params(context, request_params)

        context.base_url = getattr(self.infer_client, 'base_url', 'unknown') or 'unknown'
        emit(EVENT_REQUEST_STARTED, model=self.model, is_stream=False,
             max_concurrent=get_max_concurrent_requests())
        exception = None
        try:
            # 使用 Pipeline 处理
            context = await self._pipeline.process(messages, context)
            return context

        except Exception as e:
            exception = e
            logger.error(
                f"[{context.unique_id}] 处理请求时发生异常: {str(e)}\n"
                f"{traceback.format_exc()}"
            )
            raise
        finally:
            emit(EVENT_REQUEST_COMPLETED, context=context, exception=exception)

    # ==================== 流式处理接口 ====================

    async def process_stream(
        self,
        messages: list,
        request_id: str,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        context_holder: Optional[dict] = None,
        forward_headers: Optional[Dict[str, str]] = None,
        **request_params
    ) -> AsyncIterator[Dict[str, Any]]:
        """处理完整的流式 LLM 请求

        Args:
            messages: OpenAI 格式的消息列表
            request_id: 请求 ID
            session_id: 原始会话 ID（不再自动补充前缀）
            run_id: 运行 ID（可选）
            context_holder: 可选容器，流式完成后存储上下文
            forward_headers: 需要转发到推理服务的 header（可选）
            **request_params: 请求参数

        Yields:
            OpenAI 格式的流式响应块

        Raises:
            各种异常
        """
        # 创建上下文
        context = self._pipeline._create_context(
            request_id=request_id,
            session_id=session_id,
            run_id=run_id,
            messages=messages,
            request_params=request_params,
            is_stream=True,
            forward_headers=forward_headers or {}
        )

        logger.info(
            f"[{context.unique_id}] 开始流式处理请求: "
            f"model={self.model}, messages_count={len(messages)}, "
            f"token_mode={self.token_in_token_out}"
        )

        self._warn_unsupported_params(context, request_params)

        context.base_url = getattr(self.infer_client, 'base_url', 'unknown') or 'unknown'
        emit(EVENT_REQUEST_STARTED, model=self.model, is_stream=True,
             max_concurrent=get_max_concurrent_requests())
        exception = None
        try:
            # 使用 Pipeline 处理流式请求
            async for chunk in self._pipeline.process_stream(messages, context):
                yield chunk

        except Exception as e:
            exception = e
            logger.error(
                f"[{context.unique_id}] 流式处理异常: {str(e)}\n"
                f"{traceback.format_exc()}"
            )
            raise
        finally:
            # 将上下文写入容器
            if context_holder is not None:
                context_holder['context'] = context
            emit(EVENT_REQUEST_COMPLETED, context=context, exception=exception)
