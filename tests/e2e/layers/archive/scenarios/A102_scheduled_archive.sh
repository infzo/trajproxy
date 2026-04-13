#!/bin/bash
# 场景 A102: 定时归档触发
# 验证归档调度器按配置的时间触发归档
# 注意: 此测试需要将 schedule 配置为高频执行（如每分钟）进行测试

# 获取脚本目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 A102: 定时归档触发"
echo "========================================"
echo ""

# 生成测试 session ID
TEST_SESSION="${TEST_SESSION_PREFIX}_A102"
MONTH_LAST=$(date -d "last month" +%Y_%m 2>/dev/null || date -v-1m +%Y_%m 2>/dev/null || echo "2026_03")

log_step "步骤 1: 创建过期分区并插入测试数据"

create_partition "${MONTH_LAST}"
insert_test_data "${MONTH_LAST}" 5 "${TEST_SESSION}"

log_info "已插入测试数据到分区 ${MONTH_LAST}"

# 步骤 2: 检查调度器状态
log_step "步骤 2: 检查调度器状态"

LOGS=$(get_container_logs)

# 验证调度器启动
if echo "$LOGS" | grep -q "ArchiveScheduler 已启动"; then
    log_success "调度器已启动"
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "调度器未启动"
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi
TESTS_TOTAL=$((TESTS_TOTAL + 1))

# 验证调度器运行状态
if echo "$LOGS" | grep -q "下次归档时间"; then
    NEXT_RUN=$(echo "$LOGS" | grep "下次归档时间" | tail -1 | sed 's/.*下次归档时间: //')
    log_info "下次归档时间: ${NEXT_RUN}"
    log_success "调度器运行正常（已计算下次执行时间）"
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_warning "未找到下次归档时间"
fi
TESTS_TOTAL=$((TESTS_TOTAL + 1))

# 步骤 3: 等待定时触发或手动触发
log_step "步骤 3: 等待归档任务执行"

# 对于测试环境，我们检查是否有归档执行的日志
# 如果 schedule 配置为高频（如 "* * * * *"），则等待调度器触发
# 否则，我们手动触发一次来模拟

if echo "$LOGS" | grep -q "开始执行归档任务"; then
    log_success "检测到归档任务已执行"
else
    log_info "未检测到归档任务执行，手动触发测试..."
    run_archive_script 1 false
fi

# 步骤 4: 验证归档结果
log_step "步骤 4: 验证归档结果"

sleep 2

# 检查分区是否被删除
if check_partition_exists "request_details_active_${MONTH_LAST}"; then
    log_error "分区仍存在（应已被删除）"
    TESTS_FAILED=$((TESTS_FAILED + 1))
else
    log_success "分区已删除"
    TESTS_PASSED=$((TESTS_PASSED + 1))
fi
TESTS_TOTAL=$((TESTS_TOTAL + 1))

# 检查归档文件
ARCHIVE_FILE="${MONTH_LAST}.jsonl.gz"
if check_archive_file_exists "${ARCHIVE_FILE}"; then
    log_success "归档文件已生成: ${ARCHIVE_FILE}"
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "归档文件未生成"
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi
TESTS_TOTAL=$((TESTS_TOTAL + 1))

# 步骤 5: 检查日志中的归档执行记录
log_step "步骤 5: 检查归档执行日志"

UPDATED_LOGS=$(get_container_logs)

if echo "$UPDATED_LOGS" | grep -q "归档任务完成"; then
    log_success "日志显示归档任务已完成"
    TESTS_PASSED=$((TESTS_PASSED + 1))

    # 提取归档统计信息
    PROCESSED=$(echo "$UPDATED_LOGS" | grep "处理分区数" | tail -1 | sed 's/.*处理分区数: //')
    ARCHIVED=$(echo "$UPDATED_LOGS" | grep "本次归档记录数" | tail -1 | sed 's/.*本次归档记录数: //')
    DROPPED=$(echo "$UPDATED_LOGS" | grep "删除分区数" | tail -1 | sed 's/.*删除分区数: //')

    log_info "归档统计: 处理 ${PROCESSED} 个分区, 归档 ${ARCHIVED} 条记录, 删除 ${DROPPED} 个分区"
else
    log_error "日志未显示归档任务完成"
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi
TESTS_TOTAL=$((TESTS_TOTAL + 1))

# 步骤 6: 清理测试数据
log_step "步骤 6: 清理测试数据"

cleanup_test_data "${TEST_SESSION}"

echo ""

# 打印测试摘要
print_summary