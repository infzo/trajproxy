"""
轮询调度器 - 固定间隔执行归档任务

替代原有的 cron 定时调度，更简单可靠。
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from psycopg_pool import AsyncConnectionPool

from traj_archiver.archiver import archive_details
from traj_archiver.storage import Storage
from traj_archiver.utils import utcnow

logger = logging.getLogger(__name__)


class ArchiveScheduler:
    """归档轮询调度器

    按固定间隔执行归档任务，失败后自动重试。
    """

    def __init__(
        self,
        pool: AsyncConnectionPool,
        storage: Storage,
        retention_days: int,
        poll_interval: int = 3600,
        local_temp_path: str = "/tmp/archives",
    ):
        self.pool = pool
        self.storage = storage
        self.retention_days = retention_days
        self.poll_interval = poll_interval
        self.local_temp_path = local_temp_path

        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._started_at: Optional[datetime] = None
        self._last_run: Optional[datetime] = None
        self._last_result: Optional[Dict[str, Any]] = None
        self._total_runs: int = 0
        self._total_records_archived: int = 0

    async def start(self):
        if self._running:
            logger.warning("ArchiveScheduler 已经在运行中")
            return

        self._running = True
        self._started_at = utcnow()
        self._task = asyncio.create_task(self._run_loop())
        logger.info("ArchiveScheduler 已启动")
        logger.info(f"  poll_interval: {self.poll_interval}s")
        logger.info(f"  retention_days: {self.retention_days}")
        logger.info(f"  local_temp_path: {self.local_temp_path}")

    async def stop(self):
        if not self._running:
            return

        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("ArchiveScheduler 已停止")

    async def trigger_now(self) -> Dict[str, Any]:
        logger.info("手动触发归档任务...")
        result = await self._execute_archive()
        self._last_run = utcnow()
        self._total_runs += 1
        self._last_result = result
        return result

    def get_status(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "poll_interval": self.poll_interval,
            "retention_days": self.retention_days,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "last_run": self._last_run.isoformat() if self._last_run else None,
            "last_result": self._last_result,
            "total_runs": self._total_runs,
            "total_records_archived": self._total_records_archived,
        }

    async def _run_loop(self):
        while self._running:
            try:
                await self._execute_archive()
                self._last_run = utcnow()
                self._total_runs += 1
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"归档任务失败: {e}", exc_info=True)
                if self._running:
                    logger.info("等待 5 分钟后重试...")
                    try:
                        await asyncio.sleep(300)
                    except asyncio.CancelledError:
                        break
                    continue

            if self._running:
                try:
                    await asyncio.sleep(self.poll_interval)
                except asyncio.CancelledError:
                    break

    async def _execute_archive(self) -> Dict[str, Any]:
        logger.info("=" * 50)
        logger.info("开始执行归档任务...")
        logger.info("=" * 50)

        result = await archive_details(
            pool=self.pool,
            storage=self.storage,
            local_temp_path=self.local_temp_path,
            retention_days=self.retention_days,
        )

        records_archived = result.get("records_archived", 0)
        self._total_records_archived += records_archived

        logger.info("=" * 50)
        logger.info("归档任务完成")
        logger.info(f"  处理分区数: {result.get('partitions_processed', 0)}")
        logger.info(f"  本次归档记录数: {records_archived}")
        logger.info(f"  累计归档记录数: {self._total_records_archived}")
        logger.info(f"  删除分区数: {result.get('partitions_dropped', 0)}")
        if result.get("errors"):
            logger.warning(f"  错误数: {len(result['errors'])}")
        logger.info("=" * 50)

        self._last_result = result
        return result
