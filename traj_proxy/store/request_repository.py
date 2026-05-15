"""
RequestRepository - 请求轨迹记录操作

负责 request_metadata 和 request_details_active 两张表的 CRUD 操作。
采用方案二架构：元数据表长期保留统计信息，详情表只存近期大字段。
"""

import time
from typing import List, Dict, Any, Optional, TYPE_CHECKING
from datetime import datetime
import traceback
from psycopg.rows import dict_row
from psycopg.types.json import Json

from traj_proxy.exceptions import DatabaseError
from traj_proxy.utils.logger import get_logger

logger = get_logger(__name__)

# 延迟导入以避免循环导入
if TYPE_CHECKING:
    from traj_proxy.proxy_core.context import ProcessContext

# ======================== 字段映射（fields 参数支持） ========================

# 详情查询字段映射（JOIN request_metadata + request_details_active）
FIELDS_MAPPING: Dict[str, str] = {
    "id": "m.id",
    "unique_id": "m.unique_id",
    "request_id": "m.request_id",
    "session_id": "m.session_id",
    "run_id": "m.run_id",
    "model": "m.model",
    "prompt_tokens": "m.prompt_tokens",
    "completion_tokens": "m.completion_tokens",
    "total_tokens": "m.total_tokens",
    "cache_hit_tokens": "m.cache_hit_tokens",
    "processing_duration_ms": "m.processing_duration_ms",
    "start_time": "m.start_time",
    "end_time": "m.end_time",
    "created_at": "m.created_at",
    "error": "m.error",
    "tokenizer_path": "d.tokenizer_path",
    "messages": "d.messages",
    "raw_request": "d.raw_request",
    "raw_response": "d.raw_response",
    "text_request": "d.text_request",
    "text_response": "d.text_response",
    "prompt_text": "d.prompt_text",
    "token_ids": "d.token_ids",
    "token_request": "d.token_request",
    "token_response": "d.token_response",
    "response_text": "d.response_text",
    "response_ids": "d.response_ids",
    "full_conversation_text": "d.full_conversation_text",
    "full_conversation_token_ids": "d.full_conversation_token_ids",
    "error_traceback": "d.error_traceback",
}

# 元数据查询字段映射（仅 request_metadata 表，无 JOIN）
META_FIELDS_MAPPING: Dict[str, str] = {
    "id": "id",
    "unique_id": "unique_id",
    "request_id": "request_id",
    "session_id": "session_id",
    "run_id": "run_id",
    "model": "model",
    "prompt_tokens": "prompt_tokens",
    "completion_tokens": "completion_tokens",
    "total_tokens": "total_tokens",
    "cache_hit_tokens": "cache_hit_tokens",
    "processing_duration_ms": "processing_duration_ms",
    "start_time": "start_time",
    "end_time": "end_time",
    "created_at": "created_at",
    "error": "error",
    "archive_location": "archive_location",
    "archived_at": "archived_at",
}

ALL_FIELDS = list(FIELDS_MAPPING.keys())
META_ALL_FIELDS = list(META_FIELDS_MAPPING.keys())


def resolve_fields(fields_str: Optional[str], mapping: Dict[str, str]) -> List[str]:
    """解析 fields 参数，返回最终的字段名列表

    支持两种语法：
    - field_name: 包含指定字段
    - -field_name: 排除指定字段

    不在 mapping 白名单中的字段名静默忽略。

    Args:
        fields_str: 逗号分隔的字段名，None 或空字符串表示返回全部
        mapping: 字段名 → SQL 列表达式的映射（同时作为白名单）

    Returns:
        保持 mapping 键顺序的字段名列表
    """
    all_fields = list(mapping.keys())

    if not fields_str or not fields_str.strip():
        return all_fields[:]

    parts = [p.strip() for p in fields_str.split(",") if p.strip()]
    included: set[str] = set()
    excluded: set[str] = set()

    for part in parts:
        if part.startswith("-"):
            name = part[1:]
            if name in mapping:
                excluded.add(name)
        else:
            if part in mapping:
                included.add(part)

    if not included:
        included = set(all_fields)

    return [f for f in all_fields if f in included and f not in excluded]


