"""
Ray进程管理器

使用Ray创建和管理Worker进程。

部署契约（重要）：
    Ray Actor 配置 max_restarts=3 + max_task_retries=2 提供有限自动恢复。
    当 max_restarts 耗尽后，Actor handle 失效，Worker 进入永久死亡状态，
    WorkerManager 无法自行恢复。此时必须依赖外部监控（k8s Pod liveness probe /
    Docker restart policy / supervisord / systemd）重建整个 WorkerManager 进程。
    详见 docs/design/architecture.md §十五 "Worker 崩溃恢复与部署契约"。
"""

import ray
import asyncio
import os
import traceback
from typing import List, Dict, Optional, Any
from traj_proxy.workers.worker import ProxyWorker
from traj_proxy.utils.logger import get_logger

# 配置日志
logger = get_logger(__name__)


@ray.remote(num_cpus=1)
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
        self._initialized = False  # 标记Worker是否已初始化（用于崩溃恢复检测）
        self._server_thread = None  # uvicorn服务线程引用，用于幂等性检查

    async def initialize(self):
        """
        初始化Worker实例并在后台运行uvicorn服务器

        需要在Actor启动后显式调用。

        **幂等性保证**：
        - 如果已初始化成功（_initialized=True），直接返回
        - 如果 uvicorn 线程已存在且存活，说明端口已被占用，不能重复启动，
          此时抛出 RuntimeError 提示调用方不要重试
        """
        # BUG-1 修复：幂等性检查 — 已初始化成功则直接返回
        if self._initialized:
            return

        # BUG-1 修复：如果 uvicorn 线程已存在且仍在运行，说明端口已被占用
        # 此时后续步骤（如 DB 连接）虽未完成，但端口冲突意味着重试必定失败
        if self._server_thread is not None and self._server_thread.is_alive():
            raise RuntimeError(
                f"RemoteWorker {self.worker_id}: uvicorn thread already running on port {self.port}. "
                f"Cannot re-initialize — retrying would cause EADDRINUSE. "
                f"This indicates a partial initialization failure (e.g., DB connection timeout). "
                f"The worker process must be fully restarted to recover."
            )

        # 为当前Worker创建独立的日志记录器
        worker_name = f"{self.worker_class.__name__}-{self.worker_id}"
        self.worker_logger = get_logger(__name__, worker_id=worker_name)

        # 如果有db_url参数，则传递给Worker
        if self.db_url:
            self.worker = self.worker_class(self.worker_id, self.port, self.db_url)
        else:
            self.worker = self.worker_class(self.worker_id, self.port)

        self.worker_logger.info(f"RemoteWorker initialized: {self.worker_class.__name__}-{self.worker_id} on port {self.port}")

        # BUG-1 修复：先启动 uvicorn 线程并保存引用，
        # 即使后续步骤失败，线程引用仍被记录以便幂等性检查
        import uvicorn
        from threading import Thread

        def run_server():
            uvicorn.run(self.worker.app, host="0.0.0.0", port=self.port, log_level="info", timeout_keep_alive=65)

        self._server_thread = Thread(target=run_server, daemon=True)
        self._server_thread.start()
        self.worker_logger.info(f"Started uvicorn server for {self.worker_class.__name__}-{self.worker_id} on port {self.port}")

        try:
            # 如果Worker有initialize方法，则调用（DB连接等可能在此失败）
            if hasattr(self.worker, 'initialize'):
                await self.worker.initialize()

            self._initialized = True  # 全部初始化步骤完成
        except Exception as e:
            self.worker_logger.error(
                f"RemoteWorker {self.worker_id} post-uvicorn initialization failed: {e}\n"
                f"{traceback.format_exc()}\n"
                f"NOTE: uvicorn thread is still running on port {self.port}. "
                f"Re-initialization will fail with EADDRINUSE. "
                f"Worker process must be fully killed and restarted."
            )
            raise

    def is_initialized(self) -> bool:
        """
        检查Worker是否已完成初始化

        Ray Actor 崩溃后重启时，__init__ 会被重新执行（_initialized=False），
        但 initialize() 不会被自动重调。外部监控可通过此方法检测是否需要
        重新初始化。

        返回:
            True 表示已初始化，False 表示需要重新调用 initialize()
        """
        return self._initialized

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
        运行Worker（已废弃）

        此方法存在两个严重问题：
        1. self.initialize() 是 async def，在同步方法中直接调用会产生未 await 的 coroutine，
           实际上不会执行任何初始化逻辑。
        2. uvicorn 服务器已在 initialize() 中以线程方式启动，无需再阻塞调用 worker.run()。

        项目中无任何调用方使用此方法，属于死代码。
        TODO: 在确认无外部依赖后彻底移除此方法。
        """
        import warnings
        warnings.warn(
            "RemoteWorker.run() is deprecated: initialize() already starts uvicorn in a thread. "
            "Direct call to async initialize() from sync context creates unawaited coroutine. "
            "Do not use this method.",
            DeprecationWarning,
            stacklevel=2,
        )


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

        # 优先使用环境变量，其次使用配置文件，最后使用默认值
        working_dir = os.getenv("RAY_WORKING_DIR") or ray_config.get("working_dir", "/app/traj_proxy")
        pythonpath = os.getenv("RAY_PYTHONPATH") or ray_config.get("pythonpath", "/app/traj_proxy")

        # 初始化Ray - 增加稳定性配置
        # working_dir 限定在代码目录（/app/traj_proxy），models/ 等大目录不在其下，无需排除
        ray.init(
            ignore_reinit_error=True,
            num_cpus=num_cpus,
            runtime_env={
                "working_dir": working_dir,
                "env_vars": {
                    "PYTHONPATH": pythonpath
                },
                "excludes": [
                    "*.pyc",
                    "__pycache__/",
                    ".git/"
                ]
            },
            log_to_driver=True,
            _system_config={
                "automatic_object_spilling_enabled": False,
                "worker_lease_timeout_milliseconds": 10000,
            }
        )
        logger.info(f"Ray initialized successfully: working_dir={working_dir}, pythonpath={pythonpath}")

        self.workers: List[ray.ActorHandle] = []
        self._worker_meta: Dict[int, dict] = {}  # Worker创建元数据，用于崩溃恢复
        self._health_check_interval: int = 30  # 健康检查间隔（秒）
        self._health_task: Optional[asyncio.Task] = None  # 健康检查后台任务
        self.config = config

    async def start_workers(self):
        """
        启动所有Worker

        创建并初始化所有配置的ProxyWorker Actor
        """
        logger.info("Starting workers...")

        # 获取数据库URL
        db_config = self.config.get("database", {})
        db_url = db_config.get("url")

        # 启动 ProxyWorkers
        proxy_config = self.config.get("proxy_workers", {})
        worker_count = proxy_config.get("count", 1)
        base_port = proxy_config.get("base_port", 12300)

        logger.info(f"Starting {worker_count} ProxyWorkers from port {base_port}")

        async def _init_worker(i: int):
            port = base_port + i

            # BUG-2 修复：放弃命名 Actor，改用 Ray UUID + 本地缓存 handle。
            # 原因：命名 Actor 在 WorkerManager 非优雅重启（OOM / kill -9）后，
            # 旧 Actor 名称仍在 Ray 集群中，新进程创建同名 Actor 会抛
            # ValueError: name is already used，导致所有 Worker 创建失败。
            #
            # 放弃命名 Actor 的可行性分析：
            # 1. Ray max_restarts=-1 配置下，Actor OOM/panic 后 Ray 会无限自动重启，
            #    并保持原有 handle 有效（__init__ 重执行，但 initialize() 不会自动调用）。
            # 2. _health_monitor_loop 已能检测 _initialized=False 并重新调用 initialize()，
            #    因此不需要命名 Actor 来"找回" handle。
            #
            # 结论：命名 Actor 唯一的价值是 _recover_dead_worker 中 ray.get_actor()，
            # 但在 max_restarts=-1 机制下 handle 不会失效，命名 Actor 反而引入了
            # 非优雅重启后的冲突风险。因此移除 name 参数。
            max_restarts = proxy_config.get("max_restarts", -1)
            max_task_retries = proxy_config.get("max_task_retries", 2)
            worker = RemoteWorker.options(
                max_restarts=max_restarts,
                max_task_retries=max_task_retries,
            ).remote(ProxyWorker, i, port, db_url)

            # 重试 initialize 调用，防止首次初始化偶发失败
            # BUG-1 修复：如果 initialize() 因 uvicorn 线程已存在而抛 RuntimeError，
            # 说明端口已被占用，继续重试必定失败（EADDRINUSE），此时应立即终止重试。
            max_init_retries = 3
            for attempt in range(max_init_retries):
                try:
                    await worker.initialize.remote()
                    logger.info(f"ProxyWorker {i} started on port {port}")
                    break
                except RuntimeError as re_err:
                    # uvicorn 线程冲突 — 重试无意义，立即终止
                    logger.error(
                        f"ProxyWorker {i} initialize failed with RuntimeError "
                        f"(uvicorn thread conflict, retrying will cause EADDRINUSE): {re_err}"
                    )
                    raise
                except Exception as e:
                    logger.warning(
                        f"ProxyWorker {i} initialize failed "
                        f"(attempt {attempt + 1}/{max_init_retries}): {e}"
                    )
                    if attempt == max_init_retries - 1:
                        logger.error(
                            f"ProxyWorker {i} initialize failed after "
                            f"{max_init_retries} attempts: {e}"
                        )
                        raise

            # 存储创建元数据，用于崩溃恢复时重新初始化（不再存储 actor_name）
            self._worker_meta[i] = {
                "worker_id": i,
                "port": port,
                "db_url": db_url,
            }
            return worker

        self.workers = await asyncio.gather(*[_init_worker(i) for i in range(worker_count)])

        # 启动后台健康监控，定期检测 Worker 存活状态并自动恢复
        self._health_task = asyncio.create_task(self._health_monitor_loop())
        logger.info(f"Health monitor started (interval={self._health_check_interval}s)")

        logger.info(f"All workers started: {len(self.workers)} ProxyWorkers")

    async def get_worker_status(self) -> Dict:
        """
        获取所有Worker状态，包含存活检测

        对每个 Worker 尝试调用 is_initialized() 和 get_info()，
        区分三种状态：healthy / recovering / dead。
        已死亡的 Worker 不会阻塞整体状态查询。

        返回:
            包含所有Worker状态的字典
        """
        status_list = []
        for i, worker in enumerate(self.workers):
            try:
                # 先检测 Actor 是否存活且已初始化（增加 timeout 防止无限阻塞）
                is_init = await asyncio.to_thread(
                    ray.get, worker.is_initialized.remote(), timeout=5
                )
                if is_init:
                    # Actor 存活且已初始化，获取详细信息
                    info = await asyncio.to_thread(
                        ray.get, worker.get_info.remote(), timeout=5
                    )
                    status_list.append(info)
                else:
                    # Actor 已重启但未重新初始化，标记为 recovering
                    meta = self._worker_meta.get(i, {})
                    status_list.append({
                        "worker_id": meta.get("worker_id", i),
                        "status": "recovering",
                        "port": meta.get("port", 0),
                    })
                    logger.warning(f"Worker {i} detected as recovering (restarted but not initialized)")
            except Exception as e:
                # Actor 已死亡或不可达，标记为 dead
                meta = self._worker_meta.get(i, {})
                status_list.append({
                    "worker_id": meta.get("worker_id", i),
                    "status": "dead",
                    "port": meta.get("port", 0),
                    "error": str(e),
                })
                logger.error(f"Worker {i} detected as dead: {e}")

        return {
            "workers": status_list,
            "total": len(self.workers),
            "healthy": sum(1 for s in status_list if s.get("status") == "healthy"),
            "recovering": sum(1 for s in status_list if s.get("status") == "recovering"),
            "dead": sum(1 for s in status_list if s.get("status") == "dead"),
        }

    async def _health_monitor_loop(self) -> None:
        """
        健康监控循环：定期检测所有 Worker 存活状态并自动恢复

        当 Ray Actor 因 max_restarts 配置自动重启后，__init__ 会重新执行，
        但 initialize() 不会自动重调。本循环检测 _initialized=False 的 Actor
        并重新调用 initialize()，恢复 uvicorn 服务。
        """
        while True:
            try:
                await asyncio.sleep(self._health_check_interval)
                await self._check_and_recover_workers()
            except asyncio.CancelledError:
                logger.info("Health monitor task cancelled, exiting loop")
                break
            except Exception as e:
                logger.error(f"Health monitor loop error: {e}\n{traceback.format_exc()}")

    async def _check_and_recover_workers(self) -> None:
        """
        检查所有 Worker 健康状态，自动恢复需要重新初始化的 Worker

        处理两种场景：
        1. Actor 已被 Ray 自动重启但未初始化（_initialized=False）
        2. Actor 引用失效（max_restarts 耗尽，需外部干预）

        BUG-4 修复：ray.get 调用增加 timeout=5 参数，
        防止 Actor 无响应时 asyncio.to_thread 永久阻塞产生孤儿线程。
        """
        for i, worker in enumerate(self.workers):
            try:
                is_init = await asyncio.to_thread(
                    ray.get, worker.is_initialized.remote(), timeout=5
                )
                if not is_init:
                    logger.warning(f"Worker {i} needs re-initialization (possibly restarted by Ray)")
                    await self._reinitialize_worker(i, worker)
            except ray.exceptions.GetTimeoutError:
                logger.warning(
                    f"Worker {i} health check timed out (5s) — "
                    f"Actor may be overloaded or unresponsive"
                )
            except Exception as e:
                logger.error(f"Worker {i} health check failed: {e}")
                # Actor 引用失效（max_restarts 耗尽）
                await self._recover_dead_worker(i)

    async def _reinitialize_worker(self, worker_id: int, worker: Any) -> None:
        """
        重新初始化一个已重启但未初始化的 Worker

        Ray 重启 Actor 后 __init__ 会重置 _initialized=False，
        此方法重新调用 initialize() 以恢复 uvicorn 服务和业务组件。

        异常处理策略：
        - RayActorError: Actor 已死，等待下一轮健康检查触发 _recover_dead_worker
        - RuntimeError: uvicorn daemon 线程残留占用端口，re-init 不可能成功；
          强杀当前 Actor，让 Ray 用 max_restarts 配额重启新进程，释放端口
        - 其他 Exception: 记录日志，等待下一轮健康检查重试

        参数:
            worker_id: Worker 编号
            worker: Ray Actor handle
        """
        try:
            await worker.initialize.remote()
            logger.info(f"Worker {worker_id} re-initialized successfully after crash recovery")
        except ray.exceptions.RayActorError:
            # Actor 已死, 等下一轮健康检查触发 _recover_dead_worker
            logger.warning(f"Worker {worker_id} actor handle already dead, will be recovered in next cycle")
        except RuntimeError as re_err:
            # uvicorn daemon 残留占用端口, re-init 不可能成功
            # 强杀当前 Actor, 让 Ray 用 max_restarts 配额重启新进程
            # Ray Actor 重启一定在新 OS process, daemon 线程随旧进程死亡自动退出、释放端口
            # 新进程可以绑定同一端口, 因此 no_restart=False 是正确选择
            logger.error(
                f"Worker {worker_id} re-init RuntimeError (uvicorn zombie state): {re_err}. "
                f"Killing actor to force Ray restart with fresh process."
            )
            ray.kill(worker, no_restart=False)  # no_restart=False → Ray 会重启 Actor, 新进程释放端口
        except Exception as e:
            logger.error(f"Worker {worker_id} re-initialization failed: {e}\n{traceback.format_exc()}")

    async def _recover_dead_worker(self, worker_id: int) -> None:
        """
        恢复已死亡的 Worker Actor

        当 Actor handle 失效时（max_restarts 耗尽），无法通过命名 Actor 恢复，
        因为已放弃命名 Actor 机制（避免非优雅重启后的名称冲突）。

        此方法记录不可恢复状态，等待外部监控重建整个 WorkerManager 进程。
        部署契约要求：生产环境必须配置外部监控（k8s liveness probe / systemd
        restart 等），详见 docs/design/architecture.md §十五。

        参数:
            worker_id: Worker 编号
        """
        meta = self._worker_meta.get(worker_id, {})
        logger.error(
            f"Worker {worker_id} (port={meta.get('port', 0)}) actor handle失效，"
            f"max_restarts 已耗尽。无法自动恢复 — 需要重启 WorkerManager 进程。"
        )

    def shutdown(self):
        """
        关闭所有Worker和Ray

        先取消健康监控后台任务，再关闭 Ray 运行时，清理所有资源
        """
        logger.warning("Shutting down WorkerManager...")
        # 取消健康监控后台任务
        if self._health_task and not self._health_task.done():
            self._health_task.cancel()
            logger.info("Health monitor task cancelled")
        ray.shutdown()
        logger.info("Ray shutdown complete")
