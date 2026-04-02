"""
Worker 实现

集成了 ProxyCore 和 TranscriptProvider 的所有功能，整合了 FastAPI 基类逻辑
"""

from fastapi import FastAPI, Request
from typing import Optional
import time
import traceback
import uuid
import logging

from traj_proxy.store.database_manager import DatabaseManager
from traj_proxy.store.request_repository import RequestRepository
from traj_proxy.proxy_core.processor_manager import ProcessorManager
from traj_proxy.transcript_provider.provider import TranscriptProvider
from traj_proxy.utils.logger import get_logger
from traj_proxy.utils.config import get_database_pool_config
from traj_proxy.utils.validators import normalize_run_id

logger = get_logger(__name__)


def get_processor_manager(request: Optional[Request] = None) -> ProcessorManager:
    """
    获取 ProcessorManager 实例

    优先从请求上下文获取，其次从全局状态获取。
    这种设计支持依赖注入，同时保持向后兼容。

    Args:
        request: FastAPI Request 对象（可选）

    Returns:
        ProcessorManager 实例

    Raises:
        RuntimeError: Worker 未初始化或 ProcessorManager 未初始化
    """
    # 尝试从请求上下文获取
    if request is not None:
        pm = getattr(request.app.state, "processor_manager", None)
        if pm is not None:
            return pm

    # 使用线程本地存储获取当前 Worker 的 app 实例
    app = _get_current_app()
    if app is None:
        raise RuntimeError("Worker 未初始化")

    pm = getattr(app.state, "processor_manager", None)
    if pm is None:
        raise RuntimeError("ProcessorManager 未初始化")
    return pm


def get_transcript_provider(request: Optional[Request] = None) -> TranscriptProvider:
    """
    获取 TranscriptProvider 实例

    优先从请求上下文获取，其次从全局状态获取。

    Args:
        request: FastAPI Request 对象（可选）

    Returns:
        TranscriptProvider 实例

    Raises:
        RuntimeError: Worker 未初始化或 TranscriptProvider 未初始化
    """
    # 尝试从请求上下文获取
    if request is not None:
        tp = getattr(request.app.state, "transcript_provider", None)
        if tp is not None:
            return tp

    # 使用线程本地存储获取当前 Worker 的 app 实例
    app = _get_current_app()
    if app is None:
        raise RuntimeError("Worker 未初始化")

    tp = getattr(app.state, "transcript_provider", None)
    if tp is None:
        raise RuntimeError("TranscriptProvider 未初始化")
    return tp


# 线程本地存储，用于存储当前 Worker 的 app 实例
import threading
_thread_local = threading.local()


def _get_current_app() -> Optional[FastAPI]:
    """获取当前线程关联的 FastAPI app 实例"""
    return getattr(_thread_local, 'app', None)


def _set_current_app(app: Optional[FastAPI]):
    """设置当前线程关联的 FastAPI app 实例"""
    _thread_local.app = app


