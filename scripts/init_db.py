#!/usr/bin/env python3
"""
数据库一次性初始化脚本

在集群部署前执行一次，创建数据库、表和索引。
支持幂等执行，重复运行不会报错。

用法:
    # 从环境变量读取 DATABASE_URL
    python scripts/init_db.py

    # 从命令行参数指定
    python scripts/init_db.py --db-url postgresql://user:pass@host:port/dbname
"""

import argparse
import os
import re
import sys
import traceback

import psycopg


def ensure_database_exists(db_url: str):
    """确保目标数据库存在，如果不存在则创建"""
    parsed = __import__("urllib.parse", fromlist=["urlparse"]).urlparse(db_url)
    db_name = parsed.path.lstrip("/")
    host = parsed.hostname
    port = parsed.port or 5432
    user = parsed.username
    password = parsed.password

    # 验证标识符，防止 SQL 注入
    identifier_pattern = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    if not identifier_pattern.match(db_name):
        print(f"错误: 无效的数据库名称: {db_name}")
        sys.exit(1)
    if not identifier_pattern.match(user):
        print(f"错误: 无效的用户名称: {user}")
        sys.exit(1)

    default_url = f"postgresql://{user}:{password}@{host}:{port}/postgres"

    try:
        with psycopg.connect(default_url, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
                if not cur.fetchone():
                    print(f"数据库 '{db_name}' 不存在，正在创建...")
                    cur.execute(
                        "CREATE DATABASE {} OWNER {}".format(
                            psycopg.sql.Identifier(db_name).as_string(conn),
                            psycopg.sql.Identifier(user).as_string(conn),
                        )
                    )
                    print(f"数据库 '{db_name}' 创建成功")
                else:
                    print(f"数据库 '{db_name}' 已存在，跳过")
    except Exception as e:
        print(f"警告: 检查/创建数据库时出错: {e}")
        print("将尝试继续连接目标数据库...")


def create_tables(db_url: str):
    """创建所有表和索引"""
    with psycopg.connect(db_url, autocommit=True) as conn:
        # ========== request_records 表 ==========
        print("正在创建 request_records 表...")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS request_records (
                id SERIAL PRIMARY KEY,
                unique_id TEXT NOT NULL UNIQUE,
                request_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                model TEXT NOT NULL,
                tokenizer_path TEXT NOT NULL,
                messages JSONB NOT NULL,

                -- 阶段1: OpenAI Chat 格式
                raw_request JSONB,
                raw_response JSONB,

                -- 阶段2: 文本推理格式（Token模式）
                text_request JSONB,
                text_response JSONB,

                -- 阶段3: Token 推理格式（Token模式）
                prompt_text TEXT,
                token_ids INTEGER[],
                token_request JSONB,
                token_response JSONB,

                -- 输出数据
                response_text TEXT,
                response_ids INTEGER[],

                -- 完整对话
                full_conversation_text TEXT,
                full_conversation_token_ids INTEGER[],

                -- 元数据
                start_time TIMESTAMP WITH TIME ZONE NOT NULL,
                end_time TIMESTAMP WITH TIME ZONE,
                processing_duration_ms FLOAT,

                -- 统计信息
                prompt_tokens INTEGER,
                completion_tokens INTEGER,
                total_tokens INTEGER,
                cache_hit_tokens INTEGER DEFAULT 0,

                -- 错误信息
                error TEXT,
                error_traceback TEXT,

                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """)
        print("request_records 表创建完成")

        # request_records 索引
        indexes = [
            ("request_records_session_id_idx", "CREATE INDEX IF NOT EXISTS request_records_session_id_idx ON request_records (session_id)"),
            ("request_records_request_id_idx", "CREATE INDEX IF NOT EXISTS request_records_request_id_idx ON request_records (request_id)"),
            ("request_records_unique_id_idx", "CREATE INDEX IF NOT EXISTS request_records_unique_id_idx ON request_records (unique_id)"),
            ("request_records_start_time_idx", "CREATE INDEX IF NOT EXISTS request_records_start_time_idx ON request_records (start_time DESC)"),
        ]
        for name, sql in indexes:
            print(f"  创建索引 {name}...")
            conn.execute(sql)

        # 兼容性：删除旧列（如果存在）
        print("检查 request_records 表列兼容性...")
        conn.execute("""
            DO $$
            BEGIN
                -- 删除旧的 response 列
                IF EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name='request_records' AND column_name='response') THEN
                    ALTER TABLE request_records DROP COLUMN response;
                END IF;
                -- 删除旧的 infer_request_body 列
                IF EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name='request_records' AND column_name='infer_request_body') THEN
                    ALTER TABLE request_records DROP COLUMN infer_request_body;
                END IF;
                -- 删除旧的 infer_response 列
                IF EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name='request_records' AND column_name='infer_response') THEN
                    ALTER TABLE request_records DROP COLUMN infer_response;
                END IF;
            END $$;
        """)

        # ========== model_registry 表 ==========
        print("正在创建 model_registry 表...")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS model_registry (
                id SERIAL PRIMARY KEY,
                run_id TEXT NOT NULL DEFAULT '',
                model_name TEXT NOT NULL,
                url TEXT NOT NULL,
                api_key TEXT NOT NULL,
                tokenizer_path TEXT,
                token_in_token_out BOOLEAN DEFAULT FALSE,
                tool_parser TEXT NOT NULL DEFAULT '',
                reasoning_parser TEXT NOT NULL DEFAULT '',
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                CONSTRAINT unique_run_model UNIQUE (run_id, model_name)
            )
        """)
        print("model_registry 表创建完成")

        # 兼容性：为旧表添加新列
        print("检查 model_registry 表列兼容性...")
        conn.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                               WHERE table_name='model_registry' AND column_name='tool_parser') THEN
                    ALTER TABLE model_registry ADD COLUMN tool_parser TEXT NOT NULL DEFAULT '';
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                               WHERE table_name='model_registry' AND column_name='reasoning_parser') THEN
                    ALTER TABLE model_registry ADD COLUMN reasoning_parser TEXT NOT NULL DEFAULT '';
                END IF;
                -- 修改 tokenizer_path 为可为空（直接转发模式不需要）
                -- 注意：PostgreSQL 无法直接修改列的 NOT NULL 约束，需要使用 ALTER COLUMN
            END $$;
        """)

        # 修改 tokenizer_path 列为可为空
        conn.execute("""
            ALTER TABLE model_registry ALTER COLUMN tokenizer_path DROP NOT NULL
        """)

        # model_registry 索引
        indexes = [
            ("model_registry_run_id_idx", "CREATE INDEX IF NOT EXISTS model_registry_run_id_idx ON model_registry (run_id)"),
            ("model_registry_model_name_idx", "CREATE INDEX IF NOT EXISTS model_registry_model_name_idx ON model_registry (model_name)"),
            ("model_registry_updated_at_idx", "CREATE INDEX IF NOT EXISTS model_registry_updated_at_idx ON model_registry (updated_at DESC)"),
        ]
        for name, sql in indexes:
            print(f"  创建索引 {name}...")
            conn.execute(sql)

        print("\n所有表和索引创建完成")


def main():
    parser = argparse.ArgumentParser(description="TrajProxy 数据库初始化脚本")
    parser.add_argument(
        "--db-url",
        default=os.environ.get("DATABASE_URL"),
        help="数据库连接 URL，例如 postgresql://user:pass@host:port/dbname。默认从环境变量 DATABASE_URL 读取。",
    )
    args = parser.parse_args()

    if not args.db_url:
        parser.error("请通过 --db-url 参数或 DATABASE_URL 环境变量提供数据库连接 URL")

    print(f"=== TrajProxy 数据库初始化 ===")
    print(f"数据库 URL: {re.sub(r'://[^:]+:[^@]+@', r'://***:***@', args.db_url)}")
    print()

    try:
        ensure_database_exists(args.db_url)
        create_tables(args.db_url)
        print("\n=== 初始化完成 ===")
    except Exception as e:
        print(f"\n初始化失败: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
