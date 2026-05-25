#!/bin/bash
# Archive 层测试工具函数
# 适配独立归档进程 traj_archiver + S3 存储

# 导入配置
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/config.sh"

# 导入公共工具函数
source "${SCRIPT_DIR}/../../utils.sh"

# ========================================
# 数据库操作函数
# ========================================

# 执行 SQL 查询（在数据库容器内运行）
db_query() {
    local sql="$1"
    echo "$sql" | docker exec -i "${DB_CONTAINER_NAME}" \
        env PGPASSWORD="${ARCHIVE_DB_PASSWORD}" psql \
        -h "${ARCHIVE_DB_HOST}" -p "${ARCHIVE_DB_PORT}" \
        -U "${ARCHIVE_DB_USER}" -d "${ARCHIVE_DB_NAME}" \
        -t -A
}

# 执行 SQL 命令（无输出）
db_execute() {
    local sql="$1"
    echo "$sql" | docker exec -i "${DB_CONTAINER_NAME}" \
        env PGPASSWORD="${ARCHIVE_DB_PASSWORD}" psql \
        -h "${ARCHIVE_DB_HOST}" -p "${ARCHIVE_DB_PORT}" \
        -U "${ARCHIVE_DB_USER}" -d "${ARCHIVE_DB_NAME}" \
        -q
}

# ========================================
# 测试数据生成函数
# ========================================

