"""
DatabaseManager - 数据库管理器

负责连接池管理和数据库表创建。
"""

import asyncio
import re
from typing import Optional
from urllib.parse import urlparse
import psycopg
from psycopg_pool import AsyncConnectionPool

from traj_proxy.exceptions import DatabaseError
from traj_proxy.utils.logger import get_logger

logger = get_logger(__name__)


class DatabaseManager:
    """数据库管理器 - 处理连接池和表创建

    使用 PostgreSQL 存储数据，管理两个表：
    - request_records: 请求轨迹记录
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

    async def _ensure_database_exists(self):
        """确保目标数据库存在，如果不存在则创建"""
        parsed = urlparse(self.db_url)
        db_name = parsed.path.lstrip('/')
        host = parsed.hostname
        port = parsed.port or 5432
        user = parsed.username
        password = parsed.password

        # 验证数据库名和用户名，防止 SQL 注入
        # PostgreSQL 标识符只允许字母、数字和下划线，且不能以数字开头
        identifier_pattern = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')
        if not identifier_pattern.match(db_name):
            raise DatabaseError(f"无效的数据库名称: {db_name}")
        if not identifier_pattern.match(user):
            raise DatabaseError(f"无效的用户名称: {user}")

        # 连接到默认的 postgres 数据库检查目标数据库是否存在
        default_url = f"postgresql://{user}:{password}@{host}:{port}/postgres"

        try:
            async with await psycopg.AsyncConnection.connect(default_url, autocommit=True) as conn:
                async with conn.cursor() as cur:
                    # 检查数据库是否存在
                    await cur.execute(
                        "SELECT 1 FROM pg_database WHERE datname = %s",
                        (db_name,)
                    )
                    result = await cur.fetchone()

                    if not result:
                        logger.info(f"DatabaseManager: 数据库 '{db_name}' 不存在，正在创建...")
                        # 创建数据库（使用参数化查询避免 SQL 注入）
                        await cur.execute(
                            'CREATE DATABASE {} OWNER {}'.format(
                                psycopg.sql.Identifier(db_name).as_string(conn),
                                psycopg.sql.Identifier(user).as_string(conn)
                            )
                        )
                        logger.info(f"DatabaseManager: 数据库 '{db_name}' 创建成功")
                    else:
                        logger.debug(f"DatabaseManager: 数据库 '{db_name}' 已存在")
        except Exception as e:
            logger.warning(f"DatabaseManager: 检查/创建数据库时出错: {e}")
            # 如果连接 postgres 数据库失败，继续尝试原有逻辑

    async def initialize(self):
        """初始化连接池并创建必要的表"""
        logger.info("DatabaseManager: 开始初始化连接池和表")
        try:
            # 先确保数据库存在
            await self._ensure_database_exists()

            self.pool = AsyncConnectionPool(
                conninfo=self.db_url,
                min_size=self.pool_config["min_size"],
                max_size=self.pool_config["max_size"],
                timeout=self.pool_config["timeout"]
            )
            # 显式打开连接池
            logger.info("DatabaseManager: 正在打开连接池...")
            await self.pool.open()
            logger.info("DatabaseManager: 连接池已打开")
            await self._create_tables()
            logger.info("DatabaseManager: 初始化完成")
        except Exception as e:
            logger.error(f"DatabaseManager: 初始化失败: {e}")
            raise

    async def close(self):
        """关闭连接池"""
        if self.pool:
            await self.pool.close()

    async def _index_exists(self, conn, index_name: str) -> bool:
        """检查索引是否存在

        Args:
            conn: 数据库连接
            index_name: 索引名称

        Returns:
            bool: 索引是否存在
        """
        result = await conn.execute("""
            SELECT 1 FROM pg_indexes WHERE indexname = %s
        """, (index_name,))
        row = await result.fetchone()
        return row is not None

    async def _create_table_index(self, conn, index_name: str, sql: str):
        """安全创建索引

        Args:
            conn: 数据库连接
            index_name: 索引名称
            sql: 创建索引的 SQL 语句
        """
        try:
            # 先检查索引是否存在
            if await self._index_exists(conn, index_name):
                logger.debug(f"DatabaseManager: 索引 {index_name} 已存在，跳过创建")
                return

            # 创建索引
            await conn.execute(sql)
            logger.debug(f"DatabaseManager: 索引 {index_name} 创建成功")
        except Exception as e:
            # 处理并发创建导致的错误
            error_str = str(e)
            if "already exists" in error_str:
                logger.debug(f"DatabaseManager: 索引 {index_name} 已存在（并发创建）")
            elif "InFailedSqlTransaction" in error_str or "current transaction is aborted" in error_str:
                # 事务处于失败状态，回滚后重新检查索引
                await conn.rollback()
                if await self._index_exists(conn, index_name):
                    logger.debug(f"DatabaseManager: 索引 {index_name} 已存在（事务恢复后确认）")
                else:
                    logger.warning(f"DatabaseManager: 索引 {index_name} 创建时发生事务错误: {e}")
            else:
                raise

    async def _create_tables(self):
        """创建数据库表和索引"""
        logger.info("DatabaseManager: 开始创建数据库表...")
        try:
            async with self.pool.connection() as conn:
                # 创建 request_records 表
                logger.info("DatabaseManager: 创建 request_records 表...")
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
                )
            """)

            # session_id 索引，用于前缀匹配查询
            await self._create_table_index(conn, "request_records_session_id_idx", """
                CREATE INDEX request_records_session_id_idx
                    ON request_records (session_id)
            """)

            # request_id 索引
            await self._create_table_index(conn, "request_records_request_id_idx", """
                CREATE INDEX request_records_request_id_idx
                    ON request_records (request_id)
            """)

            # unique_id 索引
            await self._create_table_index(conn, "request_records_unique_id_idx", """
                CREATE INDEX request_records_unique_id_idx
                    ON request_records (unique_id)
            """)

            # 时间索引，用于查询历史
            await self._create_table_index(conn, "request_records_start_time_idx", """
                CREATE INDEX request_records_start_time_idx
                    ON request_records (start_time DESC)
            """)
            logger.info("DatabaseManager: request_records 表及索引创建完成")

            # 创建 model_registry 表（用于多 Worker 动态模型同步）
            logger.info("DatabaseManager: 创建 model_registry 表...")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS model_registry (
                    id SERIAL PRIMARY KEY,
                    job_id TEXT NOT NULL DEFAULT '',
                    model_name TEXT NOT NULL,
                    url TEXT NOT NULL,
                    api_key TEXT NOT NULL,
                    tokenizer_path TEXT NOT NULL,
                    token_in_token_out BOOLEAN DEFAULT FALSE,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    CONSTRAINT unique_job_model UNIQUE (job_id, model_name)
                )
            """)

            # 作业ID索引
            await self._create_table_index(conn, "model_registry_job_id_idx", """
                CREATE INDEX model_registry_job_id_idx
                    ON model_registry (job_id)
            """)

            # 模型名称索引
            await self._create_table_index(conn, "model_registry_model_name_idx", """
                CREATE INDEX model_registry_model_name_idx
                    ON model_registry (model_name)
            """)

            # 更新时间索引（用于轮询同步）
            await self._create_table_index(conn, "model_registry_updated_at_idx", """
                CREATE INDEX model_registry_updated_at_idx
                    ON model_registry (updated_at DESC)
            """)

            logger.info("DatabaseManager: 数据库表创建完成")
        except Exception as e:
            logger.error(f"DatabaseManager: 创建数据库表失败: {e}")
            raise
