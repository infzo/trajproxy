"""
DatabaseManager - 数据库管理器

负责连接池管理和轨迹记录的存储查询操作。
"""

from typing import List, Dict, Any, Optional
from psycopg_pool import AsyncConnectionPool
from psycopg.rows import dict_row

from traj_proxy.exceptions import DatabaseError
from traj_proxy.proxy_core.context import ProcessContext


class DatabaseManager:
    """数据库管理器 - 处理连接池和查询操作

    使用 PostgreSQL 存储请求轨迹记录，支持前缀匹配查询。
    """

    def __init__(self, db_url: str):
        """初始化 DatabaseManager

        Args:
            db_url: 数据库连接 URL（如 postgresql://user:pass@host:port/dbname）
        """
        self.db_url = db_url
        self.pool: Optional[AsyncConnectionPool] = None

    async def initialize(self):
        """初始化连接池并创建必要的表"""
        self.pool = AsyncConnectionPool(
            conninfo=self.db_url,
            min_size=2,
            max_size=20,
            timeout=30
        )
        await self._create_tables()

    async def close(self):
        """关闭连接池"""
        if self.pool:
            await self.pool.close()

    async def _create_tables(self):
        """创建数据库表和索引"""
        async with self.pool.connection() as conn:
            # 创建 request_records 表
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS request_records (
                    id SERIAL PRIMARY KEY,
                    unique_id TEXT NOT NULL UNIQUE,
                    request_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    model TEXT NOT NULL,
                    tokenizer_path TEXT NOT NULL,
                    messages JSONB NOT NULL,
                    prompt_text TEXT NOT NULL,
                    token_ids INTEGER[] NOT NULL,
                    full_conversation_text TEXT,
                    full_conversation_token_ids INTEGER[],
                    response JSONB NOT NULL,
                    response_text TEXT,
                    response_ids INTEGER[],
                    start_time TIMESTAMP WITH TIME ZONE NOT NULL,
                    end_time TIMESTAMP WITH TIME ZONE,
                    processing_duration_ms FLOAT,
                    prompt_tokens INTEGER,
                    completion_tokens INTEGER,
                    total_tokens INTEGER,
                    cache_hit_tokens INTEGER DEFAULT 0,
                    error TEXT,
                    error_traceback TEXT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                );

                -- session_id 索引，用于前缀匹配查询
                CREATE INDEX IF NOT EXISTS request_records_session_id_idx
                    ON request_records (session_id);

                -- request_id 索引
                CREATE INDEX IF NOT EXISTS request_records_request_id_idx
                    ON request_records (request_id);

                -- unique_id 索引
                CREATE INDEX IF NOT EXISTS request_records_unique_id_idx
                    ON request_records (unique_id);

                -- 时间索引，用于查询历史
                CREATE INDEX IF NOT EXISTS request_records_start_time_idx
                    ON request_records (start_time DESC);
            """)

    async def insert_trajectory(self, context: ProcessContext, tokenizer_path: str):
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
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21)
                """,
                    context.unique_id,
                    context.request_id,
                    context.session_id or "",
                    context.model,
                    tokenizer_path,
                    context.messages,
                    context.prompt_text or "",
                    context.token_ids or [],
                    context.full_conversation_text,
                    context.full_conversation_token_ids,
                    context.response,
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
        except Exception as e:
            raise DatabaseError(f"插入轨迹记录失败: {str(e)}")

    async def get_request_records_by_session(
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
                            unique_id, request_id, session_id, model, tokenizer_path,
                            prompt_text, token_ids, full_conversation_text,
                            full_conversation_token_ids, start_time
                        FROM request_records
                        WHERE session_id = $1
                        ORDER BY start_time DESC
                        LIMIT $2
                    """, (session_id, limit))
                    return await cur.fetchall()
        except Exception as e:
            raise DatabaseError(f"查询 session 记录失败: {str(e)}")

    async def get_trajectory_by_unique_id(
        self,
        unique_id: str
    ) -> Optional[Dict[str, Any]]:
        """根据 unique_id 获取单条轨迹记录

        Args:
            unique_id: 唯一 ID (格式: {session_id}#{req_id})

        Returns:
            轨迹记录字典，如果不存在则返回 None

        Raises:
            DatabaseError: 当查询失败时抛出
        """
        try:
            async with self.pool.connection() as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute("""
                        SELECT * FROM request_records WHERE unique_id = $1
                    """, (unique_id,))
                    return await cur.fetchone()
        except Exception as e:
            raise DatabaseError(f"查询轨迹记录失败: {str(e)}")

    async def get_trajectory_by_request_id(
        self,
        request_id: str
    ) -> Optional[Dict[str, Any]]:
        """根据 request_id 获取单条轨迹记录

        Args:
            request_id: 请求 ID

        Returns:
            轨迹记录字典，如果不存在则返回 None

        Raises:
            DatabaseError: 当查询失败时抛出
        """
        try:
            async with self.pool.connection() as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute("""
                        SELECT * FROM request_records WHERE request_id = $1
                    """, (request_id,))
                    return await cur.fetchone()
        except Exception as e:
            raise DatabaseError(f"查询轨迹记录失败: {str(e)}")