# 创建指定月份的分区
create_partition() {
    local year_month="$1"  # 格式: YYYY_MM
    local year="${year_month:0:4}"
    local month="${year_month:5:2}"

    local next_month=$((10#$month + 1))
    local next_year=$year
    if [ $next_month -gt 12 ]; then
        next_month=1
        next_year=$((year + 1))
    fi

    local month_start="${year}-${month}-01"
    local next_month_start="${next_year}-$(printf '%02d' $next_month)-01"

    local partition_name="request_details_active_${year_month}"

    db_execute "
        CREATE TABLE IF NOT EXISTS public.${partition_name}
            PARTITION OF public.request_details_active
            FOR VALUES FROM ('${month_start}') TO ('${next_month_start}');
    "

    log_info "已创建分区: ${partition_name}"
}

# 插入测试数据到指定分区
insert_test_data() {
    local year_month="$1"
    local count="${2:-10}"
    local session_id="${3:-${TEST_SESSION_PREFIX}}"

    local year="${year_month:0:4}"
    local month="${year_month:5:2}"
    local created_at="${year}-${month}-15 10:00:00"

    for i in $(seq 1 $count); do
        local unique_id="${session_id}_req_${i}"
        local request_id="req_${i}"

        db_execute "
            INSERT INTO request_metadata (
                unique_id, request_id, session_id, model,
                prompt_tokens, completion_tokens, total_tokens,
                start_time, end_time, created_at
            ) VALUES (
                '${unique_id}', '${request_id}', '${session_id}', '${TEST_MODEL_NAME}',
                100, 50, 150,
                '${created_at}', '${created_at}', '${created_at}'
            )
            ON CONFLICT (unique_id) DO NOTHING;
        "

        db_execute "
            INSERT INTO request_details_active (
                unique_id, created_at, messages
            ) VALUES (
                '${unique_id}', '${created_at}', '[{\"role\": \"user\", \"content\": \"test message\"}]'::jsonb
            )
            ON CONFLICT DO NOTHING;
        "
    done

    log_info "已插入 ${count} 条测试数据到分区 ${year_month}"
}

# 清理测试数据
cleanup_test_data() {
    local session_id="${1:-${TEST_SESSION_PREFIX}}"

    db_execute "DELETE FROM request_details_active WHERE unique_id LIKE '${session_id}%';"
    db_execute "DELETE FROM request_metadata WHERE session_id LIKE '${session_id}%';"

    log_info "已清理测试数据: ${session_id}"
}

# ========================================
# 归档验证函数
# ========================================

# 检查分区是否存在
check_partition_exists() {
    local partition_name="$1"
    local result=$(db_query "
        SELECT COUNT(*) FROM pg_class
        WHERE relname = '${partition_name}'
        AND relnamespace = 'public'::regnamespace;
    ")
    [ "$result" -gt 0 ]
}

# 获取分区记录数
get_partition_record_count() {
    local partition_name="$1"
    db_query "SELECT COUNT(*) FROM public.${partition_name};" 2>/dev/null || echo "0"
}

# 获取已归档记录数
get_archived_record_count() {
    local session_id="$1"
    db_query "
        SELECT COUNT(*) FROM request_metadata
        WHERE session_id LIKE '${session_id}%'
        AND archive_location IS NOT NULL;
    "
}

# 获取归档记录的 archive_location
get_archive_location() {
    local session_id="$1"
    db_query "
        SELECT archive_location FROM request_metadata
        WHERE session_id LIKE '${session_id}%'
        AND archive_location IS NOT NULL
        LIMIT 1;
    "
}

# 检查 S3 上是否存在归档文件（通过归档容器执行）
check_s3_archive_exists() {
    local s3_key="$1"
    docker exec "${ARCHIVER_CONTAINER_NAME}" python3 -c "
import boto3, os
s3 = boto3.client('s3',
    endpoint_url=os.environ.get('AWS_ENDPOINT_URL'),
)
key = '${ARCHIVE_S3_PREFIX}${s3_key}'
try:
    s3.head_object(Bucket='${ARCHIVE_S3_BUCKET}', Key=key)
    print('exists')
except:
    print('not_found')
" 2>/dev/null
}

# 从 S3 下载归档文件到归档容器的临时目录，并统计记录数
verify_s3_archive_file() {
    local s3_key="$1"
    local expected_count="$2"

    # 在归档容器内下载并统计
    local actual_count=$(docker exec "${ARCHIVER_CONTAINER_NAME}" python3 -c "
import boto3, gzip, os, sys
s3 = boto3.client('s3',
    endpoint_url=os.environ.get('AWS_ENDPOINT_URL'),
)
key = '${ARCHIVE_S3_PREFIX}${s3_key}'
local_path = '/tmp/test_verify_${s3_key}'
s3.download_file('${ARCHIVE_S3_BUCKET}', key, local_path)
with gzip.open(local_path, 'rt') as f:
    count = sum(1 for _ in f)
os.unlink(local_path)
print(count)
" 2>/dev/null)

    if [ "$actual_count" -ne "$expected_count" ]; then
        log_error "归档文件记录数不匹配: 期望 ${expected_count}, 实际 ${actual_count}"
        return 1
    fi

    log_success "归档文件验证通过: ${s3_key} (${actual_count} 条记录)"
    return 0
}

# 从 S3 下载归档文件并读取第一行
read_s3_archive_first_line() {
    local s3_key="$1"
    docker exec "${ARCHIVER_CONTAINER_NAME}" python3 -c "
import boto3, gzip, json, os
s3 = boto3.client('s3',
    endpoint_url=os.environ.get('AWS_ENDPOINT_URL'),
)
key = '${ARCHIVE_S3_PREFIX}${s3_key}'
local_path = '/tmp/test_read_${s3_key}'
s3.download_file('${ARCHIVE_S3_BUCKET}', key, local_path)
with gzip.open(local_path, 'rt') as f:
    print(f.readline().strip())
os.unlink(local_path)
" 2>/dev/null
}

# ========================================
# 归档执行函数
# ========================================

# 在归档容器内执行一次性归档（不启动调度器）
run_archive_once() {
    local retention_days="${1:-${ARCHIVE_RETENTION_DAYS}}"

    docker exec "${ARCHIVER_CONTAINER_NAME}" python3 -c "
import asyncio, sys
sys.path.insert(0, '/app')

from psycopg_pool import AsyncConnectionPool
from traj_archiver.config import get_database_url, get_s3_config, get_archive_config, get_database_pool_config
from traj_archiver.s3_storage import S3Storage
from traj_archiver.archiver import archive_details

async def main():
    db_url = get_database_url()
    pool_config = get_database_pool_config()
    s3_config = get_s3_config()

    pool = AsyncConnectionPool(conninfo=db_url, **pool_config)
    await pool.open()

    s3 = S3Storage(
        bucket=s3_config.get('bucket', ''),
        prefix=s3_config.get('prefix', ''),
        endpoint_url=s3_config.get('endpoint_url'),
    )

    result = await archive_details(
        pool=pool,
        s3_storage=s3,
        local_temp_path='/tmp/archives',
        retention_days=${retention_days},
    )
    await pool.close()

    print(f'records_archived={result[\"records_archived\"]}')
    print(f'partitions_dropped={result[\"partitions_dropped\"]}')
    print(f'errors={len(result[\"errors\"])}')

asyncio.run(main())
" 2>&1
}

# ========================================
# 容器操作函数
# ========================================

# 获取归档容器日志
get_archiver_logs() {
    docker logs "${ARCHIVER_CONTAINER_NAME}" 2>&1
}

# 在归档容器日志中搜索特定模式
search_archiver_logs() {
    local pattern="$1"
    docker logs "${ARCHIVER_CONTAINER_NAME}" 2>&1 | grep -E "$pattern"
}

# 检查归档容器日志中是否包含特定模式
archiver_log_contains() {
    local pattern="$1"
    docker logs "${ARCHIVER_CONTAINER_NAME}" 2>&1 | grep -qE "$pattern"
}

# 获取业务容器日志（用于验证核心业务不包含归档代码）
get_proxy_logs() {
    docker logs "${PROXY_CONTAINER_NAME}" 2>&1
}

# 检查业务容器日志中是否包含归档相关内容
proxy_log_contains() {
    local pattern="$1"
    docker logs "${PROXY_CONTAINER_NAME}" 2>&1 | grep -qE "$pattern"
}
