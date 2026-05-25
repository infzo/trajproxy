#!/bin/bash
# 场景 A101: 手动触发归档
# 测试在归档容器内手动执行一次性归档的完整流程

# 获取脚本目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 A101: 手动触发归档"
echo "========================================"
echo ""

# 生成测试 session ID
TEST_SESSION="${TEST_SESSION_PREFIX}_A101"
MONTH_LAST=$(date -d "last month" +%Y_%m 2>/dev/null || date -v-1m +%Y_%m 2>/dev/null || echo "2026_03")

# 步骤 1: 创建过期分区并插入测试数据
log_step "步骤 1: 创建过期分区并插入测试数据"

create_partition "${MONTH_LAST}"
insert_test_data "${MONTH_LAST}" 10 "${TEST_SESSION}"

RECORD_COUNT=$(get_partition_record_count "request_details_active_${MONTH_LAST}")
log_info "分区 ${MONTH_LAST} 包含 ${RECORD_COUNT} 条记录"

# 步骤 2: 验证数据已插入
log_step "步骤 2: 验证数据已插入"

METADATA_COUNT=$(db_query "
    SELECT COUNT(*) FROM request_metadata
    WHERE session_id = '${TEST_SESSION}';
")

assert_eq "10" "$METADATA_COUNT" "元数据记录数应为 10"

# 步骤 3: 在归档容器内执行一次性归档
log_step "步骤 3: 执行归档（retention_days=1）"

ARCHIVE_OUTPUT=$(run_archive_once 1 2>&1)
log_info "归档输出:"
echo "$ARCHIVE_OUTPUT" | tail -5

# 步骤 4: 验证分区已被删除
log_step "步骤 4: 验证分区已被删除"

sleep 2

if check_partition_exists "request_details_active_${MONTH_LAST}"; then
    log_error "分区仍然存在（应已被删除）"
    TESTS_FAILED=$((TESTS_FAILED + 1))
else
    log_success "分区已删除: request_details_active_${MONTH_LAST}"
    TESTS_PASSED=$((TESTS_PASSED + 1))
fi
TESTS_TOTAL=$((TESTS_TOTAL + 1))

# 步骤 5: 验证元数据已更新为 S3 URI
log_step "步骤 5: 验证元数据 archive_location 为 S3 URI"

ARCHIVED_COUNT=$(get_archived_record_count "${TEST_SESSION}")
log_info "已归档记录数: ${ARCHIVED_COUNT}"

ARCHIVE_LOCATION=$(get_archive_location "${TEST_SESSION}")
log_info "archive_location: ${ARCHIVE_LOCATION}"

TESTS_TOTAL=$((TESTS_TOTAL + 1))
if echo "$ARCHIVE_LOCATION" | grep -q "^s3://"; then
    log_success "archive_location 为 S3 URI: ${ARCHIVE_LOCATION}"
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "archive_location 不是 S3 URI: ${ARCHIVE_LOCATION}"
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

# 步骤 6: 验证 S3 上的归档文件
log_step "步骤 6: 验证 S3 归档文件"

ARCHIVE_FILE="${MONTH_LAST}.jsonl.gz"

S3_EXISTS=$(check_s3_archive_exists "${ARCHIVE_FILE}")
if [ "$S3_EXISTS" = "exists" ]; then
    log_success "S3 归档文件存在: ${ARCHIVE_FILE}"
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "S3 归档文件不存在: ${ARCHIVE_FILE}"
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi
TESTS_TOTAL=$((TESTS_TOTAL + 1))

# 步骤 7: 验证归档文件内容
log_step "步骤 7: 验证归档文件内容"

verify_s3_archive_file "${ARCHIVE_FILE}" 10

# 步骤 8: 清理测试数据
log_step "步骤 8: 清理测试数据"

cleanup_test_data "${TEST_SESSION}"

echo ""

# 打印测试摘要
print_summary
