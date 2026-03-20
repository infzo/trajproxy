"""
ProxyCore Worker实现

处理LLM请求转发
"""

from traj_proxy.workers.base import Worker
from traj_proxy.proxy_core.processor import Processor
from traj_proxy.proxy_core.infer_client import InferClient
from traj_proxy.store.database import DatabaseManager
import yaml
import os


# 全局 Processor 实例，用于依赖注入
_processor: Processor = None


def get_processor() -> Processor:
    """获取 Processor 实例，用于依赖注入"""
    global _processor
    if _processor is None:
        raise RuntimeError("Processor 未初始化")
    return _processor


def load_config() -> dict:
    """加载配置文件"""
    config_path = os.path.join(os.path.dirname(__file__), "..", "workers.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class ProxyCoreWorker(Worker):
    """代理核心Worker，处理LLM请求转发"""

    def __init__(self, worker_id: int, port: int, db_url: str):
        """
        初始化 ProxyCoreWorker

        参数:
            worker_id: Worker唯一标识
            port: 监听端口
            db_url: 数据库连接URL
        """
        self.db_url = db_url
        self.db_manager = None
        self.infer_client = None
        self.processor = None
        super().__init__(worker_id, port)

    async def initialize(self):
        """
        初始化数据库连接池、Infer 客户端和 Processor
        """
        global _processor

        # 加载配置
        config = load_config()
        proxy_core_config = config.get("proxy_core_workers", {})

        # 初始化数据库管理器
        self.db_manager = DatabaseManager(self.db_url)
        await self.db_manager.initialize()

        # 初始化 Infer 客户端
        self.infer_client = InferClient()

        # 初始化 Processor
        model = proxy_core_config.get("model", "qwen2.5-7b-instruct")
        tokenizer_path = proxy_core_config.get("tokenizer_path", "Qwen/Qwen2.5-7B-Instruct")

        self.processor = Processor(
            model=model,
            tokenizer_path=tokenizer_path,
            db_manager=self.db_manager,
            infer_client=self.infer_client
        )
        _processor = self.processor

    async def shutdown(self):
        """
        关闭资源
        """
        if self.db_manager:
            await self.db_manager.close()

    def get_worker_name(self) -> str:
        """
        返回Worker名称

        返回:
            Worker的名称字符串
        """
        return f"ProxyCoreWorker-{self.worker_id}"

    def _setup_routes(self):
        """
        设置路由

        包含健康检查和聊天补全路由
        """
        from traj_proxy.proxy_core.routes import router
        self.app.include_router(router, prefix="/v1")
