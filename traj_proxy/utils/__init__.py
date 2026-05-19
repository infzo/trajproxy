"""
工具模块
"""

from datetime import datetime, timezone

from traj_proxy.utils.logger import get_logger, update_worker_id, WorkerIDFilter


def utcnow() -> datetime:
    """返回当前 UTC 时区感知时间"""
    return datetime.now(timezone.utc)


__all__ = ['get_logger', 'update_worker_id', 'WorkerIDFilter', 'utcnow']
