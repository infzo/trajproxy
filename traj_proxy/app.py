"""
TrajProxy 主入口文件

加载配置并启动WorkerManager
"""

import sys
import time
import signal
import asyncio
import traceback
import ray
from pathlib import Path
from traj_proxy.workers.manager import WorkerManager
from traj_proxy.utils.logger import get_logger
from traj_proxy.utils.config import load_config, get_config, get_archive_config

# 添加项目根目录到Python路径，确保 Ray Actor 能找到 traj_proxy 模块
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 初始化日志系统
logger = get_logger(__name__)


async def main():
    """
    主函数

    加载配置并启动所有Worker
    """
    logger.info("=" * 50)
    logger.info("TrajProxy Worker Manager 启动中...")
    logger.info("=" * 50)

    # 加载配置
    try:
        config = load_config()
        logger.info(f"配置加载成功")
    except FileNotFoundError as e:
        logger.error(f"错误: {e}\n{traceback.format_exc()}")
        sys.exit(1)

    # 创建管理器
    manager = WorkerManager(config)

    # 启动所有Worker
    await manager.start_workers()

    logger.info("=" * 50)
    logger.info("TrajProxy Worker Manager 已启动")
    logger.info("=" * 50)
    logger.info(f"ProxyWorkers: {len(manager.workers)} 个")
    logger.info("=" * 50)

    # 异步打印状态信息
    status = await manager.get_worker_status()
    logger.info("Worker 状态:")
    for worker in status["workers"]:
        logger.info(f"  - Worker {worker['worker_id']}: {worker['status']} (端口: {worker['port']})")
    logger.info("")

    # 启动归档调度器（如果配置启用）
    archive_scheduler = None
    archive_db_manager = None
    archive_config = get_archive_config()
    if archive_config.get("enabled", False):
        try:
            from traj_proxy.archive.scheduler import ArchiveScheduler
            from traj_proxy.store.database_manager import DatabaseManager
            from traj_proxy.utils.config import get_database_config, get_database_pool_config

            # 创建独立的数据库连接池用于归档
            db_config = get_database_config()
            db_url = db_config.get("url")
            pool_config = get_database_pool_config()

            archive_db_manager = DatabaseManager(db_url, pool_config)
            await archive_db_manager.initialize()

            archive_scheduler = ArchiveScheduler(
                schedule=archive_config.get("schedule", "0 2 * * *"),
                retention_days=archive_config.get("retention_days", 30),
                storage_path=archive_config.get("storage_path", "/data/archives"),
                batch_size=archive_config.get("batch_size", 1000),
                timezone=archive_config.get("timezone", "UTC"),
                pool=archive_db_manager.pool,
            )
            await archive_scheduler.start()
            logger.info("归档调度器已启动")
        except Exception as e:
            logger.error(f"启动归档调度器失败: {e}")
            logger.debug(traceback.format_exc())
    else:
        logger.info("归档调度器未启用（archive.enabled=false）")

    # 设置信号处理
    shutdown_event = asyncio.Event()

    def signal_handler(signum, frame):
        logger.warning(f"接收到信号 {signum}，正在关闭...")
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 等待关闭信号
    try:
        await shutdown_event.wait()
    except KeyboardInterrupt:
        logger.warning("用户中断，正在关闭...")

    # 停止归档调度器
    if archive_scheduler:
        logger.info("正在停止归档调度器...")
        await archive_scheduler.stop()

    # 关闭归档数据库连接池
    if archive_db_manager:
        logger.info("正在关闭归档数据库连接池...")
        await archive_db_manager.close()

    manager.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
