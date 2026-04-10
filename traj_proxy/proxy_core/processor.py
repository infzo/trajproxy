"""
Processor - 统一请求处理器

根据配置选择不同的 Pipeline 处理请求。
提供统一的非流式和流式处理接口。
"""

from typing import Optional, Dict, Any, AsyncIterator
from datetime import datetime
import traceback
from pathlib import Path

from transformers import AutoTokenizer

from traj_proxy.proxy_core.context import ProcessContext
from traj_proxy.proxy_core.pipeline.base import BasePipeline
from traj_proxy.proxy_core.pipeline.direct_pipeline import DirectPipeline
from traj_proxy.proxy_core.pipeline.token_pipeline import TokenPipeline
from traj_proxy.proxy_core.converters.message_converter import MessageConverter
from traj_proxy.proxy_core.converters.token_converter import TokenConverter
from traj_proxy.proxy_core.builders.openai_builder import OpenAIResponseBuilder
from traj_proxy.proxy_core.builders.stream_builder import StreamChunkBuilder
from traj_proxy.proxy_core.cache.prefix_cache import PrefixMatchCache
from traj_proxy.proxy_core.parsers import ParserManager
from traj_proxy.store.request_repository import RequestRepository
from traj_proxy.exceptions import DatabaseError
from traj_proxy.utils.logger import get_logger

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
        reasoning_parser: str = ""
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
        """
        self.model = model
        self.run_id = run_id
        self.tokenizer_path = tokenizer_path
        self.request_repository = request_repository
        self.infer_client = infer_client
        self.tool_parser_name = tool_parser
        self.reasoning_parser_name = reasoning_parser

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
        # 加载 tokenizer
        if not self.tokenizer_path:
            raise ValueError("token_in_token_out=True 时，tokenizer_path 必须提供")
        tokenizer = AutoTokenizer.from_pretrained(self.tokenizer_path)

        # 推断 TITO 模板路径
        tito_template_path = self._get_tito_template_path()

        # 创建 Parser
        parser = ParserManager.create_parser(
            tool_parser_name=self.tool_parser_name,
            reasoning_parser_name=self.reasoning_parser_name,
            tokenizer=tokenizer
        )

        # 创建缓存策略
        cache_strategy = PrefixMatchCache(self.request_repository)

        # 创建转换器（传入 TITO 模板路径）
        message_converter = MessageConverter(tokenizer, tito_template_path)
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
            tokenizer_path=self.tokenizer_path
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

    # ==================== 非流式处理接口 ====================

    async def process_request(
        self,
        messages: list,
        request_id: str,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        **request_params
    ) -> ProcessContext:
        """处理完整的非流式 LLM 请求

        Args:
            messages: OpenAI 格式的消息列表
            request_id: 请求 ID
            session_id: 原始会话 ID（不再自动补充前缀）
            run_id: 运行 ID（可选）
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
            is_stream=False
        )

        logger.info(
            f"[{context.unique_id}] 开始处理请求: "
            f"model={self.model}, messages_count={len(messages)}, "
            f"token_mode={self.token_in_token_out}"
        )

        try:
            # 使用 Pipeline 处理
            context = await self._pipeline.process(messages, context)
            return context

        except Exception as e:
            logger.error(
                f"[{context.unique_id}] 处理请求时发生异常: {str(e)}\n"
                f"{traceback.format_exc()}"
            )
            raise

    # ==================== 流式处理接口 ====================

    async def process_stream(
        self,
        messages: list,
        request_id: str,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        context_holder: Optional[dict] = None,
        **request_params
    ) -> AsyncIterator[Dict[str, Any]]:
        """处理完整的流式 LLM 请求

        Args:
            messages: OpenAI 格式的消息列表
            request_id: 请求 ID
            session_id: 原始会话 ID（不再自动补充前缀）
            run_id: 运行 ID（可选）
            context_holder: 可选容器，流式完成后存储上下文
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
            is_stream=True
        )

        logger.info(
            f"[{context.unique_id}] 开始流式处理请求: "
            f"model={self.model}, messages_count={len(messages)}, "
            f"token_mode={self.token_in_token_out}"
        )

        try:
            # 使用 Pipeline 处理流式请求
            async for chunk in self._pipeline.process_stream(messages, context):
                yield chunk

        except Exception as e:
            logger.error(
                f"[{context.unique_id}] 流式处理异常: {str(e)}\n"
                f"{traceback.format_exc()}"
            )
            raise
        finally:
            # 将上下文写入容器
            if context_holder is not None:
                context_holder['context'] = context
