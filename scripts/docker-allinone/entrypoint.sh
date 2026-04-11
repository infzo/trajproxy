#!/bin/bash
# TrajProxy All-in-One 入口脚本
# 用法：
#   ./entrypoint_allinone.sh           -- 完整初始化 + 启动 supervisord
#   ./entrypoint_allinone.sh litellm   -- 启动 litellm（supervisord 调用）
#   ./entrypoint_allinone.sh traj_proxy -- 启动 traj_proxy（supervisord 调用）

set -e

# ========================================
# 环境变量默认值
# ========================================
PGDATA="${PGDATA:-/data/postgres}"
POSTGRES_DB="${POSTGRES_DB:-litellm}"
POSTGRES_USER="${POSTGRES_USER:-llmproxy}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-dbpassword9090}"
TRAJ_PROXY_DB="${TRAJ_PROXY_DB:-traj_proxy}"
LITELLM_MASTER_KEY="${LITELLM_MASTER_KEY:-sk-1234}"
LITELLM_SALT_KEY="${LITELLM_SALT_KEY:-sk-1234}"

# 自动生成数据库连接 URL（用户显式设置则优先）
export DATABASE_URL="${DATABASE_URL:-postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@127.0.0.1:5432/${TRAJ_PROXY_DB}}"
export LITELLM_DATABASE_URL="${LITELLM_DATABASE_URL:-postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@127.0.0.1:5432/${POSTGRES_DB}}"
export STORE_MODEL_IN_DB="${STORE_MODEL_IN_DB:-True}"

# ========================================
# 子命令模式（supervisord 调用）
# ========================================
if [ "$1" = "litellm" ]; then
    echo "=== 启动 LiteLLM ==="
    # 等待 PostgreSQL 就绪
    until pg_isready -h 127.0.0.1 -p 5432 -U "${POSTGRES_USER}" -q 2>/dev/null; do
        echo "等待 PostgreSQL 就绪..."
        sleep 2
    done
    exec /opt/litellm-venv/bin/litellm --config /app/configs/litellm_allinone.yaml
fi

if [ "$1" = "traj_proxy" ]; then
    echo "=== 启动 TrajProxy ==="
    # 等待 PostgreSQL 就绪
    until pg_isready -h 127.0.0.1 -p 5432 -U "${POSTGRES_USER}" -q 2>/dev/null; do
        echo "等待 PostgreSQL 就绪..."
        sleep 2
    done
    exec python -m traj_proxy.app
fi

# ========================================
# 完整初始化模式（容器入口）
# ========================================

echo "=== TrajProxy All-in-One 容器启动 ==="

# ========================================
# 第一阶段：PostgreSQL 数据目录初始化
# ========================================
if [ ! -f "${PGDATA}/PG_VERSION" ]; then
    echo "--- 初始化 PostgreSQL 数据目录 ---"
    mkdir -p "${PGDATA}"
    chown postgres:postgres "${PGDATA}"

    # 初始化数据库集群
    gosu postgres /usr/lib/postgresql/16/bin/initdb \
        -D "${PGDATA}" \
        -E UTF8 \
        --locale=C \
        --auth=trust

    # 配置 pg_hba.conf 允许本地连接
    cat > "${PGDATA}/pg_hba.conf" <<EOF
# 本地连接（all-in-one 模式）
local   all             all                                     trust
host    all             all             127.0.0.1/32            md5
host    all             all             ::1/128                 md5
EOF

    echo "PostgreSQL 数据目录初始化完成"
else
    echo "PostgreSQL 数据目录已存在，跳过初始化"
fi

# 确保目录权限正确
chown -R postgres:postgres "${PGDATA}" /run/postgresql /var/log/supervisor

# ========================================
# 第二阶段：临时启动 PostgreSQL，创建用户和数据库
# ========================================
echo "--- 启动 PostgreSQL 进行初始化 ---"
gosu postgres /usr/lib/postgresql/16/bin/pg_ctl \
    -D "${PGDATA}" \
    -o "-c unix_socket_directories=/run/postgresql" \
    -l /var/log/supervisor/postgresql_init.log \
    start -w -t 60

# 等待 PostgreSQL 接受连接
echo "--- 等待 PostgreSQL 就绪 ---"
for i in $(seq 1 30); do
    if gosu postgres psql -U postgres -c '\q' 2>/dev/null; then
        echo "PostgreSQL 已就绪"
        break
    fi
    sleep 1
