"""
Worker 实现

集成了 ProxyCore 和 TranscriptProvider 的所有功能，整合了 FastAPI 基类逻辑
"""

import asyncio
import time
import threading

import uuid
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import Response

from traj_proxy.observability import metrics_collector
from traj_proxy.proxy_core.infer_client import close_shared_client
from traj_proxy.proxy_core.processor_manager import ProcessorManager
from traj_proxy.proxy_core.provider import TrajectoryProvider
from traj_proxy.store.database_manager import DatabaseManager
from traj_proxy.store.model_synchronizer import ModelSynchronizer
from traj_proxy.store.request_repository import RequestRepository
from traj_proxy.utils.config import get_database_pool_config, get_gzip_config, get_max_concurrent_requests, get_storage_mode
from traj_proxy.utils.logger import get_logger
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


def get_transcript_provider(request: Optional[Request] = None) -> TrajectoryProvider:
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


# 轨迹路由集合（用于中间件兜底错误上报）
_TRAJECTORY_ROUTES = frozenset({"trajectory_list", "trajectory_detail", "trajectory_legacy"})


def _emit_api_error_silently(route: str, run_id: str, error_category: str) -> None:
    """fire-and-forget 上报 API 错误，仅用于中间件兜底，不影响主业务流"""
    try:
        from traj_proxy.observability.event_bus import emit
        from traj_proxy.observability.events import EVENT_API_ERROR
        emit(EVENT_API_ERROR, route=route, run_id=run_id, error_category=error_category)
    except Exception:
        pass


# 线程本地存储，用于存储当前 Worker 的 app 实例
_thread_local = threading.local()


def _get_current_app() -> Optional[FastAPI]:
    """获取当前线程关联的 FastAPI app 实例"""
    return getattr(_thread_local, 'app', None)


def _set_current_app(app: Optional[FastAPI]):
    """设置当前线程关联的 FastAPI app 实例"""
    _thread_local.app = app


