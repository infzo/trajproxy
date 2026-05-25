"""
归档进程工具函数

不依赖 traj_proxy 的任何模块。
"""

from datetime import datetime, timezone


def utcnow() -> datetime:
    """返回当前 UTC 时间（时区感知）"""
    return datetime.now(timezone.utc)