class RequestRepository:
    """请求记录仓库

    提供 request_metadata + request_details_active 双表的持久化和查询功能。
    元数据表长期保留统计信息，详情表只存近期大字段，过期后归档到外部存储。
    对外接口保持不变，业务代码零修改。
    """

    def __init__(self, pool):
        """初始化 RequestRepository

        Args:
            pool: PostgreSQL 连接池
        """
        self.pool = pool

    async def insert(self, context: "ProcessContext", tokenizer_path: Optional[str] = None, run_id: Optional[str] = None):
        """插入轨迹记录（事务双写）

        在同一事务中写入 request_metadata 和 request_details_active。

        Args:
            context: 处理上下文
            tokenizer_path: Tokenizer 路径（可选，直接转发模式下不需要）
            run_id: 运行ID（可选，独立存储）

        Raises:
            DatabaseError: 当插入失败时抛出
        """
        try:
            async with self.pool.connection() as conn:
                async with conn.transaction():
                    # 1. 写入元数据表（统计字段，长期保留）
                    await conn.execute("""
                        INSERT INTO public.request_metadata (
                            unique_id, request_id, session_id, run_id, model,
                            prompt_tokens, completion_tokens, total_tokens,
                            cache_hit_tokens, processing_duration_ms,
                            start_time, end_time, error
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        context.unique_id,
                        context.request_id,
                        context.session_id or "",
                        run_id,
                        context.model,
                        context.prompt_tokens,
                        context.completion_tokens,
                        context.total_tokens,
                        context.cache_hit_tokens or 0,
                        context.processing_duration_ms,
                        context.start_time,
                        context.end_time,
                        context.error,
                    ))

                    # 2. 写入详情表（大字段，近期保留，按月分区）
                    await conn.execute("""
                        INSERT INTO public.request_details_active (
                            unique_id, created_at, tokenizer_path, messages,
                            raw_request, raw_response,
                            text_request, text_response,
                            prompt_text, token_ids,
                            token_request, token_response,
                            response_text, response_ids,
                            full_conversation_text, full_conversation_token_ids,
                            error_traceback
                        ) VALUES (%s, NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        context.unique_id,
                        tokenizer_path or "",
                        Json(context.messages),
                        Json(context.raw_request) if context.raw_request else None,
                        Json(context.raw_response) if context.raw_response else None,
                        Json(context.text_request) if context.text_request else None,
                        Json(context.text_response) if context.text_response else None,
                        context.prompt_text or "",
                        context.token_ids or [],
                        Json(context.token_request) if context.token_request else None,
                        Json(context.token_response) if context.token_response else None,
                        context.response_text,
                        context.response_ids,
                        context.full_conversation_text,
                        context.full_conversation_token_ids,
                        context.error_traceback
                    ))
        except Exception as e:
            raise DatabaseError(f"插入轨迹记录失败: {str(e)}\n{traceback.format_exc()}")

    async def get_prefix_candidates(
        self,
        session_id: str,
    ) -> List[Dict[str, Any]]:
        """获取前缀匹配候选记录（仅查询匹配所需字段）

        与 get_by_session 不同，此方法只查询 full_conversation_text
        和 full_conversation_token_ids 两个字段，避免加载 messages、
        raw_request 等大 JSONB 字段，大幅减少数据传输量。

        查询全部历史记录，不做 LIMIT，保证前缀匹配的完整性。

        Args:
            session_id: 会话 ID

        Returns:
            候选记录列表，按 start_time 倒序
        """
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("""
                    SELECT
                        d.full_conversation_text,
                        d.full_conversation_token_ids
                    FROM public.request_metadata m
                    JOIN public.request_details_active d ON m.unique_id = d.unique_id
                    WHERE m.session_id = %s
                      AND m.archive_location IS NULL
                      AND d.full_conversation_text IS NOT NULL
                      AND d.full_conversation_text != ''
                    ORDER BY m.start_time DESC
                """, (session_id,))
                return await cur.fetchall()

    async def get_by_session(
        self,
        session_id: str,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """根据 session_id 获取活跃请求记录（JOIN 查询）

        按创建时间倒序排列，只返回活跃（未归档）的完整记录。
        返回字段与旧版完全一致，业务代码零修改。

        Args:
            session_id: 会话 ID (格式: app_id,sample_id,task_id)
            limit: 最多返回的记录数

        Returns:
            轨迹记录列表，按创建时间倒序

        Raises:
            DatabaseError: 当查询失败时抛出
        """
        try:
            async with self.pool.connection() as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute("""
                        SELECT
                            m.id, m.unique_id, m.request_id, m.session_id, m.run_id, m.model,
                            m.prompt_tokens, m.completion_tokens, m.total_tokens,
                            m.cache_hit_tokens, m.processing_duration_ms,
                            m.start_time, m.end_time, m.created_at, m.error,
                            d.tokenizer_path, d.messages,
                            d.raw_request, d.raw_response,
                            d.text_request, d.text_response,
                            d.prompt_text, d.token_ids,
                            d.token_request, d.token_response,
                            d.response_text, d.response_ids,
                            d.full_conversation_text, d.full_conversation_token_ids,
                            d.error_traceback
                        FROM public.request_metadata m
                        JOIN public.request_details_active d ON m.unique_id = d.unique_id
                        WHERE m.session_id = %s AND m.archive_location IS NULL
                        ORDER BY m.start_time DESC
                        LIMIT %s
                    """, (session_id, limit))
                    return await cur.fetchall()
        except Exception as e:
            raise DatabaseError(f"查询 session 记录失败: {str(e)}\n{traceback.format_exc()}")

    async def get_metadata_by_session(
        self,
        session_id: str,
        limit: int = 10000,
        fields: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """根据 session_id 获取元数据（轻量查询，含活跃+已归档）

        只查 request_metadata 表，不 JOIN 详情表，适合统计展示。

        Args:
            session_id: 会话 ID
            limit: 最多返回的记录数
            fields: 逗号分隔的字段名列表，None 返回全部；
                    支持 field_name（包含）和 -field_name（排除）

        Returns:
            元数据记录列表

        Raises:
            DatabaseError: 当查询失败时抛出
        """
        try:
            field_list = resolve_fields(fields, META_FIELDS_MAPPING)
            select_columns = ", ".join(META_FIELDS_MAPPING[f] for f in field_list)
            async with self.pool.connection() as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(f"""
                        SELECT {select_columns}
                        FROM public.request_metadata
                        WHERE session_id = %s
                        ORDER BY start_time DESC
                        LIMIT %s
                    """, (session_id, limit))
                    return await cur.fetchall()
        except Exception as e:
            raise DatabaseError(f"查询 session 元数据失败: {str(e)}\n{traceback.format_exc()}")

    async def get_statistics(
        self,
        model: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """聚合统计查询

        在 request_metadata 表上进行聚合计算，轻量高效。

        Args:
            model: 按模型过滤（可选）
            start_time: 起始时间（可选）
            end_time: 结束时间（可选）

        Returns:
            统计结果字典

        Raises:
            DatabaseError: 当查询失败时抛出
        """
        conditions = []
        params = []
        idx = 1

        if model:
            conditions.append(f"model = %{idx}")
            params.append(model)
            idx += 1
        if start_time:
            conditions.append(f"start_time >= %{idx}")
            params.append(start_time)
            idx += 1
        if end_time:
            conditions.append(f"start_time < %{idx}")
            params.append(end_time)
            idx += 1

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        try:
            async with self.pool.connection() as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(f"""
                        SELECT
                            COUNT(*) as total_count,
                            COALESCE(SUM(total_tokens), 0) as total_tokens,
                            COALESCE(SUM(prompt_tokens), 0) as total_prompt_tokens,
                            COALESCE(SUM(completion_tokens), 0) as total_completion_tokens,
                            COALESCE(SUM(cache_hit_tokens), 0) as total_cache_hit_tokens,
                            COALESCE(AVG(processing_duration_ms), 0) as avg_duration_ms,
                            COUNT(*) FILTER (WHERE error IS NOT NULL) as error_count
                        FROM public.request_metadata
                        {where_clause}
                    """, tuple(params))
                    return await cur.fetchone()
        except Exception as e:
            raise DatabaseError(f"查询统计信息失败: {str(e)}\n{traceback.format_exc()}")

    async def list_sessions(
        self,
        run_id: str
    ) -> List[Dict[str, Any]]:
        """查询指定 run_id 下的 session 列表（带统计信息）

        按 session_id 分组统计记录数和时间范围。

        Args:
            run_id: 运行ID（必填）

        Returns:
            session 列表，每个包含 session_id、record_count、时间范围

        Raises:
            DatabaseError: 当查询失败时抛出
        """
        try:
            async with self.pool.connection() as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute("""
                        SELECT
                            session_id,
                            run_id,
                            COUNT(*) as record_count,
                            MIN(start_time) as first_request_time,
                            MAX(start_time) as last_request_time
                        FROM public.request_metadata
                        WHERE run_id = %s
                        GROUP BY session_id, run_id
                        ORDER BY session_id
                    """, (run_id,))
                    return await cur.fetchall()
        except Exception as e:
            raise DatabaseError(f"查询 session 列表失败: {str(e)}\n{traceback.format_exc()}")

    async def get_all_by_session(
        self,
        session_id: str,
        fields: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """查询指定 session 的所有轨迹记录

        与 get_by_session 方法一致，返回完整的 record 数据。
        只返回未归档的记录（archive_location IS NULL）。

        Args:
            session_id: 会话 ID
            fields: 逗号分隔的字段名列表，None 返回全部；
                    支持 field_name（包含）和 -field_name（排除）

        Returns:
            轨迹记录列表，按 start_time 倒序

        Raises:
            DatabaseError: 当查询失败时抛出
        """
        t_start = time.perf_counter()
        field_list = resolve_fields(fields, FIELDS_MAPPING)
        select_columns = ", ".join(FIELDS_MAPPING[f] for f in field_list)
        try:
            t_conn_start = time.perf_counter()
            async with self.pool.connection() as conn:
                t_conn_elapsed = (time.perf_counter() - t_conn_start) * 1000
                t_query_start = time.perf_counter()
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(f"""
                        SELECT {select_columns}
                        FROM public.request_metadata m
                        JOIN public.request_details_active d ON m.unique_id = d.unique_id
                        WHERE m.session_id = %s AND m.archive_location IS NULL
                        ORDER BY m.start_time DESC
                    """, (session_id,))
                    rows = await cur.fetchall()
                t_query_elapsed = (time.perf_counter() - t_query_start) * 1000
                t_total = (time.perf_counter() - t_start) * 1000
                logger.info(
                    f"[{session_id}] Repository.get_all_by_session: {len(rows)}条记录, "
                    f"字段数={len(field_list)}, 获取连接={t_conn_elapsed:.1f}ms, "
                    f"SQL执行+fetchall={t_query_elapsed:.1f}ms, 总仓库耗时={t_total:.1f}ms"
                )
                return rows
        except Exception as e:
            t_total = (time.perf_counter() - t_start) * 1000
            logger.error(f"[{session_id}] Repository.get_all_by_session 失败, 已耗时={t_total:.1f}ms: {e}")
            raise DatabaseError(f"查询轨迹记录失败: {str(e)}\n{traceback.format_exc()}")