class ProxyWorker:
    """
    统一的代理 Worker

    集成了 ProxyCoreWorker 和 TranscriptProviderWorker 的所有功能：
    - 处理 LLM 请求转发
    - 管理多个模型
    - 提供轨迹记录查询
    """

    def __init__(self, worker_id: int, port: int, db_url: str):
        """
        初始化 ProxyWorker

        参数:
            worker_id: Worker 唯一标识
            port: 监听端口
            db_url: 数据库连接 URL
        """
        self.worker_id = worker_id
        self.port = port
        self.app = FastAPI(title=f"ProxyWorker-{worker_id}")

        # 设置当前线程的 app 引用（用于依赖注入函数）
        _set_current_app(self.app)

        # 为该Worker创建独立的日志记录器
        worker_name = f"ProxyWorker-{worker_id}"
        self.logger = get_logger(__name__, worker_id=worker_name)
        self.logger.info(f"Worker 初始化完成: {worker_name} (端口: {port})")

        # 初始化组件（稍后在 initialize 中完成）
        self.db_url = db_url
        self.db_manager: Optional[DatabaseManager] = None
        self.processor_manager: Optional[ProcessorManager] = None
        self.transcript_provider: Optional[TranscriptProvider] = None

        # 设置中间件和路由
        self._setup_middleware()
        self._setup_routes()

    def _setup_middleware(self):
        """设置中间件：为所有请求添加日志记录"""
        @self.app.middleware("http")
        async def request_logging_middleware(request: Request, call_next):
            """请求日志中间件：记录请求详情和响应耗时"""
            start_time = time.time()
            request_id = str(uuid.uuid4())[:8]

            # 记录请求详情
            url = str(request.url)
            method = request.method
            client_host = request.client.host if request.client else "unknown"

            # 获取模型名（如果有的话）
            model = request.query_params.get("model", "unknown")
            if model == "unknown":
                if "/chat/completions" in url:
                    model = "chat"
                elif "/models" in url:
                    model = "models"

            self.logger.info(
                f"Request: {method} {url} | "
                f"request_id={request_id} | "
                f"model={model} | "
                f"client={client_host}"
            )

            # 处理请求
            try:
                response = await call_next(request)
            except Exception as e:
                elapsed_ms = int((time.time() - start_time) * 1000)
                self.logger.error(
                    f"Request failed: {method} {url} | "
                    f"request_id={request_id} | "
                    f"status=500 | "
                    f"elapsed={elapsed_ms}ms | "
                    f"error={str(e)}\n{traceback.format_exc()}"
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

        @self.app.middleware("http")
        async def session_id_path_middleware(request, call_next):
            """从路径中提取 session_id 并注入到请求头 x-session-id"""
            path = request.url.path

            if path.startswith("/s/") and "/v1/chat/completions" in path:
                try:
                    parts = path.split("/")
                    if len(parts) >= 4 and parts[1] == "s" and parts[3] == "v1":
                        session_id = parts[2]
                        if not request.headers.get("x-session-id"):
                            request.state.session_id_from_path = session_id
                except (IndexError, AttributeError):
                    pass

            response = await call_next(request)
            return response

    def _setup_routes(self):
        """设置路由"""
        from traj_proxy.workers.route_registrar import RouteRegistrar
        registrar = RouteRegistrar(self.app)
        registrar.register_all()

    async def initialize(self):
        """
        初始化数据库连接池、ProcessorManager、TranscriptProvider 和默认模型
        """
        # 加载配置
        from traj_proxy.utils.config import get_proxy_workers_config, load_config

        config = load_config()
        proxy_workers_config = get_proxy_workers_config()
        models_config = proxy_workers_config.get("models", [])

        # 获取连接池配置
        pool_config = get_database_pool_config()

        # 初始化数据库管理器（共享）
        self.db_manager = DatabaseManager(self.db_url, pool_config)
        await self.db_manager.initialize()

        # 初始化 RequestRepository（共享）
        request_repository = RequestRepository(self.db_manager.pool)

        # 创建 ProcessorManager
        self.processor_manager = ProcessorManager(self.db_manager, db_url=self.db_url)
        self.app.state.processor_manager = self.processor_manager

        # 创建 TranscriptProvider（共享 request_repository）
        self.transcript_provider = TranscriptProvider(request_repository)
        self.app.state.transcript_provider = self.transcript_provider

        # 启动模型同步
        await self.processor_manager.start_sync()

        # 注册预置模型（不持久化到数据库）
        if models_config:
            for model_config in models_config:
                try:
                    self.processor_manager.register_static_processor(
                        model_name=model_config.get('model_name'),
                        url=model_config.get('url'),
                        api_key=model_config.get('api_key'),
                        tokenizer_path=model_config.get('tokenizer_path'),
                        token_in_token_out=model_config.get('token_in_token_out', False),
                        run_id=normalize_run_id(model_config.get('run_id')),
                        tool_parser=model_config.get('tool_parser', ''),
                        reasoning_parser=model_config.get('reasoning_parser', '')
                    )
                    logger.info(f"预置模型注册成功: {model_config.get('model_name')}")
                except ValueError as e:
                    logger.warning(f"预置模型注册失败（可能已存在）: {model_config.get('model_name')}, 错误: {e}")
                except Exception as e:
                    logger.error(f"预置模型注册异常: {model_config.get('model_name')}, 错误: {e}\n{traceback.format_exc()}")

        logger.info(f"ProxyWorker 初始化完成，预置模型: {len(self.processor_manager.config_processors)}, 动态模型: {len(self.processor_manager.dynamic_processors)}")

    async def shutdown(self):
        """关闭资源"""
        if self.processor_manager:
            try:
                await self.processor_manager.stop_sync()
            except Exception as e:
                logger.error(f"停止同步失败: {e}\n{traceback.format_exc()}")

        if self.db_manager:
            await self.db_manager.close()

    def run(self):
        """启动FastAPI应用"""
        import uvicorn
        uvicorn.run(self.app, host="0.0.0.0", port=self.port)

    def get_health_status(self) -> dict:
        """健康检查接口"""
        return {
            "worker_id": self.worker_id,
            "status": "healthy",
            "port": self.port
        }
