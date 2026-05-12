"""
DatabaseManager - 数据库管理器

负责连接池管理。

注意：数据库表和索引的创建已迁移到 scripts/init_db.py（一次性脚本），
集群部署前请先执行该脚本初始化数据库。
"""

import asyncio
import traceback
from typing import Optional

from psycopg_pool import AsyncConnectionPool

from traj_proxy.exceptions import DatabaseError
from traj_proxy.utils.logger import get_logger

logger = get_logger(__name__)


class DatabaseManager:
    """数据库管理器 - 处理连接池

    使用 PostgreSQL 存储数据，管理三个表：
    - request_metadata: 请求轨迹元数据（长期保留）
    - request_details_active: 请求轨迹详情（近期大字段）
    - model_registry: 模型配置注册表
    """

    def __init__(self, db_url: str, pool_config: Optional[dict] = None):
        """初始化 DatabaseManager

        Args:
            db_url: 数据库连接 URL（如 postgresql://user:pass@host:port/dbname）
            pool_config: 连接池配置，包含 min_size, max_size, timeout
        """
        self.db_url = db_url
        self.pool: Optional[AsyncConnectionPool] = None
        # 使用传入的配置或默认值
        self.pool_config = pool_config or {
            "min_size": 2,
            "max_size": 20,
            "timeout": 30
        }
        # 连接池监控
        self._monitor_task: Optional[asyncio.Task] = None
        self._peak_used: int = 0
        self._peak_usage_pct: float = 0.0

    async def initialize(self):
        """初始化连接池

        注意：不再自动创建数据库表，请确保部署前已执行 scripts/init_db.py
        """
        logger.info("DatabaseManager: 开始初始化连接池")
        try:
            self.pool = AsyncConnectionPool(
                conninfo=self.db_url,
                min_size=self.pool_config["min_size"],
                max_size=self.pool_config["max_size"],
                timeout=self.pool_config["timeout"]
            )
            # 显式打开连接池
            logger.info("DatabaseManager: 正在打开连接池...")
            await self.pool.open()
            logger.info("DatabaseManager: 连接池已打开，初始化完成")

            # 启动连接池监控任务
            self._monitor_task = asyncio.create_task(self._monitor_pool())
        except Exception as e:
            logger.error(f"DatabaseManager: 初始化失败: {e}\n{traceback.format_exc()}")
            raise

    async def _monitor_pool(self, interval: int = 30):
        """周期性监控进程内连接池使用情况

        每 interval 秒采样一次，记录当前使用量和峰值。
        使用率超过 80% 时打印 WARNING。

        Args:
            interval: 监控间隔（秒），默认 30
        """
        while True:
            try:
                if not self.pool or self.pool.closed:
                    break

                stats = self.pool.get_stats()
                pool_max = stats.get("pool_max", 0)
                pool_size = stats.get("pool_size", 0)
                pool_available = stats.get("pool_available", 0)
                used = pool_size - pool_available
                usage_pct = (used / pool_max * 100) if pool_max > 0 else 0

                # 更新峰值
                if used > self._peak_used:
                    self._peak_used = used
                if usage_pct > self._peak_usage_pct:
                    self._peak_usage_pct = usage_pct

                if usage_pct >= 80:
                    logger.warning(
                        f"连接池使用率过高: "
                        f"used={used}, size={pool_size}, max={pool_max}, "
                        f"available={pool_available}, "
                        f"usage={usage_pct:.1f}%, "
                        f"peak_used={self._peak_used}, "
                        f"peak_usage={self._peak_usage_pct:.1f}%, "
                        f"requests_queued={stats.get('requests_queued', 0)}, "
                        f"requests_errors={stats.get('requests_errors', 0)}"
                    )
                else:
                    logger.info(
                        f"连接池状态: "
                        f"used={used}, size={pool_size}, max={pool_max}, "
                        f"available={pool_available}, "
                        f"usage={usage_pct:.1f}%, "
                        f"peak_used={self._peak_used}, "
                        f"peak_usage={self._peak_usage_pct:.1f}%"
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"连接池监控异常: {e}")

            await asyncio.sleep(interval)

    async def close(self):
        """关闭连接池"""
        # 停止监控任务
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        if self.pool:
            await self.pool.close()
