"""
归档调度器 - 应用内定时归档任务

使用 asyncio 实现定时任务调度，不依赖系统 cron。
"""

import asyncio
from datetime import datetime
from typing import Optional, Dict, Any

from traj_proxy.utils.logger import get_logger

logger = get_logger(__name__)

# 延迟导入，避免循环依赖
def _get_croniter():
    """延迟导入 croniter"""
    from croniter import croniter
    return croniter


class ArchiveScheduler:
    """归档定时调度器

    根据配置的 cron 表达式定时触发归档任务。

    配置示例:
        schedule: "0 2 * * *"  # 每天凌晨 2 点执行
        timezone: "Asia/Shanghai"

    使用:
        scheduler = ArchiveScheduler(
            schedule="0 2 * * *",
            retention_days=30,
            storage_path="/data/archives",
            pool=db_pool,
        )
        await scheduler.start()
        # ...
        await scheduler.stop()
    """

    # 心跳状态日志间隔（秒）
    HEARTBEAT_INTERVAL = 3600  # 每小时输出一次状态

    def __init__(
        self,
        schedule: str,
        retention_days: int,
        storage_path: str,
        batch_size: int = 1000,
        timezone: str = "UTC",
        pool=None,
    ):
        """初始化归档调度器

        Args:
            schedule: cron 表达式，如 "0 2 * * *" 表示每天凌晨 2 点
            retention_days: 活跃数据保留天数
            storage_path: 归档文件存储路径
            batch_size: 每批处理的记录数
            timezone: 时区配置
            pool: 数据库连接池
        """
        self.schedule = schedule
        self.retention_days = retention_days
        self.storage_path = storage_path
        self.batch_size = batch_size
        self.timezone = timezone
        self.pool = pool

        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._croniter = None
        self._last_run: Optional[datetime] = None
        self._last_result: Optional[Dict[str, Any]] = None
        self._started_at: Optional[datetime] = None
        self._total_runs: int = 0
        self._total_records_archived: int = 0
        self._last_heartbeat: Optional[datetime] = None

    async def start(self):
        """启动调度器"""
        if self._running:
            logger.warning("ArchiveScheduler 已经在运行中")
            return

        self._running = True
        self._started_at = datetime.now()
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"ArchiveScheduler 已启动")
        logger.info(f"  schedule: {self.schedule}")
        logger.info(f"  retention_days: {self.retention_days}")
        logger.info(f"  storage_path: {self.storage_path}")
        logger.info(f"  timezone: {self.timezone}")

    async def stop(self):
        """停止调度器"""
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
        """立即触发一次归档（手动触发）

        Returns:
            归档结果统计
        """
        logger.info("手动触发归档任务...")
        result = await self._execute_archive()
        self._last_run = datetime.now()
        self._total_runs += 1
        self._last_result = result
        return result

    def get_status(self) -> Dict[str, Any]:
        """获取调度器状态

        Returns:
            状态信息字典
        """
        return {
            "running": self._running,
            "schedule": self.schedule,
            "retention_days": self.retention_days,
            "storage_path": self.storage_path,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "last_run": self._last_run.isoformat() if self._last_run else None,
            "last_result": self._last_result,
            "total_runs": self._total_runs,
            "total_records_archived": self._total_records_archived,
        }

    async def _run_loop(self):
        """主循环：计算下次执行时间并等待"""
        try:
            croniter = _get_croniter()
            self._croniter = croniter(self.schedule)
        except Exception as e:
            logger.error(f"无法解析 cron 表达式 '{self.schedule}': {e}")
            return

        while self._running:
            try:
                # 计算下次执行时间
                next_run = self._croniter.get_next(datetime)
                now = datetime.now()
                wait_seconds = (next_run - now).total_seconds()

                if wait_seconds > 0:
                    logger.info(f"下次归档时间: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")

                    # 分段等待，定期输出心跳状态
                    remaining = wait_seconds
                    while remaining > 0 and self._running:
                        sleep_time = min(remaining, self.HEARTBEAT_INTERVAL)
                        await asyncio.sleep(sleep_time)
                        remaining -= sleep_time

                        # 输出心跳状态日志
                        if remaining > 0 and self._running:
                            self._log_heartbeat(next_run, remaining)

                if self._running:
                    await self._execute_archive()
                    self._last_run = datetime.now()
                    self._total_runs += 1

            except asyncio.CancelledError:
                logger.debug("调度器循环被取消")
                break
            except Exception as e:
                logger.error(f"ArchiveScheduler 错误: {e}", exc_info=True)
                # 出错后等待 5 分钟再重试
                if self._running:
                    logger.info("等待 5 分钟后重试...")
                    await asyncio.sleep(300)

    def _log_heartbeat(self, next_run: datetime, remaining_seconds: float):
        """输出心跳状态日志

        Args:
            next_run: 下次执行时间
            remaining_seconds: 剩余等待秒数
        """
        hours = int(remaining_seconds // 3600)
        minutes = int((remaining_seconds % 3600) // 60)

        logger.info(
            f"[ArchiveScheduler 心跳] 运行中 | "
            f"下次执行: {next_run.strftime('%Y-%m-%d %H:%M:%S')} | "
            f"剩余: {hours}h{minutes}m | "
            f"已运行次数: {self._total_runs} | "
            f"已归档记录: {self._total_records_archived}"
        )
        self._last_heartbeat = datetime.now()

    async def _execute_archive(self) -> Dict[str, Any]:
        """执行归档任务

        Returns:
            归档结果统计
        """
        from traj_proxy.archive.archiver import archive_details

        logger.info("=" * 50)
        logger.info("开始执行归档任务...")
        logger.info("=" * 50)

        try:
            result = await archive_details(
                pool=self.pool,
                archive_dir=self.storage_path,
                retention_days=self.retention_days,
                batch_size=self.batch_size,
                dry_run=False,
            )

            # 更新统计信息
            records_archived = result.get('records_archived', 0)
            self._total_records_archived += records_archived

            logger.info("=" * 50)
            logger.info("归档任务完成")
            logger.info(f"  处理分区数: {result.get('partitions_processed', 0)}")
            logger.info(f"  本次归档记录数: {records_archived}")
            logger.info(f"  累计归档记录数: {self._total_records_archived}")
            logger.info(f"  删除分区数: {result.get('partitions_dropped', 0)}")
            if result.get('errors'):
                logger.warning(f"  错误数: {len(result['errors'])}")
            logger.info("=" * 50)

            self._last_result = result
            return result

        except Exception as e:
            logger.error(f"归档任务失败: {e}", exc_info=True)
            raise
