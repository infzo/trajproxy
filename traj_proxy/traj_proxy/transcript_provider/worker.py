"""
TranscriptProvider Worker实现

处理文本转录相关请求
"""

from traj_proxy.workers.base import Worker
from traj_proxy.store.database import DatabaseManager


# 全局DatabaseManager实例，用于依赖注入
_db_manager: DatabaseManager = None


def get_db_manager() -> DatabaseManager:
    """获取DatabaseManager实例，用于依赖注入"""
    global _db_manager
    if _db_manager is None:
        raise RuntimeError("DatabaseManager未初始化")
    return _db_manager


class TranscriptProviderWorker(Worker):
    """转录提供者Worker，处理文本转录相关请求"""

    def __init__(self, worker_id: int, port: int, db_url: str):
        """
        初始化TranscriptProviderWorker

        参数:
            worker_id: Worker唯一标识
            port: 监听端口
            db_url: 数据库连接URL
        """
        self.db_url = db_url
        self.db_manager = None
        super().__init__(worker_id, port)

    def get_worker_name(self) -> str:
        """
        返回Worker名称

        返回:
            Worker的名称字符串
        """
        return f"TranscriptProviderWorker-{self.worker_id}"

    async def initialize(self):
        """
        初始化数据库连接池
        """
        global _db_manager
        self.db_manager = DatabaseManager(self.db_url)
        await self.db_manager.initialize()
        _db_manager = self.db_manager

    def _setup_routes(self):
        """
        设置路由

        包含健康检查和转录处理路由
        """
        from traj_proxy.transcript_provider.routes import router
        self.app.include_router(router, prefix="/transcript")
