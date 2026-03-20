"""
Ray进程管理器

使用Ray创建和管理Worker进程
"""

import ray
import asyncio
from typing import List, Dict
from traj_proxy.proxy_core.worker import ProxyCoreWorker
from traj_proxy.transcript_provider.worker import TranscriptProviderWorker
from traj_proxy.utils.logger import get_logger

# 配置日志
logger = get_logger(__name__)


@ray.remote
class RemoteWorker:
    """
    Ray远程Worker封装

    将Worker类包装为Ray Actor，实现远程执行
    """

    def __init__(self, worker_class, worker_id: int, port: int, db_url: str = None):
        """
        初始化远程Worker

        参数:
            worker_class: Worker类类型
            worker_id: Worker唯一标识
            port: 监听端口
            db_url: 数据库连接URL（可选）
        """
        self.worker_class = worker_class
        self.worker_id = worker_id
        self.port = port
        self.db_url = db_url
        self.worker = None

    async def initialize(self):
        """
        初始化Worker实例并在后台运行uvicorn服务器

        需要在Actor启动后显式调用
        """
        try:
            # 为当前Worker创建独立的日志记录器
            worker_name = f"{self.worker_class.__name__}-{self.worker_id}"
            self.worker_logger = get_logger(__name__, worker_id=worker_name)

            # 如果有db_url参数，则传递给Worker
            if self.db_url:
                self.worker = self.worker_class(self.worker_id, self.port, self.db_url)
            else:
                self.worker = self.worker_class(self.worker_id, self.port)

            self.worker_logger.info(f"RemoteWorker initialized: {self.worker_class.__name__}-{self.worker_id} on port {self.port}")

            # 如果Worker有initialize方法，则调用
            if hasattr(self.worker, 'initialize'):
                await self.worker.initialize()

            # 在后台任务中启动uvicorn服务器
            import uvicorn
            from threading import Thread

            def run_server():
                uvicorn.run(self.worker.app, host="0.0.0.0", port=self.port, log_level="info")

            thread = Thread(target=run_server, daemon=True)
            thread.start()

            self.worker_logger.info(f"Started uvicorn server for {self.worker_class.__name__}-{self.worker_id} on port {self.port}")
        except Exception as e:
            self.worker_logger.error(f"Failed to initialize RemoteWorker: {e}")
            raise

    def get_info(self) -> Dict:
        """
        获取Worker信息

        返回:
            包含Worker状态信息的字典
        """
        if self.worker is None:
            return {
                "worker_id": self.worker_id,
                "status": "not_initialized",
                "port": self.port
            }
        return self.worker.get_health_status()

    def run(self):
        """
        运行Worker

        启动FastAPI应用（注意：这会阻塞当前Actor）
        """
        if self.worker is None:
            self.initialize()
        self.worker_logger.info(f"Starting worker: {self.worker_class.__name__}-{self.worker_id}")
        self.worker.run()


class WorkerManager:
    """
    Worker管理器

    使用Ray创建和管理多个Worker进程
    """

    def __init__(self, config: dict):
        """
        初始化Worker管理器

        参数:
            config: Worker配置字典
        """
        # 获取Ray配置
        ray_config = config.get("ray", {})
        num_cpus = ray_config.get("num_cpus", 4)

        # 初始化Ray - 增加稳定性配置
        ray.init(
            ignore_reinit_error=True,
            num_cpus=num_cpus,
            runtime_env={
                "working_dir": "/app/traj_proxy",
                "env_vars": {
                    "PYTHONPATH": "/app/traj_proxy"
                }
            },
            log_to_driver=True,
            _system_config={
                "automatic_object_spilling_enabled": False,
                "worker_lease_timeout_milliseconds": 10000,
            }
        )
        logger.info("Ray initialized successfully")

        self.proxy_workers: List[ray.ActorHandle] = []
        self.transcript_workers: List[ray.ActorHandle] = []
        self.config = config

    async def start_workers(self):
        """
        启动所有Worker

        创建并初始化所有配置的Worker Actor
        """
        logger.info("Starting workers...")

        # 启动ProxyCore Workers
        proxy_config = self.config.get("proxy_core_workers", {})
        proxy_count = proxy_config.get("count", 1)
        proxy_base_port = proxy_config.get("base_port", 8000)

        # 获取数据库URL
        db_config = self.config.get("database", {})
        db_url = db_config.get("url")

        logger.info(f"Starting {proxy_count} ProxyCore workers from port {proxy_base_port}")
        for i in range(proxy_count):
            port = proxy_base_port + i
            worker = RemoteWorker.remote(ProxyCoreWorker, i, port, db_url)
            await worker.initialize.remote()
            self.proxy_workers.append(worker)
            logger.info(f"ProxyCore Worker {i} started on port {port}")

        # 启动TranscriptProvider Workers
        transcript_config = self.config.get("transcript_provider_workers", {})
        transcript_count = transcript_config.get("count", 1)
        transcript_base_port = transcript_config.get("base_port", 9000)

        # 获取数据库URL
        db_config = self.config.get("database", {})
        db_url = db_config.get("url")

        logger.info(f"Starting {transcript_count} TranscriptProvider workers from port {transcript_base_port}")
        for i in range(transcript_count):
            port = transcript_base_port + i
            worker = RemoteWorker.remote(TranscriptProviderWorker, i, port, db_url)
            await worker.initialize.remote()
            self.transcript_workers.append(worker)
            logger.info(f"TranscriptProvider Worker {i} started on port {port}")

        logger.info(f"All workers started: {len(self.proxy_workers)} ProxyCore, {len(self.transcript_workers)} TranscriptProvider")

    async def get_worker_status(self) -> Dict:
        """
        获取所有Worker状态

        返回:
            包含所有Worker状态的字典
        """
        # 使用异步方式获取所有Worker状态
        proxy_futures = [w.get_info.remote() for w in self.proxy_workers]
        transcript_futures = [w.get_info.remote() for w in self.transcript_workers]

        # 等待结果
        proxy_status = await asyncio.gather(*[ray.get(f) for f in proxy_futures])
        transcript_status = await asyncio.gather(*[ray.get(f) for f in transcript_futures])

        return {
            "proxy_core": proxy_status,
            "transcript_provider": transcript_status
        }

    def get_worker_status_sync(self) -> Dict:
        """
        同步方式获取所有Worker状态

        返回:
            包含所有Worker状态的字典
        """
        proxy_status = [ray.get(w.get_info.remote()) for w in self.proxy_workers]
        transcript_status = [ray.get(w.get_info.remote()) for w in self.transcript_workers]

        return {
            "proxy_core": proxy_status,
            "transcript_provider": transcript_status
        }

    def shutdown(self):
        """
        关闭所有Worker和Ray

        清理资源
        """
        logger.warning("Shutting down WorkerManager...")
        ray.shutdown()
        logger.info("Ray shutdown complete")
