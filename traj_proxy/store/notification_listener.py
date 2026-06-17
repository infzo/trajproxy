"""
NotificationListener - PostgreSQL LISTEN/NOTIFY 连接管理

管理用于 model_registry_changes 频道的专用异步连接。
LISTEN 需要一个持久的独占连接，因此不能使用连接池。
"""

import asyncio
import json
import traceback
from typing import Optional, Callable, Awaitable
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse

import psycopg

from traj_proxy.utils.logger import get_logger

logger = get_logger(__name__)

# NOTIFY 通道名称
CHANNEL = "model_registry_changes"


class NotificationListener:
    """管理一个专用的 PostgreSQL 异步连接，用于 LISTEN/NOTIFY。"""

    def __init__(
        self,
        db_url: str,
        on_notification: Callable[[dict], Awaitable[None]],
        reconnect_delay: float = 5.0,
        max_reconnect_delay: float = 60.0,
    ):
        """初始化 NotificationListener

        Args:
            db_url: 数据库连接 URL
            on_notification: 通知回调函数，接收解析后的 payload 字典
            reconnect_delay: 重连初始延迟（秒）
            max_reconnect_delay: 重连最大延迟（秒）
        """
        self._db_url = db_url
        self._on_notification = on_notification
        self._reconnect_delay = reconnect_delay
        self._max_reconnect_delay = max_reconnect_delay
        self._conn: Optional[psycopg.AsyncConnection] = None
        self._listen_task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self):
        """启动 LISTEN 循环作为后台任务"""
        self._running = True
        self._listen_task = asyncio.create_task(self._listen_loop())
        logger.info(f"NotificationListener 已启动，正在监听频道 '{CHANNEL}'")

    async def stop(self):
        """停止监听器并关闭连接"""
        self._running = False
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        await self._close_connection()
        logger.info("NotificationListener 已停止")

    async def _listen_loop(self):
        """主监听循环，带自动重连"""
        delay = self._reconnect_delay
        while self._running:
            try:
                # 创建独立的异步连接（autocommit 模式，LISTEN 不需要事务块）
                # 启用 TCP keepalive 防止云环境防火墙/NAT 静默丢弃空闲连接
                keepalive_params = {
                    "keepalives": "1",
                    "keepalives_idle": "30",
                    "keepalives_interval": "10",
                    "keepalives_count": "3",
                }
                parsed = urlparse(self._db_url)
                existing = parse_qs(parsed.query)
                existing.update(keepalive_params)
                conninfo = urlunparse(parsed._replace(query=urlencode(existing, doseq=True)))
                self._conn = await psycopg.AsyncConnection.connect(
                    conninfo, autocommit=True
                )
                await self._conn.execute(f"LISTEN {CHANNEL}")
                logger.info(f"LISTEN 已激活，通道: {CHANNEL}")
                delay = self._reconnect_delay  # 连接成功后重置重试延迟

                # psycopg3 异步通知迭代器
                async for notify in self._conn.notifies():
                    if not self._running:
                        break
                    await self._handle_notification(notify)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"LISTEN 连接错误: {e}\n{traceback.format_exc()}")
                await self._close_connection()
                if self._running:
                    logger.warning(f"在 {delay:.1f} 秒后重连 LISTEN...")
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, self._max_reconnect_delay)

    async def _handle_notification(self, notify):
        """解析并分发单个通知"""
        try:
            payload = json.loads(notify.payload)
            logger.info(
                f"收到通知: action={payload.get('action')}, "
                f"run_id={payload.get('run_id')}, "
                f"model_name={payload.get('model_name')}"
            )
            await self._on_notification(payload)
        except json.JSONDecodeError as e:
            logger.error(f"无效的通知 payload: {notify.payload}, 错误: {e}")
        except Exception as e:
            logger.error(f"处理通知时出错: {e}\n{traceback.format_exc()}")

    async def _close_connection(self):
        """安全关闭 LISTEN 连接"""
        if self._conn:
            try:
                await self._conn.close()
            except Exception:
                pass
            self._conn = None
