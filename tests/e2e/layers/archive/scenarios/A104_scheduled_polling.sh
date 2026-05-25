#!/bin/bash
# 场景 A104: 观测调度器自动归档
# 验证调度器以 poll_interval 周期自动扫描并归档过期 run
# 本测试不调用 run_archive_once，完全依赖容器内调度器自主执行

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 A104: 观测调度器自动归档"
echo "========================================"
echo ""

TEST_SESSION="${TEST_SESSION_PREFIX}_A104"
TEST_RUN="test-run-a104"
# 使用 3 个月前的数据，确保超过 retention_days=30
MONTH_TARGET=$(date -d "3 months ago" +%Y_%m 2>/dev/null || date -v-3m +%Y_%m 2>/dev/null || echo "2026_02")

# 步骤 1: 准备过期数据
log_step "步骤 1: 插入过期数据（${MONTH_TARGET}，超过30天留存）"

cleanup_test_data "${TEST_SESSION}"

if ! check_partition_exists "request_details_active_${MONTH_TARGET}"; then
    create_partition "${MONTH_TARGET}"
fi
insert_test_data "${MONTH_TARGET}" 5 "${TEST_SESSION}" "${TEST_RUN}"

RECORD_COUNT=$(get_partition_record_count "request_details_active_${MONTH_TARGET}")
log_info "分区 ${MONTH_TARGET} 包含 ${RECORD_COUNT} 条记录"

# 步骤 2: 确认数据尚未被归档
log_step "步骤 2: 确认数据尚未归档"

TESTS_TOTAL=$((TESTS_TOTAL + 1))
PRE_ARCHIVE_LOCATION=$(get_archive_location "${TEST_SESSION}")
if [ -z "$PRE_ARCHIVE_LOCATION" ]; then
    log_success "初始状态: archive_location 为 NULL（未归档）"
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "初始状态异常: 已有 archive_location"
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

# 步骤 3: 等待调度器自动归档
log_step "步骤 3: 等待调度器自动执行归档（轮询 60s，最长等待 180s）"

MAX_WAIT=180
POLL_INTERVAL=5
WAITED=0
ARCHIVED=false

while [ $WAITED -lt $MAX_WAIT ]; do
    sleep $POLL_INTERVAL
    WAITED=$((WAITED + POLL_INTERVAL))

    ARCHIVE_LOCATION=$(get_archive_location "${TEST_SESSION}")
    if [ -n "$ARCHIVE_LOCATION" ]; then
        log_success "调度器在 ${WAITED}s 内完成自动归档"
        echo "    archive_location: ${ARCHIVE_LOCATION}"
        ARCHIVED=true
        break
    fi

    if [ $((WAITED % 30)) -eq 0 ]; then
        log_info "已等待 ${WAITED}s，继续轮询..."
    fi
done

# 步骤 4: 验证自动归档结果
log_step "步骤 4: 验证自动归档结果"

TESTS_TOTAL=$((TESTS_TOTAL + 1))
if [ "$ARCHIVED" = true ]; then
    log_success "调度器自动归档生效"
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "调度器未在 ${MAX_WAIT}s 内执行归档"
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

# 验证 archive_location 格式
TESTS_TOTAL=$((TESTS_TOTAL + 1))
EXPECTED_FILE="${TEST_RUN}/${TEST_SESSION}.jsonl.gz"
if echo "$ARCHIVE_LOCATION" | grep -q "${EXPECTED_FILE}"; then
    log_success "archive_location 格式正确: {run_id}/{session_id}.jsonl.gz"
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "archive_location 格式错误: ${ARCHIVE_LOCATION}"
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

# 验证数据已从活跃表 DELETE
TESTS_TOTAL=$((TESTS_TOTAL + 1))
REMAINING=$(get_partition_record_count "request_details_active_${MONTH_TARGET}" 2>/dev/null || echo "0")
if [ "${REMAINING:-0}" -eq 0 ]; then
    log_success "过期数据已被调度器自动 DELETE"
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "仍有 ${REMAINING} 条数据未清理"
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

# 步骤 5: 验证归档文件
log_step "步骤 5: 验证归档文件"

if [ -n "$ARCHIVE_LOCATION" ] && check_archive_file_exists "${ARCHIVE_LOCATION}"; then
    log_success "归档文件可由存储后端读取"
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "归档文件不可访问"
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi
TESTS_TOTAL=$((TESTS_TOTAL + 1))

# 步骤 6: 清理
log_step "步骤 6: 清理测试数据"
cleanup_test_data "${TEST_SESSION}"

echo ""
print_summary
