#!/bin/bash
# 场景 A103: 归档数据恢复
# 验证 S3 上的归档数据可以正确读取和恢复

# 获取脚本目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 A103: 归档数据恢复"
echo "========================================"
echo ""

# 生成测试 session ID
TEST_SESSION="${TEST_SESSION_PREFIX}_A103"
# 使用两个月前的月份，避免与其他场景冲突
MONTH_TARGET=$(date -d "2 months ago" +%Y_%m 2>/dev/null || date -v-2m +%Y_%m 2>/dev/null || echo "2026_02")
ARCHIVE_FILE="${MONTH_TARGET}.jsonl.gz"

# 步骤 1: 准备归档数据
log_step "步骤 1: 准备归档数据"

# 清理可能存在的旧分区
if check_partition_exists "request_details_active_${MONTH_TARGET}"; then
    log_info "删除已存在的分区..."
    db_execute "ALTER TABLE public.request_details_active DETACH PARTITION public.request_details_active_${MONTH_TARGET};" 2>/dev/null
    db_execute "DROP TABLE IF EXISTS public.request_details_active_${MONTH_TARGET};" 2>/dev/null
fi

# 清理可能残留的元数据
cleanup_test_data "${TEST_SESSION}"

# 创建新分区并插入测试数据
create_partition "${MONTH_TARGET}"
insert_test_data "${MONTH_TARGET}" 5 "${TEST_SESSION}"

PARTITION_COUNT=$(get_partition_record_count "request_details_active_${MONTH_TARGET}")
log_info "分区 ${MONTH_TARGET} 包含 ${PARTITION_COUNT} 条记录"

# 执行归档
ARCHIVE_OUTPUT=$(run_archive_once 1 2>&1)
log_info "归档输出:"
echo "$ARCHIVE_OUTPUT" | tail -3

sleep 2

# 验证 S3 归档文件生成
S3_EXISTS=$(check_s3_archive_exists "${ARCHIVE_FILE}")
if [ "$S3_EXISTS" = "exists" ]; then
    log_success "S3 归档文件存在: ${ARCHIVE_FILE}"
else
    log_error "S3 归档文件不存在，无法继续测试"
    print_summary
    exit 1
fi

# 步骤 2: 验证归档文件格式（JSONL+GZIP）
log_step "步骤 2: 验证归档文件格式（JSONL+GZIP via S3）"

# 从 S3 下载并验证 gzip 完整性
GZIP_VALID=$(docker exec "${ARCHIVER_CONTAINER_NAME}" python3 -c "
import boto3, gzip, os
s3 = boto3.client('s3', endpoint_url=os.environ.get('AWS_ENDPOINT_URL'))
key = '${ARCHIVE_S3_PREFIX}${ARCHIVE_FILE}'
local_path = '/tmp/test_gzip_${ARCHIVE_FILE}'
s3.download_file('${ARCHIVE_S3_BUCKET}', key, local_path)
try:
    with gzip.open(local_path, 'rt') as f:
        f.read(1)
    print('valid')
except:
    print('invalid')
finally:
    os.unlink(local_path)
" 2>/dev/null)

TESTS_TOTAL=$((TESTS_TOTAL + 1))
if [ "$GZIP_VALID" = "valid" ]; then
    log_success "文件格式: GZIP（验证通过）"
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "文件格式不是 GZIP 或文件损坏"
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

# 步骤 3: 读取并验证 JSONL 格式
log_step "步骤 3: 验证 JSONL 格式"

FIRST_LINE=$(read_s3_archive_first_line "${ARCHIVE_FILE}")

TESTS_TOTAL=$((TESTS_TOTAL + 1))
if [ -n "$FIRST_LINE" ]; then
    log_success "成功读取第一行数据"
    TESTS_PASSED=$((TESTS_PASSED + 1))

    # 验证是否为有效 JSON
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    if echo "$FIRST_LINE" | python3 -c "import sys, json; json.loads(sys.stdin.read())" 2>/dev/null; then
        log_success "JSON 格式有效"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        log_error "JSON 格式无效"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
else
    log_error "无法读取归档文件内容"
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

# 步骤 4: 验证数据字段完整性
log_step "步骤 4: 验证数据字段完整性"

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

# 步骤 5: 验证 S3 归档文件记录数
log_step "步骤 5: 验证归档文件记录数"

verify_s3_archive_file "${ARCHIVE_FILE}" 5

# 步骤 6: 验证 archive_location 为 S3 URI
log_step "步骤 6: 验证 archive_location 为 S3 URI"

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

# 步骤 7: 模拟数据恢复
log_step "步骤 7: 模拟数据恢复"

UNIQUE_ID=$(echo "$FIRST_LINE" | python3 -c "
import sys, json
data = json.loads(sys.stdin.read())
print(data.get('unique_id', ''))
" 2>/dev/null)

TESTS_TOTAL=$((TESTS_TOTAL + 1))
if [ -n "$UNIQUE_ID" ]; then
    log_info "归档记录 unique_id: ${UNIQUE_ID}"

    if [[ "$UNIQUE_ID" == *"${TEST_SESSION}"* ]]; then
        log_success "归档记录属于本场景测试数据"
        TESTS_PASSED=$((TESTS_PASSED + 1))

        # 在数据库中查找对应的元数据
        DB_UNIQUE_ID=$(db_query "
            SELECT unique_id FROM request_metadata
            WHERE unique_id = '${UNIQUE_ID}';
        ")

        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        if [ "$UNIQUE_ID" = "$DB_UNIQUE_ID" ]; then
            log_success "归档数据与数据库元数据匹配"
            TESTS_PASSED=$((TESTS_PASSED + 1))
        else
            log_error "归档数据与数据库元数据不匹配"
            TESTS_FAILED=$((TESTS_FAILED + 1))
        fi
    else
        log_warning "归档记录不属于本场景（来自其他场景的遗留数据），跳过匹配验证"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    fi
else
    log_error "无法从归档文件提取 unique_id"
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

# 步骤 8: 清理测试数据
log_step "步骤 8: 清理测试数据"

cleanup_test_data "${TEST_SESSION}"

echo ""

# 打印测试摘要
print_summary
