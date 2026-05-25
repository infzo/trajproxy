#!/bin/bash
# 场景 A100: 归档进程配置验证
# 验证独立归档进程正确启动，核心业务与归档解耦

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 A100: 归档进程配置验证"
echo "========================================"
echo ""

# 步骤 1: 验证归档容器运行中
log_step "步骤 1: 验证归档容器运行状态"

if docker ps --format '{{.Names}}' | grep -q "^${ARCHIVER_CONTAINER_NAME}$"; then
    log_success "归档容器运行中: ${ARCHIVER_CONTAINER_NAME}"
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "归档容器未运行: ${ARCHIVER_CONTAINER_NAME}"
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi
TESTS_TOTAL=$((TESTS_TOTAL + 1))

# 步骤 2: 检查归档进程启动日志
log_step "步骤 2: 检查归档进程启动日志"

if archiver_log_contains "TrajArchiver 已启动"; then
    log_success "归档进程已启动"
    TESTS_PASSED=$((TESTS_PASSED + 1))
elif archiver_log_contains "ArchiveScheduler 已启动"; then
    log_success "归档调度器已启动"
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "归档进程未启动"
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi
TESTS_TOTAL=$((TESTS_TOTAL + 1))

# 步骤 3: 验证轮询间隔和留存配置
log_step "步骤 3: 验证调度器配置"

if archiver_log_contains "poll_interval"; then
    POLL_INTERVAL=$(search_archiver_logs "poll_interval" | tail -1 | sed 's/.*poll_interval: //' | sed 's/[^0-9].*//')
    log_success "轮询间隔: ${POLL_INTERVAL}s"
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "未找到轮询间隔配置"
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi
TESTS_TOTAL=$((TESTS_TOTAL + 1))

if archiver_log_contains "retention_days"; then
    RETENTION=$(search_archiver_logs "retention_days" | tail -1 | sed 's/.*retention_days: //')
    log_success "保留天数: ${RETENTION}"
fi

# 步骤 4: 验证核心业务容器不包含归档代码
log_step "步骤 4: 验证核心业务与归档解耦"

if proxy_log_contains "ArchiveScheduler"; then
    log_error "核心业务容器仍包含归档调度器代码"
    TESTS_FAILED=$((TESTS_FAILED + 1))
else
    log_success "核心业务容器不包含归档代码"
    TESTS_PASSED=$((TESTS_PASSED + 1))
fi
TESTS_TOTAL=$((TESTS_TOTAL + 1))

# 步骤 5: 检查数据库分区状态
log_step "步骤 5: 检查数据库分区状态"

PARTITIONS=$(db_query "
    SELECT COUNT(*) FROM pg_class pt
    JOIN pg_inherits i ON pt.oid = i.inhparent
    JOIN pg_class pp ON i.inhrelid = pp.oid
    WHERE pt.relname = 'request_details_active'
    AND pt.relnamespace = 'public'::regnamespace;
")

if [ -n "$PARTITIONS" ] && [ "$PARTITIONS" -gt 0 ]; then
    log_success "找到 ${PARTITIONS} 个分区"
else
    log_error "未找到任何分区"
fi

echo ""
print_summary
