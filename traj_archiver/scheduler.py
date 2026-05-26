"""
轮询调度器 - 固定间隔执行归档任务
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from psycopg_pool import AsyncConnectionPool

from traj_archiver.archiver import archive_details

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ArchiveScheduler:
    """归档轮询调度器，按固定间隔执行归档任务，失败后 5 分钟重试"""

    def __init__(
        self,
        pool: AsyncConnectionPool,
        storage,
        retention_days: int,
        poll_interval: int = 3600,
        local_temp_path: str = "/tmp/archives",
        upload_concurrency: int = 1,
        compress: bool = True,
    ):
        self.pool = pool
        self.storage = storage
        self.retention_days = retention_days
        self.poll_interval = poll_interval
        self.local_temp_path = local_temp_path
        self.upload_concurrency = upload_concurrency
        self.compress = compress

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
        self._started_at = _utcnow()
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"ArchiveScheduler 已启动 (poll_interval={self.poll_interval}s, retention_days={self.retention_days}d)")

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
        self._last_run = _utcnow()
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
                self._last_run = _utcnow()
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

        result = await archive_details(
            pool=self.pool,
            storage=self.storage,
            local_temp_path=self.local_temp_path,
            retention_days=self.retention_days,
            upload_concurrency=self.upload_concurrency,
            compress=self.compress,
        )

        records_archived = result.get("records_archived", 0)
        self._total_records_archived += records_archived

        logger.info(f"归档完成: {result.get('runs_processed', 0)} runs, "
                     f"{result.get('sessions_archived', 0)} sessions, "
                     f"{records_archived} records")

        for d in result.get("details", []):
            logger.info(f"  run_id={d['run_id']}: {d['sessions']} sessions, {d['records']} records")

        for f in result.get("archive_files", []):
            logger.info(f"  文件: {f}")

        if result.get("errors"):
            for err in result["errors"]:
                logger.warning(f"  错误: {err}")

        logger.info("=" * 50)
        self._last_result = result
        return result
