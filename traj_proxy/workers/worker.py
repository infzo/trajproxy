"""
Worker 实现

集成了 ProxyCore 和 TranscriptProvider 的所有功能，整合了 FastAPI 基类逻辑
"""

from fastapi import FastAPI, Request
from typing import Optional
import time
import uuid
import logging

from traj_proxy.store.database_manager import DatabaseManager
from traj_proxy.store.request_repository import RequestRepository
from traj_proxy.proxy_core.processor_manager import ProcessorManager
from traj_proxy.transcript_provider.provider import TranscriptProvider
from traj_proxy.utils.logger import get_logger
from traj_proxy.utils.config import get_database_pool_config

logger = get_logger(__name__)


# 全局实例，用于依赖注入（每个 Ray Actor 有独立的命名空间）
_processor_manager: Optional[ProcessorManager] = None
_transcript_provider: Optional[TranscriptProvider] = None


def get_processor_manager() -> ProcessorManager:
    """获取 ProcessorManager 实例，用于依赖注入"""
    global _processor_manager
    if _processor_manager is None:
        raise RuntimeError("ProcessorManager 未初始化")
    return _processor_manager


def get_transcript_provider() -> TranscriptProvider:
    """获取 TranscriptProvider 实例，用于依赖注入"""
    global _transcript_provider
    if _transcript_provider is None:
        raise RuntimeError("TranscriptProvider 未初始化")
    return _transcript_provider


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
            # 注意：不能直接调用 request.json()，否则会消费请求体
            # 从 query 参数或 header 中获取 model 信息作为替代
            model = request.query_params.get("model", "unknown")
            if model == "unknown":
                # 尝试从 path 中提取
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
                # 记录异常
                import traceback
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

    def _setup_routes(self):
        """设置路由"""
        # 使用路由注册器
        from traj_proxy.workers.route_registrar import RouteRegistrar
        registrar = RouteRegistrar(self.app)
        registrar.register_all()

        # ========== 中间件：从路径中提取 session_id ==========
        @self.app.middleware("http")
        async def session_id_path_middleware(request, call_next):
            """从路径中提取 session_id 并注入到请求头 x-session-id"""
            path = request.url.path

            # 检查是否匹配新格式：/proxy/{session_id}/v1/chat/completions
            if path.startswith("/proxy/") and "/v1/chat/completions" in path:
                try:
                    parts = path.split("/")
                    if len(parts) >= 4 and parts[1] == "proxy" and parts[3] == "v1":
                        session_id = parts[2]
                        # 如果 header 中没有 session_id，从路径提取
                        if not request.headers.get("x-session-id"):
                            request.state.session_id_from_path = session_id
                except (IndexError, AttributeError):
                    pass

            response = await call_next(request)
            return response

    async def initialize(self):
        """
        初始化数据库连接池、ProcessorManager、TranscriptProvider 和默认模型
        """
        global _processor_manager, _transcript_provider

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
        self.processor_manager = ProcessorManager(self.db_manager)
        _processor_manager = self.processor_manager

        # 创建 TranscriptProvider（共享 request_repository）
        self.transcript_provider = TranscriptProvider(request_repository)
        _transcript_provider = self.transcript_provider

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
                        tool_parser=model_config.get('tool_parser', ''),
                        reasoning_parser=model_config.get('reasoning_parser', '')
                    )
                    logger.info(f"预置模型注册成功: {model_config.get('model_name')}")
                except ValueError as e:
                    logger.warning(f"预置模型注册失败（可能已存在）: {model_config.get('model_name')}, 错误: {e}")
                except Exception as e:
                    import traceback
                    logger.error(f"预置模型注册异常: {model_config.get('model_name')}, 错误: {e}\n{traceback.format_exc()}")

        logger.info(f"ProxyWorker 初始化完成，预置模型: {len(self.processor_manager.config_processors)}, 动态模型: {len(self.processor_manager.dynamic_processors)}")

    async def shutdown(self):
        """关闭资源"""
        if self.processor_manager:
            try:
                await self.processor_manager.stop_sync()
            except Exception as e:
                import traceback
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
