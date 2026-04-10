"""
BasePipeline - 处理管道抽象基类

定义所有处理管道的通用接口和共享逻辑。
"""

from abc import ABC, abstractmethod
from typing import AsyncIterator, Dict, Any, Optional, TYPE_CHECKING
from datetime import datetime
import traceback

from traj_proxy.proxy_core.context import ProcessContext
from traj_proxy.exceptions import DatabaseError
from traj_proxy.utils.logger import get_logger

if TYPE_CHECKING:
    from traj_proxy.proxy_core.infer_client import InferClient
    from traj_proxy.store.request_repository import RequestRepository

logger = get_logger(__name__)


class BasePipeline(ABC):
    """处理管道抽象基类

    定义流式和非流式处理的通用接口。
    子类需要实现具体的处理逻辑。
    """

    def __init__(
        self,
        model: str,
        infer_client: "InferClient",
        request_repository: Optional["RequestRepository"] = None
    ):
        """初始化 BasePipeline

        Args:
            model: 模型名称
            infer_client: 推理服务客户端
            request_repository: 请求记录仓库（可选）
        """
        self.model = model
        self.infer_client = infer_client
        self.request_repository = request_repository

    @abstractmethod
    async def process(
        self,
        messages: list,
        context: ProcessContext
    ) -> ProcessContext:
        """处理非流式请求

        Args:
            messages: OpenAI 格式的消息列表
            context: 处理上下文

        Returns:
            处理后的上下文
        """
        pass

    @abstractmethod
    async def process_stream(
        self,
        messages: list,
        context: ProcessContext
    ) -> AsyncIterator[Dict[str, Any]]:
        """处理流式请求

        Args:
            messages: OpenAI 格式的消息列表
            context: 处理上下文

        Yields:
            OpenAI 格式的流式响应块
        """
        pass

    # ==================== 共享工具方法 ====================

    def _create_context(
        self,
        request_id: str,
        session_id: Optional[str],
        messages: list,
        request_params: dict,
        is_stream: bool = False,
        run_id: Optional[str] = None
    ) -> ProcessContext:
        """创建处理上下文

        Args:
            request_id: 请求 ID
            session_id: 原始会话 ID（不再自动补充前缀）
            messages: 消息列表
            request_params: 请求参数
            is_stream: 是否流式请求
            run_id: 运行 ID（可选）

        Returns:
            初始化后的上下文
        """
        unique_id = f"{session_id},{request_id}" if session_id else request_id

        context = ProcessContext(
            request_id=request_id,
            model=self.model,
            messages=messages,
            request_params=request_params,
            session_id=session_id,
            run_id=run_id,
            unique_id=unique_id,
            is_stream=is_stream
        )
        context.start_time = datetime.now()

        # 构建原始请求
        context.raw_request = {
            "model": self.model,
            "messages": messages,
            **request_params
        }

        return context

    async def _store_trajectory(
        self,
        context: ProcessContext,
        tokenizer_path: str = "",
        run_id: Optional[str] = None
    ):
        """存储轨迹到数据库

        Args:
            context: 处理上下文
            tokenizer_path: tokenizer 路径
            run_id: 运行 ID（可选）
        """
        if not self.request_repository:
            return

        try:
            await self.request_repository.insert(context, tokenizer_path, run_id)
            logger.info(f"[{context.unique_id}] 轨迹存储成功")
        except DatabaseError as e:
            context.error = f"存储轨迹失败: {str(e)}"
            logger.error(f"[{context.unique_id}] 存储轨迹失败: {str(e)}")

    def _update_timing(self, context: ProcessContext):
        """更新时间统计

        Args:
            context: 处理上下文
        """
        context.end_time = datetime.now()
        context.processing_duration_ms = (
            context.end_time - context.start_time
        ).total_seconds() * 1000

    def _handle_error(
        self,
        context: ProcessContext,
        error: Exception
    ):
        """处理错误

        Args:
            context: 处理上下文
            error: 异常
        """
        context.error = str(error)
        context.error_traceback = traceback.format_exc()
        context.end_time = datetime.now()
        logger.error(
            f"[{context.unique_id}] 处理异常: {str(error)}\n{traceback.format_exc()}"
        )
