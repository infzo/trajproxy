#!/bin/bash
# 场景 A101: 手动触发归档
# 测试手动执行归档脚本的完整流程

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

# 计算上个月的分区名（模拟过期数据）
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

# 步骤 3: 执行归档（设置极短的保留天数，确保分区过期）
log_step "步骤 3: 执行归档脚本"

# 设置保留天数为 1 天，确保上月数据过期
ARCHIVE_OUTPUT=$(run_archive_script 1 false 2>&1)
log_info "归档脚本输出:"
echo "$ARCHIVE_OUTPUT" | head -20

# 步骤 4: 验证分区已被删除
log_step "步骤 4: 验证分区已被删除"

sleep 2

if check_partition_exists "request_details_active_${MONTH_LAST}"; then
    log_error "分区仍然存在（应已被删除）"
else
    log_success "分区已删除: request_details_active_${MONTH_LAST}"
fi

# 步骤 5: 验证归档文件生成
log_step "步骤 5: 验证归档文件生成"

ARCHIVE_FILE="${MONTH_LAST}.jsonl.gz"

if check_archive_file_exists "${ARCHIVE_FILE}"; then
    log_success "归档文件已生成: ${ARCHIVE_FILE}"
else
    log_error "归档文件未生成: ${ARCHIVE_FILE}"
fi

# 步骤 6: 验证元数据已更新
log_step "步骤 6: 验证元数据已更新"

ARCHIVED_COUNT=$(get_archived_record_count "${TEST_SESSION}")
log_info "已归档记录数: ${ARCHIVED_COUNT}"

ARCHIVE_LOCATION=$(db_query "
    SELECT archive_location FROM request_metadata
    WHERE session_id = '${TEST_SESSION}' LIMIT 1;
")

assert_eq "${ARCHIVE_FILE}" "$ARCHIVE_LOCATION" "archive_location 应为归档文件名"

# 步骤 7: 验证归档文件内容
log_step "步骤 7: 验证归档文件内容"

verify_archive_file "${ARCHIVE_FILE}" 10

# 步骤 8: 清理测试数据
log_step "步骤 8: 清理测试数据"

cleanup_test_data "${TEST_SESSION}"

echo ""

# 打印测试摘要
print_summary