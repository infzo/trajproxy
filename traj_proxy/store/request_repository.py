"""
RequestRepository - 请求轨迹记录操作

负责 request_records 表的 CRUD 操作。
"""

from typing import List, Dict, Any, TYPE_CHECKING
from psycopg.rows import dict_row
from psycopg.types.json import Json

from traj_proxy.store.models import RequestRecord
from traj_proxy.exceptions import DatabaseError

# 延迟导入以避免循环导入
if TYPE_CHECKING:
    from traj_proxy.proxy_core.context import ProcessContext


class RequestRepository:
    """请求记录仓库

    提供 request_records 表的持久化和查询功能。
    """

    def __init__(self, pool):
        """初始化 RequestRepository

        Args:
            pool: PostgreSQL 连接池
        """
        self.pool = pool

    async def insert(self, context: "ProcessContext", tokenizer_path: str):
        """插入轨迹记录

        Args:
            context: 处理上下文
            tokenizer_path: Tokenizer 路径

        Raises:
            DatabaseError: 当插入失败时抛出
        """
        try:
            async with self.pool.connection() as conn:
                await conn.execute("""
                    INSERT INTO request_records (
                        unique_id, request_id, session_id, model, tokenizer_path,
                        messages, prompt_text, token_ids, full_conversation_text,
                        full_conversation_token_ids, response, response_text,
                        response_ids, start_time, end_time, processing_duration_ms,
                        prompt_tokens, completion_tokens, total_tokens,
                        cache_hit_tokens, error, error_traceback
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                    (
                        context.unique_id,
                        context.request_id,
                        context.session_id or "",
                        context.model,
                        tokenizer_path,
                        Json(context.messages),
                        context.prompt_text or "",
                        context.token_ids or [],
                        context.full_conversation_text,
                        context.full_conversation_token_ids,
                        Json(context.response) if context.response else None,
                        context.response_text,
                        context.response_ids,
                        context.start_time,
                        context.end_time,
                        context.processing_duration_ms,
                        context.prompt_tokens,
                        context.completion_tokens,
                        context.total_tokens,
                        context.cache_hit_tokens,
                        context.error,
                        context.error_traceback
                    )
                )
        except Exception as e:
            raise DatabaseError(f"插入轨迹记录失败: {str(e)}")

    async def get_by_session(
        self,
        session_id: str,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """根据 session_id 获取所有历史请求记录

        按创建时间倒序排列，用于前缀匹配。

        Args:
            session_id: 会话 ID (格式: app_id#sample_id#task_id)
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
                            *
                        FROM request_records
                        WHERE session_id = %s
                        ORDER BY start_time DESC
                        LIMIT %s
                    """, (session_id, limit))
                    return await cur.fetchall()
        except Exception as e:
            raise DatabaseError(f"查询 session 记录失败: {str(e)}")
