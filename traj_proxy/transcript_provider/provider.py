"""
TranscriptProvider - 转录提供者

负责处理轨迹记录查询的业务逻辑
"""

from typing import List, Dict, Any
from traj_proxy.store.request_repository import RequestRepository


class TranscriptProvider:
    """转录提供者 - 处理轨迹记录查询业务逻辑

    封装数据库访问逻辑，为 routes 提供业务接口
    """

    def __init__(self, request_repository: RequestRepository):
        """初始化 TranscriptProvider

        Args:
            request_repository: 请求记录仓库
        """
        self.request_repository = request_repository

    async def get_trajectory(
        self,
        session_id: str,
        limit: int = 10000
    ) -> Dict[str, Any]:
        """根据 session_id 获取所有轨迹记录

        Args:
            session_id: 会话ID (格式: app_id;sample_id;task_id)
            limit: 最多返回的记录数，默认为100

        Returns:
            包含session_id、记录数量和记录列表的字典
        """
        records = await self.request_repository.get_by_session(session_id, limit)
        return {
            "session_id": session_id,
            "count": len(records),
            "records": records
        }
