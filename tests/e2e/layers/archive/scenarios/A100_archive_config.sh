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

# 直接在全部日志中搜索启动消息（避免启动日志被挤出）
if log_contains "ArchiveScheduler 已启动"; then
    log_success "归档调度器已启动"
    TESTS_PASSED=$((TESTS_PASSED + 1))
elif log_contains "归档调度器未启用"; then
    log_error "归档调度器未启用（archive.enabled=false）"
    TESTS_FAILED=$((TESTS_FAILED + 1))
else
    log_error "归档调度器未启动"
    # 调试：显示相关日志行
    log_info "查找 'scheduler' 相关日志:"
    search_container_logs "scheduler" | head -10 || log_info "未找到 scheduler 相关日志"
    echo "提示: 请确保 config.yaml 中 archive.enabled=true"
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi
TESTS_TOTAL=$((TESTS_TOTAL + 1))

# 步骤 2: 验证调度器配置
log_step "步骤 2: 验证调度器配置"

SCHEDULER_FOUND=false
if log_contains "schedule:"; then
    SCHEDULE=$(search_container_logs "schedule:" | tail -1 | sed 's/.*schedule: //')
    log_success "调度配置: $SCHEDULE"
    TESTS_PASSED=$((TESTS_PASSED + 1))
    SCHEDULER_FOUND=true
else
    log_error "未找到调度配置"
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi
TESTS_TOTAL=$((TESTS_TOTAL + 1))

if log_contains "retention_days:"; then
    RETENTION=$(search_container_logs "retention_days:" | tail -1 | sed 's/.*retention_days: //')
    log_success "保留天数: $RETENTION"
fi

if log_contains "storage_path:"; then
    STORAGE=$(search_container_logs "storage_path:" | tail -1 | sed 's/.*storage_path: //')
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
