"""
R3RefRepository - route_experts 大字段引用表 CRUD

功能边界：
    - 仅负责 r3_blob_refs 表的增删改查，不涉及 blob 存储本身（BlobStorage 的职责）
    - 不实现清理任务（fetch_expired_for_cleanup 仅返回过期候选列表，不执行删除）
    - 不调用 mark_consumed（本期预留，状态机完整但消费标记不触发）

对外接口：
    - insert_ref: 幂等插入引用行（status='uploading'），供 OffloadingRepository
      在抽取 route_experts 后、fire-and-forget 上传前调用；需传入 session_id
      作为多租户授权键
    - get_for_fetch: 按 (session_id, request_id) 复合查询引用行，供 fallback
      端点判定 404 / 202 / 200 / 410；多租户授权：仅当 session_id 与
      request_id 同属一行时返回数据，防止跨租户读取（P0-#3 修复）
    - mark_ready: 上传完成后将状态从 uploading → ready
    - mark_consumed: 预留，本期不调用
    - fetch_expired_for_cleanup: 预留，返回过期候选，供未来清理任务用

依赖关系：
    - psycopg_pool.AsyncConnectionPool（由 DatabaseManager 注入，与
      RequestRepository 共享同一连接池）
    - traj_proxy.exceptions.DatabaseError（统一错误类型）
    - traj_proxy.utils.logger（统一日志，禁止 print）
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from traj_proxy.exceptions import DatabaseError
from traj_proxy.utils.logger import get_logger

logger = get_logger(__name__)


class R3RefRepository:
    """route_experts 引用表仓库

    严格对齐 RequestRepository 的 psycopg3 + AsyncConnectionPool + raw SQL +
    dict_row + async with self.pool.connection() + async with conn.transaction()
    模式。所有 SQL 均使用参数化占位符（%s），禁止字符串拼接。
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        """初始化 R3RefRepository。

        Args:
            pool: PostgreSQL 异步连接池（由 DatabaseManager 注入，
                  与 RequestRepository 共享）。
        """
        self.pool = pool

    async def insert_ref(
        self,
        request_id: str,
        session_id: str,
        blob_key: str,
        backend: str,
        size_bytes: int,
        expires_at: datetime,
    ) -> None:
        """幂等插入引用行（status 由表默认置为 'uploading'）。

        若 request_id 已存在则不做任何操作（ON CONFLICT DO NOTHING），
        保证 OffloadingRepository 重试或并发场景下的幂等性。

        session_id 为多租户授权键，NOT NULL：调用方必须传入非空值。
        若 OffloadingRepository.context.session_id 为 None，调用方需用
        占位符（_UNKNOWN_SEGMENT）传入，本方法不做兜底（强制上游显式处理）。

        失败模式（设计文档 十一）：insert_ref 失败 → 调用方
        OffloadingRepository 捕获异常后退化原行为（不 strip route_experts，
        PG 存大字段）。

        Args:
            request_id: 请求 ID（主键）。
            session_id: 会话 ID，多租户授权键，禁止为空。
            blob_key: blob 存储键，格式
                      route_experts/{run_id}/{session_id}/{request_id}.json。
            backend: 存储后端，"csb" 或 "local"。
            size_bytes: 待上传数据字节数（上传前为预估值）。
            expires_at: 过期时间（created_at + ttl），供未来清理任务用。

        Raises:
            DatabaseError: 插入失败时抛出。
        """
        try:
            async with self.pool.connection() as conn:
                async with conn.transaction():
                    await conn.execute(
                        """
                        INSERT INTO public.r3_blob_refs
                            (request_id, session_id, blob_key, backend,
                             size_bytes, expires_at)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (request_id) DO NOTHING
                        """,
                        (request_id, session_id, blob_key, backend,
                         size_bytes, expires_at),
                    )
        except Exception as e:
            raise DatabaseError(
                f"插入 r3_blob_refs 失败: {str(e)}"
            )

    async def get_for_fetch(
        self, session_id: str, request_id: str
    ) -> Optional[Dict[str, Any]]:
        """按 (session_id, request_id) 复合查询引用行。

        多租户授权（P0-#3 修复）：仅当 session_id 与 request_id 同属一行
        时返回数据，任意客户端凭他人 request_id 无法跨租户读取 route_experts。

        不使用 FOR UPDATE（P0-#4 修复）：
            - fallback 端点读多写少，状态判定无需行级锁
            - 流式回拉期间无 DB 交互，锁无意义
            - 原实现 FOR UPDATE 未包 conn.transaction()，锁在 cursor 退出
              时即释放，设计意图落空且增加锁等待/死锁风险

        返回值映射（设计文档 九.3）：
            - None               → 404（未卸载或字段缺失，或跨租户请求）
            - status='uploading' → 202 Retry-After: 30
            - status='ready'     → 200 流式返回
            - status='consumed'  → 410 Gone（本期默认不标记）

        Args:
            session_id: 会话 ID，多租户授权键。
            request_id: 请求 ID。

        Returns:
            引用行字典（含 request_id / session_id / blob_key / backend /
            status / size_bytes / created_at / ready_at / consumed_at /
            expires_at）；不存在或跨租户时返回 None。

        Raises:
            DatabaseError: 查询失败时抛出。
        """
        try:
            async with self.pool.connection() as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(
                        """
                        SELECT request_id, session_id, blob_key, backend,
                               status, size_bytes, created_at, ready_at,
                               consumed_at, expires_at
                        FROM public.r3_blob_refs
                        WHERE session_id = %s AND request_id = %s
                        """,
                        (session_id, request_id),
                    )
                    row = await cur.fetchone()
                    return dict(row) if row is not None else None
        except Exception as e:
            raise DatabaseError(
                f"查询 r3_blob_refs 失败: {str(e)}"
            )

    async def mark_ready(self, request_id: str) -> None:
        """上传完成后将状态从 uploading → ready。

        条件更新（WHERE status = 'uploading'）保证幂等：若状态已变更
        （并发 mark_ready 或已被清理），UPDATE 影响 0 行，记 WARNING。

        失败模式（设计文档 十一）：上传成功但 mark_ready 失败 → ref 留
        uploading，未来清理任务扫描超时 uploading 行后修复状态。

        Args:
            request_id: 请求 ID。

        Raises:
            DatabaseError: 更新失败时抛出。
        """
        try:
            async with self.pool.connection() as conn:
                async with conn.transaction():
                    async with conn.cursor() as cur:
                        await cur.execute(
                            """
                            UPDATE public.r3_blob_refs
                            SET status = 'ready', ready_at = NOW()
                            WHERE request_id = %s AND status = 'uploading'
                            """,
                            (request_id,),
                        )
                        if cur.rowcount == 0:
                            logger.warning(
                                f"mark_ready 未匹配行 "
                                f"(request_id={request_id}): "
                                f"状态可能已变更或行不存在"
                            )
        except Exception as e:
            raise DatabaseError(
                f"mark_ready 失败: {str(e)}"
            )

    async def mark_consumed(self, request_id: str) -> None:
        """预留：消费标记，将状态从 ready → consumed。

        本期不调用（设计文档 二.2 约束：本期不标记 consumed，避免阻断
        二次回拉且无清理收益）。方法已实现以保证状态机完整，供未来
        consume-on-read 扩展（设计文档 十四.3）。

        条件更新（WHERE status = 'ready'）保证幂等：若状态已不是 ready，
        UPDATE 影响 0 行，记 WARNING。

        Args:
            request_id: 请求 ID。

        Raises:
            DatabaseError: 更新失败时抛出。
        """
        try:
            async with self.pool.connection() as conn:
                async with conn.transaction():
                    async with conn.cursor() as cur:
                        await cur.execute(
                            """
                            UPDATE public.r3_blob_refs
                            SET status = 'consumed', consumed_at = NOW()
                            WHERE request_id = %s AND status = 'ready'
                            """,
                            (request_id,),
                        )
                        if cur.rowcount == 0:
                            logger.warning(
                                f"mark_consumed 未匹配行 "
                                f"(request_id={request_id}): "
                                f"状态可能已变更或行不存在"
                            )
        except Exception as e:
            raise DatabaseError(
                f"mark_consumed 失败: {str(e)}"
            )

    async def fetch_expired_for_cleanup(
        self, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """预留：返回过期候选引用行，供未来清理任务扫描。

        本期不调用。返回 status IN ('uploading', 'ready') 且
        expires_at < NOW() 的行，按 expires_at 升序。清理任务本身
        （DELETE 引用行 + blob delete）不在此实现，设计文档 十四.1 预留。

        Args:
            limit: 最多返回行数，默认 100。

        Returns:
            过期引用行列表，按 expires_at 升序。

        Raises:
            DatabaseError: 查询失败时抛出。
        """
        try:
            async with self.pool.connection() as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(
                        """
                        SELECT request_id, session_id, blob_key, backend,
                               status, size_bytes, created_at, ready_at,
                               consumed_at, expires_at
                        FROM public.r3_blob_refs
                        WHERE expires_at < NOW()
                          AND status IN ('uploading', 'ready')
                        ORDER BY expires_at ASC
                        LIMIT %s
                        """,
                        (limit,),
                    )
                    return [dict(r) for r in await cur.fetchall()]
        except Exception as e:
            raise DatabaseError(
                f"查询过期 r3_blob_refs 失败: {str(e)}"
            )
