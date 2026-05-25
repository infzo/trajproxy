"""
traj_archiver 入口

用法: python -m traj_archiver
"""

import asyncio
import logging
import signal
import sys

from psycopg_pool import AsyncConnectionPool

from traj_archiver.config import (
    get_archive_config,
    get_database_pool_config,
    get_database_url,
    get_s3_config,
)
from traj_archiver.s3_storage import S3Storage
from traj_archiver.scheduler import ArchiveScheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main():
    logger.info("=" * 50)
    logger.info("TrajArchiver 启动中...")
    logger.info("=" * 50)

    # 加载配置
    db_url = get_database_url()
    if not db_url:
        logger.error("错误: 请设置 DATABASE_URL 环境变量或配置 database.url")
        sys.exit(1)

    archive_config = get_archive_config()
    s3_config = get_s3_config()
    pool_config = get_database_pool_config()

    # 创建数据库连接池
    pool = AsyncConnectionPool(
        conninfo=db_url,
        min_size=pool_config["min_size"],
        max_size=pool_config["max_size"],
        timeout=pool_config["timeout"],
    )
    await pool.open()
    logger.info("数据库连接池已创建")

    # 创建 S3 存储
    s3_storage = S3Storage(
        bucket=s3_config.get("bucket", ""),
        prefix=s3_config.get("prefix", ""),
        endpoint_url=s3_config.get("endpoint_url"),
    )

    # 创建并启动调度器
    scheduler = ArchiveScheduler(
        pool=pool,
        s3_storage=s3_storage,
        retention_days=archive_config.get("retention_days", 30),
        poll_interval=archive_config.get("poll_interval", 3600),
        local_temp_path=archive_config.get("local_temp_path", "/tmp/archives"),
    )
    await scheduler.start()

    logger.info("=" * 50)
    logger.info("TrajArchiver 已启动")
    logger.info("=" * 50)

    # 信号处理
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

    # 优雅退出
    await scheduler.stop()
    await pool.close()
    logger.info("TrajArchiver 已停止")


if __name__ == "__main__":
    asyncio.run(main())