done

# 创建用户（如果不存在）
echo "--- 创建数据库用户 ---"
gosu postgres psql -c \
    "DO \$\$ BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${POSTGRES_USER}') THEN
            CREATE USER ${POSTGRES_USER} WITH PASSWORD '${POSTGRES_PASSWORD}' LOGIN;
        END IF;
    END \$\$;"

# 创建数据库（如果不存在）
echo "--- 创建数据库 ---"
gosu postgres psql -c \
    "SELECT 'CREATE DATABASE ${POSTGRES_DB} OWNER ${POSTGRES_USER}'
     WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${POSTGRES_DB}')\gexec" 2>/dev/null || \
    gosu postgres createdb -O "${POSTGRES_USER}" "${POSTGRES_DB}" 2>/dev/null || \
    echo "数据库 ${POSTGRES_DB} 已存在"

gosu postgres psql -c \
    "SELECT 'CREATE DATABASE ${TRAJ_PROXY_DB} OWNER ${POSTGRES_USER}'
     WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${TRAJ_PROXY_DB}')\gexec" 2>/dev/null || \
    gosu postgres createdb -O "${POSTGRES_USER}" "${TRAJ_PROXY_DB}" 2>/dev/null || \
    echo "数据库 ${TRAJ_PROXY_DB} 已存在"

# ========================================
# 第三阶段：初始化 traj_proxy 数据表
# ========================================
echo "--- 初始化 traj_proxy 数据表 ---"
python3 - <<'PYTHON_SCRIPT'
import os
import sys
import re
import psycopg

