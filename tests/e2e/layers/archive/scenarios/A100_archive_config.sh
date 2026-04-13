#!/bin/bash
# 场景 A100: 归档配置验证
# 验证归档配置正确加载，调度器正常启动

# 获取脚本目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 A100: 归档配置验证"
echo "========================================"
echo ""

# 步骤 1: 检查调度器启动日志
log_step "步骤 1: 检查归档调度器启动日志"

LOGS=$(get_container_logs 2>&1)

# 调试：显示日志行数
log_info "获取到 $(echo "$LOGS" | wc -l) 行日志"

# 检查是否包含归档调度器启动信息
if echo "$LOGS" | grep -q "ArchiveScheduler 已启动"; then
    log_success "归档调度器已启动"
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "归档调度器未启动"
    # 调试：显示相关日志行
    log_info "查找 'scheduler' 相关日志:"
    echo "$LOGS" | grep -i "scheduler" | head -10 || log_info "未找到 scheduler 相关日志"
    echo "提示: 请确保 config.yaml 中 archive.enabled=true"
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi
TESTS_TOTAL=$((TESTS_TOTAL + 1))

# 步骤 2: 验证调度器配置
log_step "步骤 2: 验证调度器配置"

SCHEDULER_FOUND=false
if echo "$LOGS" | grep -q "schedule:"; then
    SCHEDULE=$(echo "$LOGS" | grep "schedule:" | tail -1 | sed 's/.*schedule: //')
    log_success "调度配置: $SCHEDULE"
    TESTS_PASSED=$((TESTS_PASSED + 1))
    SCHEDULER_FOUND=true
else
    log_error "未找到调度配置"
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi
TESTS_TOTAL=$((TESTS_TOTAL + 1))

if echo "$LOGS" | grep -q "retention_days:"; then
    RETENTION=$(echo "$LOGS" | grep "retention_days:" | tail -1 | sed 's/.*retention_days: //')
    log_success "保留天数: $RETENTION"
fi

if echo "$LOGS" | grep -q "storage_path:"; then
    STORAGE=$(echo "$LOGS" | grep "storage_path:" | tail -1 | sed 's/.*storage_path: //')
    log_success "存储路径: $STORAGE"
fi

# 步骤 3: 验证归档目录存在
log_step "步骤 3: 验证归档目录"

if exec_in_container "[ -d ${ARCHIVE_STORAGE_PATH} ]" 2>/dev/null; then
    log_success "归档目录存在: ${ARCHIVE_STORAGE_PATH}"
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "归档目录不存在: ${ARCHIVE_STORAGE_PATH}"
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi
TESTS_TOTAL=$((TESTS_TOTAL + 1))

# 步骤 4: 检查分区状态
log_step "步骤 4: 检查数据库分区状态"

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

# 打印测试摘要
print_summary
