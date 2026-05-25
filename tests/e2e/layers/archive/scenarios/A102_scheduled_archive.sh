#!/bin/bash
# 场景 A102: 归档 — 未过期 run 不被归档
# 验证 run 内有活跃记录时整个 run 保留不动

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 A102: 未过期 run 不被归档"
echo "========================================"
echo ""

TEST_SESSION="${TEST_SESSION_PREFIX}_A102"
TEST_RUN="test-run-a102"
# 使用当前月份，数据在 retention 期内
MONTH_CURRENT=$(date +%Y_%m 2>/dev/null || echo "2026_05")

log_step "步骤 1: 创建当前月分区并插入测试数据"

create_partition "${MONTH_CURRENT}"
insert_test_data "${MONTH_CURRENT}" 5 "${TEST_SESSION}" "${TEST_RUN}"

BEFORE=$(get_partition_record_count "request_details_active_${MONTH_CURRENT}")
log_info "分区 ${MONTH_CURRENT} 包含 ${BEFORE} 条记录"

# 步骤 2: 执行归档（留存30天，当前数据不应被归档）
log_step "步骤 2: 执行归档（retention_days=30）"

ARCHIVE_OUTPUT=$(run_archive_once 30 2>&1)
log_info "归档输出:"
echo "$ARCHIVE_OUTPUT" | tail -5

# 步骤 3: 验证数据未被删除
log_step "步骤 3: 验证活跃 run 未被归档"

sleep 1

AFTER=$(get_partition_record_count "request_details_active_${MONTH_CURRENT}")
assert_eq "${BEFORE}" "${AFTER}" "活跃 run 的数据未被 DELETE"

# 步骤 4: 验证 archive_location 为空
log_step "步骤 4: 验证 archive_location 未设置"

ARCHIVE_LOCATION=$(get_archive_location "${TEST_SESSION}")

TESTS_TOTAL=$((TESTS_TOTAL + 1))
if [ -z "$ARCHIVE_LOCATION" ]; then
    log_success "活跃 run 的 archive_location 为 NULL（未归档）"
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "archive_location 不应被设置: ${ARCHIVE_LOCATION}"
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

# 步骤 5: 清理
log_step "步骤 5: 清理测试数据"
cleanup_test_data "${TEST_SESSION}"

echo ""
print_summary