def init_database():
    """初始化数据库：创建表、索引"""
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("错误: 未设置 DATABASE_URL 环境变量")
        sys.exit(1)

    from urllib.parse import urlparse
    parsed = urlparse(db_url)
    db_name = parsed.path.lstrip("/")
    host = parsed.hostname
    port = parsed.port or 5432
    user = parsed.username
    password = parsed.password

    identifier_pattern = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    if not identifier_pattern.match(db_name):
        print(f"错误: 无效的数据库名称: {db_name}")
        sys.exit(1)
    if not identifier_pattern.match(user):
        print(f"错误: 无效的用户名称: {user}")
        sys.exit(1)

    print("创建表和索引...")
    try:
        with psycopg.connect(db_url, autocommit=True) as conn:
            # request_metadata 表
            print("  创建 request_metadata 表...")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS public.request_metadata (
                    id BIGSERIAL PRIMARY KEY,
                    unique_id TEXT NOT NULL UNIQUE,
                    request_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    run_id TEXT,
                    model TEXT NOT NULL,
                    prompt_tokens INTEGER,
                    completion_tokens INTEGER,
                    total_tokens INTEGER,
                    cache_hit_tokens INTEGER DEFAULT 0,
                    processing_duration_ms FLOAT,
                    start_time TIMESTAMP WITH TIME ZONE NOT NULL,
                    end_time TIMESTAMP WITH TIME ZONE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    error TEXT,
                    archive_location TEXT,
                    archived_at TIMESTAMP WITH TIME ZONE
                )
            """)

            # 兼容性：为 request_metadata 添加 run_id 列
            print("  检查 request_metadata 表列兼容性...")
            has_run_id = conn.execute("""
                SELECT EXISTS(SELECT 1 FROM information_schema.columns
                              WHERE table_schema='public'
                              AND table_name='request_metadata'
                              AND column_name='run_id')
            """).fetchone()[0]

            if not has_run_id:
                conn.execute("ALTER TABLE public.request_metadata ADD COLUMN run_id TEXT")
                print("  run_id 列添加完成")

            # request_metadata 索引
            conn.execute("CREATE INDEX IF NOT EXISTS request_metadata_session_id_idx ON public.request_metadata (session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS request_metadata_run_id_idx ON public.request_metadata (run_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS request_metadata_start_time_idx ON public.request_metadata (start_time DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS request_metadata_archive_location_idx ON public.request_metadata (archive_location) WHERE archive_location IS NOT NULL")

            # request_details_active 分区表
            print("  创建 request_details_active 分区表...")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS public.request_details_active (
                    id BIGSERIAL,
                    unique_id TEXT NOT NULL
                        REFERENCES public.request_metadata(unique_id) ON DELETE CASCADE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    tokenizer_path TEXT,
                    messages JSONB NOT NULL,
                    raw_request JSONB,
                    raw_response JSONB,
                    text_request JSONB,
                    text_response JSONB,
                    prompt_text TEXT,
                    token_ids INTEGER[],
                    token_request JSONB,
                    token_response JSONB,
                    response_text TEXT,
                    response_ids INTEGER[],
                    full_conversation_text TEXT,
                    full_conversation_token_ids INTEGER[],
                    error_traceback TEXT,
                    PRIMARY KEY (id, created_at)
                ) PARTITION BY RANGE (created_at)
            """)

            # 创建当月分区
            import datetime as _dt
            _now = _dt.datetime.now()
            _month_start = _now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if _now.month == 12:
                _next_month = _month_start.replace(year=_now.year + 1, month=1)
            else:
                _next_month = _month_start.replace(month=_now.month + 1)
            _partition_name = f"request_details_active_{_now.strftime('%Y_%m')}"
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS public.{_partition_name}
                    PARTITION OF public.request_details_active
                    FOR VALUES FROM ('{_month_start.isoformat()}') TO ('{_next_month.isoformat()}')
            """)

            # 默认分区
            conn.execute("""
                CREATE TABLE IF NOT EXISTS public.request_details_active_default
                    PARTITION OF public.request_details_active DEFAULT
            """)

            # request_details_active 索引
            conn.execute("CREATE INDEX IF NOT EXISTS request_details_active_unique_id_idx ON public.request_details_active (unique_id)")

            # model_registry 表
            print("  创建 model_registry 表...")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS public.model_registry (
                    id SERIAL PRIMARY KEY,
                    run_id TEXT,
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

            # model_registry 索引
            conn.execute("CREATE INDEX IF NOT EXISTS model_registry_run_id_idx ON public.model_registry (run_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS model_registry_model_name_idx ON public.model_registry (model_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS model_registry_updated_at_idx ON public.model_registry (updated_at DESC)")

            # 兼容性：为 model_registry 添加新列
            conn.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                                   WHERE table_schema='public' AND table_name='model_registry' AND column_name='tool_parser') THEN
                        ALTER TABLE public.model_registry ADD COLUMN tool_parser TEXT NOT NULL DEFAULT '';
                    END IF;
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                                   WHERE table_schema='public' AND table_name='model_registry' AND column_name='reasoning_parser') THEN
                        ALTER TABLE public.model_registry ADD COLUMN reasoning_parser TEXT NOT NULL DEFAULT '';
                    END IF;
                END $$;
            """)

            # tokenizer_path 改为可空
            conn.execute("""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema='public'
                        AND table_name='model_registry'
                        AND column_name='tokenizer_path'
                        AND is_nullable='NO'
                    ) THEN
                        ALTER TABLE public.model_registry ALTER COLUMN tokenizer_path DROP NOT NULL;
                    END IF;
                END $$;
            """)

            print("表和索引创建完成")
    except Exception as e:
        print(f"错误: 创建表失败: {e}")
        sys.exit(1)

if __name__ == "__main__":
    init_database()
PYTHON_SCRIPT

if [ $? -ne 0 ]; then
    echo "错误: 数据表初始化失败"
    gosu postgres /usr/lib/postgresql/16/bin/pg_ctl -D "${PGDATA}" stop -m fast 2>/dev/null || true
    exit 1
fi

echo "--- 数据表初始化完成 ---"

# ========================================
# 第四阶段：停止临时 PostgreSQL（supervisord 将接管）
# ========================================
echo "--- 停止临时 PostgreSQL ---"
gosu postgres /usr/lib/postgresql/16/bin/pg_ctl -D "${PGDATA}" stop -m fast

# ========================================
# 第五阶段：更新配置文件中的数据库连接（运行时覆盖）
# ========================================
echo "--- 更新配置文件 ---"
# 用运行时 DATABASE_URL 替换 config_allinone.yaml 中的硬编码值
CONFIG_FILE="/app/configs/config_allinone.yaml"
if [ -f "${CONFIG_FILE}" ]; then
    sed -i "s|postgresql://[^@]*@[^/]*/${TRAJ_PROXY_DB}|${DATABASE_URL}|g" "${CONFIG_FILE}"
fi

# ========================================
# 第六阶段：启动 supervisord
# ========================================
echo "=== 启动 supervisord（管理全部服务）==="
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/trajproxy.conf
