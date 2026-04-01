#!/bin/bash
# TrajProxy 统一入口脚本
# 1. 初始化数据库（创建数据库、表、索引）
# 2. 启动应用

set -e

echo "=== TrajProxy 容器启动 ==="

# ============================================
# 第一阶段：数据库初始化（内嵌 Python 代码）
# ============================================

if [ -n "${DATABASE_URL}" ]; then
    echo "--- 数据库初始化 ---"

    python3 - <<'PYTHON_SCRIPT'
import os
import sys
import re
import psycopg

def init_database():
    """初始化数据库：创建数据库、表、索引"""
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("错误: 未设置 DATABASE_URL 环境变量")
        sys.exit(1)

    # 解析数据库连接信息
    from urllib.parse import urlparse
    parsed = urlparse(db_url)
    db_name = parsed.path.lstrip("/")
    host = parsed.hostname
    port = parsed.port or 5432
    user = parsed.username
    password = parsed.password

    # 验证标识符
    identifier_pattern = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    if not identifier_pattern.match(db_name):
        print(f"错误: 无效的数据库名称: {db_name}")
        sys.exit(1)
    if not identifier_pattern.match(user):
        print(f"错误: 无效的用户名称: {user}")
        sys.exit(1)

    default_url = f"postgresql://{user}:{password}@{host}:{port}/postgres"

    # 1. 检查并创建数据库
    print(f"检查数据库 '{db_name}'...")
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
        print(f"错误: 创建数据库失败: {e}")
        sys.exit(1)

    # 2. 创建表和索引
    print("创建表和索引...")
    try:
        with psycopg.connect(db_url, autocommit=True) as conn:
            # request_records 表
            print("  创建 request_records 表...")
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
            print("  request_records 表创建完成")

            # request_records 索引
            print("  创建 request_records 索引...")
            conn.execute("CREATE INDEX IF NOT EXISTS request_records_session_id_idx ON request_records (session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS request_records_request_id_idx ON request_records (request_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS request_records_unique_id_idx ON request_records (unique_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS request_records_start_time_idx ON request_records (start_time DESC)")

            # model_registry 表
            print("  创建 model_registry 表...")
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
            print("  model_registry 表创建完成")

            # model_registry 索引
            print("  创建 model_registry 索引...")
            conn.execute("CREATE INDEX IF NOT EXISTS model_registry_run_id_idx ON model_registry (run_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS model_registry_model_name_idx ON model_registry (model_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS model_registry_updated_at_idx ON model_registry (updated_at DESC)")

            # 兼容性：删除旧列（如果存在）
            print("  检查 request_records 表列兼容性...")
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

            # 兼容性：为 model_registry 添加新列
            print("  检查 model_registry 表列兼容性...")
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
                END $$;
            """)

            # 修改 tokenizer_path 为可空（直接转发模式不需要）
            conn.execute("""
                DO $$
                BEGIN
                    -- 检查 tokenizer_path 是否有 NOT NULL 约束
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name='model_registry'
                        AND column_name='tokenizer_path'
                        AND is_nullable='NO'
                    ) THEN
                        ALTER TABLE model_registry ALTER COLUMN tokenizer_path DROP NOT NULL;
                    END IF;
                END $$;
            """)

            print("表和索引创建完成")
    except Exception as e:
        print(f"错误: 创建表失败: {e}")
        sys.exit(1)

    print("--- 数据库初始化完成 ---")

if __name__ == "__main__":
    init_database()
PYTHON_SCRIPT

    if [ $? -ne 0 ]; then
        echo "错误: 数据库初始化失败，退出"
        exit 1
    fi
else
    echo "警告: 未设置 DATABASE_URL，跳过数据库初始化"
fi

# ============================================
# 第二阶段：启动应用
# ============================================

echo "=== 启动 TrajProxy 服务 ==="
exec python -m traj_proxy.app
