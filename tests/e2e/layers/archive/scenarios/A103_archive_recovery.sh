#!/bin/bash
# 场景 A103: 归档数据恢复（直接读文件）
# 验证归档文件可读、JSONL 格式正确、数据完整

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 A103: 归档数据恢复"
echo "========================================"
echo ""

TEST_SESSION="${TEST_SESSION_PREFIX}_A103"
TEST_RUN="test-run-a103"
MONTH_TARGET=$(date -d "2 months ago" +%Y_%m 2>/dev/null || date -v-2m +%Y_%m 2>/dev/null || echo "2026_03")

# 步骤 1: 准备归档数据
log_step "步骤 1: 准备归档数据"

if check_partition_exists "request_details_active_${MONTH_TARGET}"; then
    log_info "删除已存在的分区..."
    db_execute "ALTER TABLE public.request_details_active DETACH PARTITION public.request_details_active_${MONTH_TARGET};" 2>/dev/null
    db_execute "DROP TABLE IF EXISTS public.request_details_active_${MONTH_TARGET};" 2>/dev/null
fi

cleanup_test_data "${TEST_SESSION}"

create_partition "${MONTH_TARGET}"
insert_test_data "${MONTH_TARGET}" 5 "${TEST_SESSION}" "${TEST_RUN}"

PARTITION_COUNT=$(get_partition_record_count "request_details_active_${MONTH_TARGET}")
log_info "分区 ${MONTH_TARGET} 包含 ${PARTITION_COUNT} 条记录"

# 执行归档
ARCHIVE_OUTPUT=$(run_archive_once 1 2>&1)
log_info "归档输出:"
echo "$ARCHIVE_OUTPUT" | tail -3
sleep 2

# 获取 archive_location
ARCHIVE_LOCATION=$(get_archive_location "${TEST_SESSION}")
log_info "archive_location: ${ARCHIVE_LOCATION}"

if ! check_archive_file_exists "${ARCHIVE_LOCATION}"; then
    log_error "归档文件不存在，无法继续测试"
    print_summary
    exit 1
fi
log_success "归档文件存在"

# 步骤 2: 读取并验证 GZIP + JSON 格式
log_step "步骤 2: 验证 GZIP + JSON 格式"

FIRST_LINE=$(read_archive_first_line "${ARCHIVE_LOCATION}")

TESTS_TOTAL=$((TESTS_TOTAL + 1))
if [ -n "$FIRST_LINE" ]; then
    log_success "归档文件可读（GZIP 验证通过）"
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "无法读取归档文件"
    TESTS_FAILED=$((TESTS_FAILED + 1))
    print_summary
    exit 1
fi

TESTS_TOTAL=$((TESTS_TOTAL + 1))
if echo "$FIRST_LINE" | python3 -c "import sys, json; json.loads(sys.stdin.read())" 2>/dev/null; then
    log_success "JSON 格式有效"
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "JSON 格式无效"
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

# 步骤 3: 验证字段完整性
log_step "步骤 3: 验证数据字段完整性"

REQUIRED_FIELDS=("unique_id" "created_at" "messages")
for field in "${REQUIRED_FIELDS[@]}"; do
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    if echo "$FIRST_LINE" | grep -q "\"${field}\""; then
        log_success "字段存在: ${field}"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        log_error "字段缺失: ${field}"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
done

# 步骤 4: 验证文件记录数
log_step "步骤 4: 验证归档文件记录数"
verify_archive_file "${ARCHIVE_LOCATION}" 5

# 步骤 5: 验证 unique_id 与数据库一致
log_step "步骤 5: 验证 unique_id 与数据库一致"

UNIQUE_ID=$(echo "$FIRST_LINE" | python3 -c "
import sys, json
data = json.loads(sys.stdin.read())
print(data.get('unique_id', ''))
" 2>/dev/null)

DB_UNIQUE_ID=$(db_query "SELECT unique_id FROM request_metadata WHERE unique_id = '${UNIQUE_ID}';")

TESTS_TOTAL=$((TESTS_TOTAL + 1))
if [ "$UNIQUE_ID" = "$DB_UNIQUE_ID" ]; then
    log_success "归档文件 unique_id 与数据库元数据匹配"
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "归档文件 unique_id 与数据库不匹配"
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

# 步骤 6: 验证 archive_location 格式
log_step "步骤 6: 验证 archive_location 格式"

EXPECTED_PATTERN="${TEST_RUN}/${TEST_SESSION}"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
if echo "$ARCHIVE_LOCATION" | grep -q "${EXPECTED_PATTERN}"; then
    log_success "archive_location 格式: {run_id}/{session_id}.jsonl.gz"
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "archive_location 格式不正确: ${ARCHIVE_LOCATION}"
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

# 步骤 7: 清理
log_step "步骤 7: 清理测试数据"
cleanup_test_data "${TEST_SESSION}"

echo ""
print_summary
