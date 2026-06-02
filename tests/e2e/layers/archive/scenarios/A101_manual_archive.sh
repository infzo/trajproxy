#!/bin/bash
# 场景 A101: 手动触发归档（按 run_id 粒度）
# 验证 run 内全部记录过期后才归档，格式 {run_id}/{session_id}.jsonl.gz

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 A101: 手动触发归档"
echo "========================================"
echo ""

TEST_SESSION="${TEST_SESSION_PREFIX}_A101"
TEST_RUN="test-run-a101"
MONTH_LAST=$(date -d "last month" +%Y_%m 2>/dev/null || date -v-1m +%Y_%m 2>/dev/null || echo "2026_04")

# 步骤 1: 创建过期分区并插入测试数据
log_step "步骤 1: 创建过期分区并插入测试数据"

create_partition "${MONTH_LAST}"
insert_test_data "${MONTH_LAST}" 10 "${TEST_SESSION}" "${TEST_RUN}"

RECORD_COUNT=$(get_partition_record_count "request_details_active_${MONTH_LAST}")
log_info "分区 ${MONTH_LAST} 包含 ${RECORD_COUNT} 条记录"

# 步骤 2: 验证数据已插入
log_step "步骤 2: 验证数据已插入"

METADATA_COUNT=$(db_query "
    SELECT COUNT(*) FROM request_metadata
    WHERE session_id = '${TEST_SESSION}';
")
assert_eq "10" "$METADATA_COUNT" "元数据记录数应为 10"

# 步骤 3: 执行归档（留存1天，上月数据全部过期）
log_step "步骤 3: 执行归档（retention_days=1）"

ARCHIVE_OUTPUT=$(run_archive_once 1 2>&1)
log_info "归档输出:"
echo "$ARCHIVE_OUTPUT" | tail -5

# 步骤 4: 验证分区数据被删除
log_step "步骤 4: 验证数据已 DELETE"

sleep 0.3

REMAINING=$(get_partition_record_count "request_details_active_${MONTH_LAST}")
assert_eq "0" "$REMAINING" "分区记录数应为 0（已全部 DELETE）"

# 步骤 5: 验证 archive_location 格式（文件夹路径，含 run_id）
log_step "步骤 5: 验证 archive_location 格式"

ARCHIVED_COUNT=$(get_archived_record_count "${TEST_SESSION}")
log_info "已归档记录数: ${ARCHIVED_COUNT}"

ARCHIVE_LOCATION=$(get_archive_location "${TEST_SESSION}")
log_info "archive_location: ${ARCHIVE_LOCATION}"

TESTS_TOTAL=$((TESTS_TOTAL + 1))
if echo "$ARCHIVE_LOCATION" | grep -q "${TEST_RUN}"; then
    log_success "archive_location 格式正确: 包含 ${TEST_RUN}"
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "archive_location 格式错误: ${ARCHIVE_LOCATION}"
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

# 步骤 6: 验证归档文件存在且内容正确
log_step "步骤 6: 验证归档文件"

# archive_location 是文件夹路径，拼接 session 文件名得到实际文件
ARCHIVE_FILE=$(build_archive_file_path "${ARCHIVE_LOCATION}" "${TEST_SESSION}")

if check_archive_file_exists "${ARCHIVE_FILE}"; then
    log_success "归档文件存在"
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "归档文件不存在"
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi
TESTS_TOTAL=$((TESTS_TOTAL + 1))

verify_archive_file "${ARCHIVE_FILE}" 10

# 步骤 7: 清理
log_step "步骤 7: 清理测试数据"
cleanup_test_data "${TEST_SESSION}"

echo ""
print_summary
