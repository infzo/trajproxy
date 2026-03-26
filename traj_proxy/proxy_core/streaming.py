"""
Streaming - 流式响应工具

提供流式响应生成器和 SSE 格式化功能。
"""

from typing import AsyncIterator, Dict, Any, Optional
import json

from traj_proxy.utils.logger import get_logger

logger = get_logger(__name__)


def format_sse(data: Any) -> str:
    """格式化为 SSE 格式

    Args:
        data: 要发送的数据（字典或字符串）

    Returns:
        SSE 格式的字符串
    """
    if isinstance(data, str):
        return f"data: {data}\n\n"
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


class StreamingResponseGenerator:
    """流式响应生成器

    封装流式响应的生成逻辑，支持：
    - SSE 格式化
    - 错误处理
    - 资源清理
    """

    def __init__(
        self,
        processor,
        messages: list,
        request_id: str,
        session_id: Optional[str] = None,
        **request_params
    ):
        """初始化生成器

        Args:
            processor: Processor 实例
            messages: 消息列表
            request_id: 请求 ID
            session_id: 会话 ID
            **request_params: 请求参数
        """
        self.processor = processor
        self.messages = messages
        self.request_id = request_id
        self.session_id = session_id
        self.request_params = request_params

    async def generate(self) -> AsyncIterator[str]:
        """生成流式响应

        Yields:
            SSE 格式的字符串
        """
        try:
            async for chunk in self.processor.process_request_stream(
                messages=self.messages,
                request_id=self.request_id,
                session_id=self.session_id,
                **self.request_params
            ):
                yield format_sse(chunk)

            # 发送结束标记
            yield format_sse("[DONE]")

        except Exception as e:
            logger.error(f"流式响应生成错误: {e}")
            # 发送错误信息
            error_chunk = {
                "error": {
                    "message": str(e),
                    "type": "stream_error"
                }
            }
            yield format_sse(error_chunk)
            yield format_sse("[DONE]")
