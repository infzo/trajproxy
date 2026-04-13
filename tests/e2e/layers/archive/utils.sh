#!/bin/bash
# Archive 层测试工具函数

# 导入配置
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/config.sh"

# 导入公共工具函数
source "${SCRIPT_DIR}/../../utils.sh"

# ========================================
# 数据库操作函数
# ========================================

# 执行 SQL 查询（在数据库容器内运行，通过 stdin 传递避免引号转义问题）
db_query() {
    local sql="$1"
    echo "$sql" | docker exec -i "${DB_CONTAINER_NAME}" \
        env PGPASSWORD="${ARCHIVE_DB_PASSWORD}" psql \
        -h "${ARCHIVE_DB_HOST}" -p "${ARCHIVE_DB_PORT}" \
        -U "${ARCHIVE_DB_USER}" -d "${ARCHIVE_DB_NAME}" \
        -t -A
}

# 执行 SQL 命令（无输出，在数据库容器内运行）
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

        # 插入元数据
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

        # 插入详情
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

# 检查归档文件是否存在（在容器内检查）
check_archive_file_exists() {
    local archive_file="${ARCHIVE_STORAGE_PATH}/${1}"
    # 在应用容器内检查文件是否存在
    docker exec "${CONTAINER_NAME}" test -f "${archive_file}" 2>/dev/null
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

# 验证归档文件内容（在容器内检查）
verify_archive_file() {
    local archive_file="${ARCHIVE_STORAGE_PATH}/${1}"
    local expected_count="$2"

    # 检查文件是否存在于容器内
    if ! docker exec "${CONTAINER_NAME}" test -f "${archive_file}" 2>/dev/null; then
        log_error "归档文件不存在: ${archive_file}"
        return 1
    fi

    # 在容器内统计记录数
    local actual_count=$(docker exec "${CONTAINER_NAME}" bash -c "zcat '${archive_file}' | wc -l" 2>/dev/null)
    if [ "$actual_count" -ne "$expected_count" ]; then
        log_error "归档文件记录数不匹配: 期望 ${expected_count}, 实际 ${actual_count}"
        return 1
    fi

    log_success "归档文件验证通过: ${archive_file} (${actual_count} 条记录)"
    return 0
}

# ========================================
# 容器操作函数
# ========================================

# 在容器中执行命令
exec_in_container() {
    local cmd="$1"
    docker exec "${CONTAINER_NAME}" bash -c "$cmd"
}

# 执行归档脚本
run_archive_script() {
    local retention_days="${1:-${ARCHIVE_RETENTION_DAYS}}"
    local dry_run="${2:-false}"

    local dry_run_flag=""
    if [ "$dry_run" = "true" ]; then
        dry_run_flag="--dry-run"
    fi

    exec_in_container "python /app/scripts/archive_records.py \
        --retention-days ${retention_days} \
        --archive-dir ${ARCHIVE_STORAGE_PATH} \
        ${dry_run_flag}"
}

# 获取容器日志
get_container_logs() {
    docker logs "${CONTAINER_NAME}" --tail 100
}

# 检查调度器是否启动
check_scheduler_started() {
    local logs=$(get_container_logs)
    if echo "$logs" | grep -q "ArchiveScheduler 已启动"; then
        return 0
    fi
    return 1
}
