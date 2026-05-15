"""
TrajectoryProvider - 转录提供者

负责处理轨迹记录查询的业务逻辑
"""

import time
from typing import Dict, Any, Optional
from traj_proxy.store.request_repository import RequestRepository
from traj_proxy.utils.logger import get_logger

logger = get_logger(__name__)


class TrajectoryProvider:
    """转录提供者 - 处理轨迹记录查询业务逻辑

    封装数据库访问逻辑，为 routes 提供业务接口
    """

    def __init__(self, request_repository: RequestRepository):
        """初始化 TrajectoryProvider

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
            session_id: 会话ID (格式: app_id,sample_id,task_id)
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

    async def list_trajectories(
        self,
        run_id: str
    ) -> Dict[str, Any]:
        """查询指定 run_id 下的轨迹列表

        Args:
            run_id: 运行ID（必填）

        Returns:
            包含 run_id 和轨迹列表的字典
        """
        sessions = await self.request_repository.list_sessions(run_id)
        return {
            "run_id": run_id,
            "trajectories": sessions
        }

    async def get_trajectories(
        self,
        session_id: str,
        fields: Optional[str] = None
    ) -> Dict[str, Any]:
        """查询指定 session 的所有轨迹记录

        Args:
            session_id: 会话ID
            fields: 逗号分隔的字段名，None 返回全部

        Returns:
            包含 session_id 和记录列表的字典
        """
        t_start = time.perf_counter()
        records = await self.request_repository.get_all_by_session(session_id, fields=fields)
        t_elapsed = (time.perf_counter() - t_start) * 1000
        logger.info(f"[{session_id}] Provider.get_trajectories 完成: {len(records)}条记录, 耗时: {t_elapsed:.1f}ms")
        return {
            "session_id": session_id,
            "records": records
        }

    async def get_trajectory_metadata(
        self,
        session_id: str,
        limit: int = 10000,
        fields: Optional[str] = None
    ) -> Dict[str, Any]:
        """查询指定 session 的元数据（轻量查询，不含大字段）

        只查 request_metadata 表，不 JOIN 详情表，适合统计和列表展示。

        Args:
            session_id: 会话ID
            limit: 最多返回的记录数
            fields: 逗号分隔的字段名，None 返回全部

        Returns:
            包含 session_id 和元数据列表的字典
        """
        records = await self.request_repository.get_metadata_by_session(session_id, limit, fields=fields)
        return {
            "session_id": session_id,
            "count": len(records),
            "records": records
        }
