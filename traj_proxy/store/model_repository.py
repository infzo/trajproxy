"""
ModelRepository - 模型配置操作

负责 model_registry 表的 CRUD 操作。
"""

from typing import List, Optional
from datetime import datetime
import json
import time
import traceback
from psycopg import errors as pg_errors
from psycopg.rows import dict_row

from traj_proxy.store.models import ModelConfig
from traj_proxy.store.notification_listener import CHANNEL
from traj_proxy.exceptions import DatabaseError
from traj_proxy.utils.logger import get_logger

logger = get_logger(__name__)


class ModelRepository:
    """模型配置仓库

    提供模型配置的持久化和查询功能。
    """

    def __init__(self, pool):
        """初始化 ModelRepository

        Args:
            pool: PostgreSQL 连接池
        """
        self.pool = pool

    async def register(
        self,
        model_name: str,
        url: str,
        api_key: str,
        tokenizer_path: Optional[str] = None,
        token_in_token_out: bool = False,
        run_id: str = "",
        tool_parser: str = "",
        reasoning_parser: str = ""
    ) -> ModelConfig:
        """注册新模型到数据库（使用 UPSERT）

        Args:
            model_name: 模型名称
            url: Infer 服务 URL
            api_key: API 密钥
            tokenizer_path: Tokenizer 路径（可选，直接转发模式下不需要）
            token_in_token_out: 是否使用 Token-in-Token-out 模式
            run_id: 运行ID，空字符串表示全局模型
            tool_parser: 工具解析器名称
            reasoning_parser: 推理解析器名称

        Returns:
            ModelConfig 实例

        Raises:
            DatabaseError: 数据库操作失败
        """
        try:
            async with self.pool.connection() as conn:
                now = datetime.now()
                await conn.execute("""
                    INSERT INTO model_registry
                    (run_id, model_name, url, api_key, tokenizer_path, token_in_token_out,
                     tool_parser, reasoning_parser, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (run_id, model_name)
                    DO UPDATE SET
                        url = EXCLUDED.url,
                        api_key = EXCLUDED.api_key,
                        tokenizer_path = EXCLUDED.tokenizer_path,
                        token_in_token_out = EXCLUDED.token_in_token_out,
                        tool_parser = EXCLUDED.tool_parser,
                        reasoning_parser = EXCLUDED.reasoning_parser,
                        updated_at = EXCLUDED.updated_at
                """, (run_id, model_name, url, api_key, tokenizer_path, token_in_token_out,
                      tool_parser, reasoning_parser, now))

                # 发送 NOTIFY 通知其他 Worker
                payload = json.dumps({
                    "action": "register",
                    "run_id": run_id,
                    "model_name": model_name,
                    "timestamp": time.time(),
                })
                # NOTIFY 语法：NOTIFY channel, 'payload'
                # 使用 psycopg.sql 安全构建 SQL
                from psycopg import sql
                await conn.execute(
                    sql.SQL("NOTIFY {}, {}").format(
                        sql.Identifier(CHANNEL),
                        sql.Literal(payload)
                    )
                )

                return ModelConfig(
                    run_id=run_id,
                    model_name=model_name,
                    url=url,
                    api_key=api_key,
                    tokenizer_path=tokenizer_path,
                    token_in_token_out=token_in_token_out,
                    tool_parser=tool_parser,
                    reasoning_parser=reasoning_parser,
                    updated_at=now
                )
        except Exception as e:
            raise DatabaseError(f"注册模型到数据库失败: {str(e)}\n{traceback.format_exc()}")

    async def unregister(self, model_name: str, run_id: str = "") -> bool:
        """从数据库删除模型

        Args:
            model_name: 模型名称
            run_id: 运行ID，空字符串表示全局模型

        Returns:
            是否成功删除

        Raises:
            DatabaseError: 数据库操作失败
        """
        try:
            async with self.pool.connection() as conn:
                result = await conn.execute("""
                    DELETE FROM model_registry
                    WHERE run_id = %s AND model_name = %s
                """, (run_id, model_name))

                # 发送 NOTIFY 通知其他 Worker
                if result.rowcount > 0:
                    payload = json.dumps({
                        "action": "unregister",
                        "run_id": run_id,
                        "model_name": model_name,
                        "timestamp": time.time(),
                    })
                    from psycopg import sql
                    await conn.execute(
                        sql.SQL("NOTIFY {}, {}").format(
                            sql.Identifier(CHANNEL),
                            sql.Literal(payload)
                        )
                    )

                return result.rowcount > 0
        except Exception as e:
            raise DatabaseError(f"从数据库删除模型失败: {str(e)}\n{traceback.format_exc()}")

    async def get_all(self) -> List[ModelConfig]:
        """获取所有模型配置（动态模型）

        Returns:
            ModelConfig 列表

        Raises:
            DatabaseError: 数据库操作失败（表不存在除外）
        """
        try:
            async with self.pool.connection() as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute("""
                        SELECT run_id, model_name, url, api_key, tokenizer_path, token_in_token_out,
                               tool_parser, reasoning_parser, updated_at
                        FROM model_registry
                        ORDER BY run_id, model_name
                    """)
                    rows = await cur.fetchall()

                    return [
                        ModelConfig(
                            run_id=row["run_id"],
                            model_name=row["model_name"],
                            url=row["url"],
                            api_key=row["api_key"],
                            tokenizer_path=row["tokenizer_path"],
                            token_in_token_out=row["token_in_token_out"],
                            tool_parser=row["tool_parser"],
                            reasoning_parser=row["reasoning_parser"],
                            updated_at=row["updated_at"]
                        )
                        for row in rows
                    ]
        except pg_errors.UndefinedTable:
            # 表不存在，返回空列表（首次启动时可能出现）
            logger.info("model_registry 表不存在，返回空列表")
            return []
        except Exception as e:
            raise DatabaseError(f"从数据库获取模型列表失败: {str(e)}\n{traceback.format_exc()}")

    async def get_by_key(self, run_id: str, model_name: str) -> Optional[ModelConfig]:
        """根据键获取单个模型配置（用于增量同步）

        Args:
            run_id: 运行ID
            model_name: 模型名称

        Returns:
            ModelConfig（如果找到）或 None

        Raises:
            DatabaseError: 数据库操作失败
        """
        try:
            async with self.pool.connection() as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute("""
                        SELECT run_id, model_name, url, api_key, tokenizer_path,
                               token_in_token_out, tool_parser, reasoning_parser, updated_at
                        FROM model_registry
                        WHERE run_id = %s AND model_name = %s
                    """, (run_id, model_name))
                    row = await cur.fetchone()
                    if row is None:
                        return None
                    return ModelConfig(
                        run_id=row["run_id"],
                        model_name=row["model_name"],
                        url=row["url"],
                        api_key=row["api_key"],
                        tokenizer_path=row["tokenizer_path"],
                        token_in_token_out=row["token_in_token_out"],
                        tool_parser=row["tool_parser"],
                        reasoning_parser=row["reasoning_parser"],
                        updated_at=row["updated_at"],
                    )
        except Exception as e:
            raise DatabaseError(f"获取模型失败 ({run_id}, {model_name}): {str(e)}\n{traceback.format_exc()}")
