"""
Worker抽象基类

所有Worker类需继承此类并实现抽象方法
"""

from abc import ABC, abstractmethod
from fastapi import FastAPI, Request
from typing import Optional
import time
import uuid
import logging
from traj_proxy.utils.logger import get_logger, WorkerIDFilter


class Worker(ABC):
    """Worker抽象基类，所有Worker需继承此类"""

    def __init__(self, worker_id: int, port: int):
        """
        初始化Worker

        参数:
            worker_id: Worker唯一标识
            port: 监听端口
        """
        self.worker_id = worker_id
        self.port = port
        self.app = FastAPI(title=self.get_worker_name())
        self._setup_routes()
        self._setup_middleware()

        # 为该Worker创建独立的日志记录器
        worker_name = f"{self.get_worker_name()}-{worker_id}"
        self.logger = get_logger(__name__, worker_id=worker_name)
        self.logger.info(f"Worker 初始化完成: {worker_name} (端口: {port})")

    @abstractmethod
    def get_worker_name(self) -> str:
        """
        返回Worker名称

        返回:
            Worker的唯一名称
        """
        pass

    @abstractmethod
    def _setup_routes(self):
        """
        设置路由

        子类需要在此方法中配置FastAPI路由
        """
        pass

    def _setup_middleware(self):
        """
        设置中间件

        为所有请求添加日志记录中间件
        """
        @self.app.middleware("http")
        async def request_logging_middleware(request: Request, call_next):
            """
            请求日志中间件

            记录请求详情和响应耗时
            """
            start_time = time.time()
            request_id = str(uuid.uuid4())[:8]

            # 记录请求详情
            url = str(request.url)
            method = request.method
            client_host = request.client.host if request.client else "unknown"

            # 获取模型名（如果有的话）
            try:
                body = await request.json()
                model = body.get("model", "unknown")
            except Exception:
                model = "unknown"
                # 需要重新读取请求体（因为已经读取过了）
                request.state._body = None

            self.logger.info(
                f"Request: {method} {url} | "
                f"request_id={request_id} | "
                f"model={model} | "
                f"client={client_host}"
            )

            # 如果是DEBUG级别，记录完整的请求体
            if self.logger.isEnabledFor(logging.DEBUG):
                try:
                    self.logger.debug(f"Request body: {await request.json()}")
                except Exception:
                    pass

            # 处理请求
            try:
                response = await call_next(request)
            except Exception as e:
                # 记录异常
                elapsed_ms = int((time.time() - start_time) * 1000)
                self.logger.error(
                    f"Request failed: {method} {url} | "
                    f"request_id={request_id} | "
                    f"status=500 | "
                    f"elapsed={elapsed_ms}ms | "
                    f"error={str(e)}"
                )
                raise

            # 记录响应详情
            elapsed_ms = int((time.time() - start_time) * 1000)
            status_code = response.status_code

            self.logger.info(
                f"Response: {method} {url} | "
                f"request_id={request_id} | "
                f"status={status_code} | "
                f"elapsed={elapsed_ms}ms"
            )

            # 添加请求ID到响应头
            response.headers["X-Request-ID"] = request_id

            return response

    def run(self):
        """
        启动FastAPI应用
        """
        import uvicorn
        uvicorn.run(self.app, host="0.0.0.0", port=self.port)

    def get_health_status(self) -> dict:
        """
        健康检查接口

        返回:
            包含Worker状态信息的字典
        """
        return {
            "worker_id": self.worker_id,
            "status": "healthy",
            "port": self.port
        }