def classify_route(request: Request) -> str:
    """路由分类器：将请求路径归一化为有限标签，避免高基数

    Args:
        request: FastAPI Request 对象

    Returns:
        归一化后的路由标签字符串
    """
    path = request.url.path
    method = request.method
    if "chat/completions" in path:
        return "chat_completions"
    if path.startswith("/trajectories"):
        return "trajectory_list" if path == "/trajectories" else "trajectory_detail"
    if path.startswith("/trajectory"):
        return "trajectory_legacy"
    if path.startswith("/models"):
        if path == "/models/register" and method == "POST":
            return "model_register"
        if method == "DELETE":
            return "model_delete"
        return "model_list"
    if path == "/health":
        return "health"
    if path == "/metrics":
        return "metrics"
    return "other"


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

        # 并发限流信号量（单 worker 级别）
        max_concurrent = get_max_concurrent_requests()
        self._request_semaphore = asyncio.Semaphore(max_concurrent)
        self.logger.info(f"并发限流已启用: max_concurrent={max_concurrent}")

        # 初始化组件（稍后在 initialize 中完成）
        self.db_url = db_url
        self.db_manager: Optional[DatabaseManager] = None
        self.processor_manager: Optional[ProcessorManager] = None
        self.model_synchronizer: Optional[ModelSynchronizer] = None
        self.transcript_provider: Optional[TrajectoryProvider] = None

        # 设置中间件和路由
        self._setup_middleware()
        self._setup_routes()

    def _setup_middleware(self):
        """设置中间件：CORS 支持和请求日志"""
        # 添加 CORS 中间件，允许本地开发工具访问
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # 置于 CORS 之后、日志中间件之前，使日志中间件位于最外层记录原始请求
        gzip_cfg = get_gzip_config()
        if gzip_cfg["enabled"]:
            self.app.add_middleware(
                GZipMiddleware,
                minimum_size=gzip_cfg["minimum_size"],
            )
            self.logger.info(
                f"gzip 响应压缩已启用: minimum_size={gzip_cfg['minimum_size']}"
            )
        else:
            self.logger.info("gzip 响应压缩未启用（gzip_enabled=false）")

        @self.app.middleware("http")
        async def request_logging_middleware(request: Request, call_next):
            """请求日志中间件：记录请求详情和响应耗时"""
            start_time = time.time()
            request_id = str(uuid.uuid4())
            request.state.request_id = request_id
            request.state.request_start_time = start_time
            # 同时设置 ContextVar 供日志 Filter 使用
            from traj_proxy.observability.request_context import set_request_id
            set_request_id(request_id)

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
                f"收到请求: method={method}, url={url}, "
                f"request_id={request_id}, model={model}, client={client_host}"
            )

            # 处理请求
            try:
                response = await call_next(request)
            except Exception as e:
                elapsed_ms = int((time.time() - start_time) * 1000)
                self.logger.error(
                    f"请求处理失败: method={method}, url={url}, "
                    f"request_id={request_id}, status=500, elapsed={elapsed_ms}ms, error={e}",
                    exc_info=True,
                )
                raise

            # 记录响应详情
            elapsed_ms = int((time.time() - start_time) * 1000)
            status_code = response.status_code

            self.logger.info(
                f"响应完成: method={method}, url={url}, "
                f"request_id={request_id}, status={status_code}, elapsed={elapsed_ms}ms"
            )

            # 添加请求ID到响应头
            response.headers["X-Request-ID"] = request_id[:8]

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

        @self.app.middleware("http")
        async def api_metrics_middleware(request: Request, call_next) -> Response:
            """HTTP API 层指标中间件：记录每条路由的请求数和耗时

            run_id 提取策略：
            - trajectory_list：从 query param `run_id` 提取（路由处理前）
            - trajectory_detail / trajectory_legacy：由路由 handler 写入 `request.state.metric_run_id`，调用后读取

            错误上报兜底策略：
            - FastAPI 框架在路由函数执行**之前**拦截的错误（如 422 参数校验）不会进入业务层
              try/except，导致 trajproxy_api_errors_total 细分指标漏记。
            - 本中间件在响应/异常路径末尾对轨迹路由做兜底上报：
                * 422 → error_category="validation"
                * 未捕获异常 → error_category="server_error"
            - 与业务层 _emit_api_error 不冲突：业务层只产生 timeout/database/rate_limit/
              serialize_timeout/other 分类，不会产生 validation/server_error。
            """
            from traj_proxy.observability.label_guards import safe_run_id_label

            route = classify_route(request)
            method = request.method

            # trajectory_list 路由可直接从 query param 提取 run_id
            if route == "trajectory_list":
                raw_run_id = request.query_params.get("run_id") or ""
                request.state.metric_run_id = safe_run_id_label(normalize_run_id(raw_run_id or None))

            t0 = time.perf_counter()
            # 必须通过模块属性访问——register_all() 才创建真正指标，值导入会拿到初始 None
            if metrics_collector.API_REQUEST_DURATION is None or metrics_collector.API_REQUESTS_TOTAL is None:
                if not getattr(request.app.state, "_metrics_none_warned", False):
                    logger.warning(
                        "API metrics 指标未初始化（register_all() 未执行？），指标采集已跳过。"
                        "这可能是启动阶段的正常行为（如健康检查请求），请观察后续是否恢复正常。"
                    )
                    request.app.state._metrics_none_warned = True
                return await call_next(request)
            try:
                response = await call_next(request)
            except Exception:
                duration = time.perf_counter() - t0
                # 异常路径：尝试读取 handler 写入的 metric_run_id，否则取空
                run_id_label = getattr(request.state, "metric_run_id", "")
                metrics_collector.API_REQUEST_DURATION.labels(route=route, run_id=run_id_label).observe(duration)
                metrics_collector.API_REQUESTS_TOTAL.labels(
                    route=route, method=method, status_code="500", run_id=run_id_label
                ).inc()
                # 未捕获异常兜底上报（业务代码必然未执行 _emit_api_error，否则会被捕获为 HTTPException）
                if route in _TRAJECTORY_ROUTES:
                    _emit_api_error_silently(route, run_id_label, "server_error")
                raise
            duration = time.perf_counter() - t0
            code = str(response.status_code)
            run_id_label = getattr(request.state, "metric_run_id", "")
            metrics_collector.API_REQUEST_DURATION.labels(route=route, run_id=run_id_label).observe(duration)
            metrics_collector.API_REQUESTS_TOTAL.labels(
                route=route, method=method, status_code=code, run_id=run_id_label
            ).inc()
            # 框架层 422 兜底上报（业务代码在 422 场景下不会执行 _emit_api_error）
            # 通过 request.state 标志防止与业务层重复上报
            if route in _TRAJECTORY_ROUTES and code == "422":
                if not getattr(request.state, "_api_error_emitted", False):
                    _emit_api_error_silently(route, run_id_label, "validation")
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
        # 初始化可观测性系统
        from traj_proxy.observability import setup as setup_observability
        setup_observability(self.app)

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
        request_repository = RequestRepository(self.db_manager.pool, get_storage_mode())

        # 创建 ProcessorManager
        self.processor_manager = ProcessorManager(self.db_manager)
        self.app.state.processor_manager = self.processor_manager

        # 创建 TranscriptProvider（共享 request_repository）
        self.transcript_provider = TrajectoryProvider(request_repository)
        self.app.state.transcript_provider = self.transcript_provider

        # 创建 ModelSynchronizer 并启动同步
        self.model_synchronizer = ModelSynchronizer(
            model_registry=self.processor_manager.model_registry,
            db_url=self.db_url,
            on_model_register=self.processor_manager.register_from_config,
            on_model_unregister=self.processor_manager.unregister_by_key,
            on_full_sync=self.processor_manager.full_sync,
        )
        await self.model_synchronizer.start()

        # 启动空闲淘汰
        await self.processor_manager.start_idle_eviction()

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
                    logger.error(f"预置模型注册异常: {model_config.get('model_name')}, 错误: {e}", exc_info=True)

        # 将并发限流信号量挂到 app.state，供路由使用
        self.app.state.request_semaphore = self._request_semaphore
        self.app.state.max_concurrent_requests = get_max_concurrent_requests()

        logger.info(f"ProxyWorker 初始化完成，预置模型: {self.processor_manager.config_processor_count}, "
                    f"动态模型: {self.processor_manager.dynamic_processor_count}")

    async def shutdown(self):
        """关闭资源"""
        if self.processor_manager:
            # 停止空闲淘汰
            await self.processor_manager.stop_idle_eviction()
            # 清理 LRU 缓存
            await self.processor_manager.clear_cache()

        # 关闭进程级共享 HTTP client（必须在 Processor 清理之后）
        await close_shared_client()

        if self.model_synchronizer:
            try:
                await self.model_synchronizer.stop()
            except Exception as e:
                logger.error(f"停止同步失败: {e}", exc_info=True)

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
