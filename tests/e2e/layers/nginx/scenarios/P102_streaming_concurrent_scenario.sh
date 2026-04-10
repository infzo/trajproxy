#!/bin/bash
# 场景 P102: 流式并发压测（Nginx 层）
# 测试流程：注册模型 -> 并发发送流式请求(800并发，共800个) -> 统计响应时间、chunk数和失败次数 -> 删除模型

# 获取脚本目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 P102: 流式并发压测（Nginx 层）"
echo "========================================"
echo ""

# 测试配置
SCENARIO_ID=$(basename "${BASH_SOURCE[0]}" .sh | grep -oE '[FP][0-9]+' | tr '[:upper:]' '[:lower:]')
CONCURRENT_TEST_BASE_URL="${BASE_URL}"
CONCURRENT_TEST_MODEL_NAME="streaming-concurrent-test-model"
CONCURRENT_TEST_RUN_ID="run-${SCENARIO_ID}"
CONCURRENT_TEST_SESSION_ID="session-${SCENARIO_ID}-$(date +%s%N | md5sum | head -c 8)"
CONCURRENT_TEST_TOTAL_REQUESTS=800
CONCURRENT_TEST_MAX_CONCURRENT=800

# 检查文件描述符限制
ULIMIT_SOFT=$(ulimit -Sn)
# 每个并发请求至少需要 2 个文件描述符（请求+响应），加上系统开销预留 200
MIN_FD_NEEDED=$((CONCURRENT_TEST_MAX_CONCURRENT * 2 + 200))
if [ "$ULIMIT_SOFT" -lt "$MIN_FD_NEEDED" ]; then
    log_error "文件描述符限制 (${ULIMIT_SOFT}) 不足以支撑 ${CONCURRENT_TEST_MAX_CONCURRENT} 并发"
    log_error "请执行: ulimit -n ${MIN_FD_NEEDED} 或更高值"
    log_error "当前需要至少 ${MIN_FD_NEEDED}，建议设置为 $((${MIN_FD_NEEDED} + 500))"
    exit 1
fi

# 性能统计变量
TOTAL_REQUESTS=0
SUCCESS_COUNT=0
FAIL_COUNT=0
STREAM_INCOMPLETE_COUNT=0
RESPONSE_TIMES=()
TOTAL_RESPONSE_TIME=0
CHUNK_COUNTS=()
TOTAL_CHUNK_COUNT=0

# 步骤 1: 注册模型（带 run_id）
log_step "步骤 1: 注册模型（run_id: ${CONCURRENT_TEST_RUN_ID}）"
log_curl_cmd "curl -s -w '\n%{http_code}' \
    -X POST '${CONCURRENT_TEST_BASE_URL}/models/register' \
    -H 'Content-Type: application/json' \
    -d '{
        \"run_id\": \"${CONCURRENT_TEST_RUN_ID}\",
        \"model_name\": \"${CONCURRENT_TEST_MODEL_NAME}\",
        \"url\": \"${BACKEND_MODEL_URL}\",
        \"api_key\": \"${TEST_MODEL_API_KEY}\",
        \"token_in_token_out\": false
    }'"
log_separator

REGISTER_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${CONCURRENT_TEST_BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{
        \"run_id\": \"${CONCURRENT_TEST_RUN_ID}\",
        \"model_name\": \"${CONCURRENT_TEST_MODEL_NAME}\",
        \"url\": \"${BACKEND_MODEL_URL}\",
        \"api_key\": \"${TEST_MODEL_API_KEY}\",
        \"token_in_token_out\": false
    }")

REGISTER_BODY=$(echo "$REGISTER_RESPONSE" | sed '$d')
REGISTER_STATUS=$(echo "$REGISTER_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${REGISTER_STATUS}"
log_response "${REGISTER_BODY}"
log_separator

assert_http_status "200" "$REGISTER_STATUS" "HTTP 状态码应为 200"

REGISTER_RESULT=$(json_get "$REGISTER_BODY" "status")
assert_eq "success" "$REGISTER_RESULT" "注册模型应返回 success"

sleep 1

echo ""

# 步骤 2: 并发发送流式请求
log_step "步骤 2: 并发发送 ${CONCURRENT_TEST_TOTAL_REQUESTS} 个流式请求（最大并发数: ${CONCURRENT_TEST_MAX_CONCURRENT}）"

# 创建临时目录存储结果
TEMP_DIR=$(mktemp -d)
trap "rm -rf $TEMP_DIR" EXIT

# 发送单个流式请求的函数
send_streaming_request() {
    local request_id=$1
    local temp_file="${TEMP_DIR}/req_${request_id}"

    # 记录开始时间（毫秒）
    START_TIME=$(python3 -c "import time; print(int(time.time() * 1000))")

    # 发送流式请求
    STREAM_RESPONSE=$(curl -s --no-buffer -w "\n%{http_code}" -X POST "${CONCURRENT_TEST_BASE_URL}/s/${CONCURRENT_TEST_RUN_ID}/${CONCURRENT_TEST_SESSION_ID}/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${CHAT_API_KEY}" \
        -d "{
            \"model\": \"${CONCURRENT_TEST_MODEL_NAME}\",
            \"messages\": [{\"role\": \"user\", \"content\": \"Hello ${request_id}\"}],
            \"stream\": true
        }")

    # 记录结束时间
    END_TIME=$(python3 -c "import time; print(int(time.time() * 1000))")

    # 计算响应时间
    RESPONSE_TIME=$((END_TIME - START_TIME))

    # 提取 HTTP 状态码（最后一行）
    CHAT_STATUS=$(echo "$STREAM_RESPONSE" | sed -n '$p')

    # 去掉状态码行，获取实际响应体
    RESPONSE_BODY=$(echo "$STREAM_RESPONSE" | sed '$d')

    # 统计 chunk 数量
    CHUNK_COUNT=$(echo "$RESPONSE_BODY" | grep -c "^data: {" || true)

    # 检查流式完整性：有 chunk 且包含 [DONE]
    HAS_DONE="false"
    if echo "$RESPONSE_BODY" | grep -q "\[DONE\]"; then
        HAS_DONE="true"
    fi

    # 写入结果文件: request_id,http_status,response_time_ms,chunk_count,has_done
    echo "${request_id},${CHAT_STATUS},${RESPONSE_TIME},${CHUNK_COUNT},${HAS_DONE}" > "${temp_file}"
}

# 导出函数和变量供子进程使用
export -f send_streaming_request
export CONCURRENT_TEST_BASE_URL CONCURRENT_TEST_SESSION_ID CONCURRENT_TEST_MODEL_NAME CONCURRENT_TEST_RUN_ID CHAT_API_KEY TEMP_DIR

# 并发控制：维持最多指定数量的并发请求
SENT_COUNT=0
PIDS=()

while [ $SENT_COUNT -lt $CONCURRENT_TEST_TOTAL_REQUESTS ]; do
    # 检查当前运行的进程数
    RUNNING_COUNT=0
    NEW_PIDS=()
    for pid in "${PIDS[@]}"; do
        if kill -0 $pid 2>/dev/null; then
            NEW_PIDS+=($pid)
            RUNNING_COUNT=$((RUNNING_COUNT + 1))
        fi
    done
    PIDS=("${NEW_PIDS[@]}")

    # 如果当前并发数小于最大值，启动新请求
    while [ $RUNNING_COUNT -lt $CONCURRENT_TEST_MAX_CONCURRENT ] && [ $SENT_COUNT -lt $CONCURRENT_TEST_TOTAL_REQUESTS ]; do
        SENT_COUNT=$((SENT_COUNT + 1))
        send_streaming_request $SENT_COUNT &
        PIDS+=($!)
        RUNNING_COUNT=$((RUNNING_COUNT + 1))
        echo -ne "\r已发送: ${SENT_COUNT}/${CONCURRENT_TEST_TOTAL_REQUESTS} | 当前并发: ${RUNNING_COUNT}   "
    done

    # 短暂等待避免忙等待
    sleep 0.05
done

# 等待所有请求完成
echo ""
log_info "等待所有请求完成..."
wait

echo ""

# 步骤 3: 删除模型
log_step "步骤 3: 删除模型（run_id: ${CONCURRENT_TEST_RUN_ID}）"
log_curl_cmd "curl -s -w '\n%{http_code}' \
    -X DELETE '${CONCURRENT_TEST_BASE_URL}/models?model_name=${CONCURRENT_TEST_MODEL_NAME}&run_id=${CONCURRENT_TEST_RUN_ID}'"
log_separator

DELETE_RESPONSE=$(curl -s -w "\n%{http_code}" -X DELETE "${CONCURRENT_TEST_BASE_URL}/models?model_name=${CONCURRENT_TEST_MODEL_NAME}&run_id=${CONCURRENT_TEST_RUN_ID}")

DELETE_BODY=$(echo "$DELETE_RESPONSE" | sed '$d')
DELETE_STATUS=$(echo "$DELETE_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${DELETE_STATUS}"
log_response "${DELETE_BODY}"
log_separator

assert_http_status "200" "$DELETE_STATUS" "HTTP 状态码应为 200"

DELETE_RESULT=$(json_get "$DELETE_BODY" "status")
assert_eq "success" "$DELETE_RESULT" "删除模型应返回 success"

echo ""

# 步骤 4: 统计结果
log_step "步骤 4: 性能统计"

# 读取所有结果文件
for result_file in "${TEMP_DIR}"/req_*; do
    if [ -f "$result_file" ]; then
        TOTAL_REQUESTS=$((TOTAL_REQUESTS + 1))
        IFS=',' read -r req_id status response_time chunk_count has_done < "$result_file"

        RESPONSE_TIMES+=($response_time)
        TOTAL_RESPONSE_TIME=$((TOTAL_RESPONSE_TIME + response_time))
        CHUNK_COUNTS+=($chunk_count)
        TOTAL_CHUNK_COUNT=$((TOTAL_CHUNK_COUNT + chunk_count))

        if [ "$status" == "200" ]; then
            SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
        else
            FAIL_COUNT=$((FAIL_COUNT + 1))
            log_error "请求 ${req_id} 失败, HTTP Status: ${status}"
        fi

        # 检查流式完整性
        if [ "$has_done" != "true" ] || [ "$chunk_count" -eq 0 ]; then
            STREAM_INCOMPLETE_COUNT=$((STREAM_INCOMPLETE_COUNT + 1))
        fi
    fi
done

# 计算平均响应时间
if [ $TOTAL_REQUESTS -gt 0 ]; then
    AVG_RESPONSE_TIME=$((TOTAL_RESPONSE_TIME / TOTAL_REQUESTS))
else
    AVG_RESPONSE_TIME=0
fi

# 计算最小和最大响应时间
MIN_RESPONSE_TIME=${RESPONSE_TIMES[0]}
MAX_RESPONSE_TIME=${RESPONSE_TIMES[0]}

for time in "${RESPONSE_TIMES[@]}"; do
    if [ $time -lt $MIN_RESPONSE_TIME ]; then
        MIN_RESPONSE_TIME=$time
    fi
    if [ $time -gt $MAX_RESPONSE_TIME ]; then
        MAX_RESPONSE_TIME=$time
    fi
done

# 计算平均 chunk 数
if [ $TOTAL_REQUESTS -gt 0 ]; then
    AVG_CHUNK_COUNT=$((TOTAL_CHUNK_COUNT / TOTAL_REQUESTS))
else
    AVG_CHUNK_COUNT=0
fi

# 计算最小和最大 chunk 数
MIN_CHUNK_COUNT=${CHUNK_COUNTS[0]}
MAX_CHUNK_COUNT=${CHUNK_COUNTS[0]}

for count in "${CHUNK_COUNTS[@]}"; do
    if [ $count -lt $MIN_CHUNK_COUNT ]; then
        MIN_CHUNK_COUNT=$count
    fi
    if [ $count -gt $MAX_CHUNK_COUNT ]; then
        MAX_CHUNK_COUNT=$count
    fi
done

# 计算成功率
if [ $TOTAL_REQUESTS -gt 0 ]; then
    SUCCESS_RATE=$(python3 -c "print(f'{$SUCCESS_COUNT / $TOTAL_REQUESTS * 100:.2f}')")
else
    SUCCESS_RATE="0.00"
fi

# 计算流式完整率
if [ $TOTAL_REQUESTS -gt 0 ]; then
    STREAM_COMPLETE_COUNT=$((TOTAL_REQUESTS - STREAM_INCOMPLETE_COUNT))
    STREAM_COMPLETE_RATE=$(python3 -c "print(f'{$STREAM_COMPLETE_COUNT / $TOTAL_REQUESTS * 100:.2f}')")
else
    STREAM_COMPLETE_RATE="0.00"
fi

echo ""
echo "========================================"
echo "流式并发压测摘要"
echo "========================================"
echo "总请求数: ${TOTAL_REQUESTS}"
echo "成功次数: ${SUCCESS_COUNT}"
echo "失败次数: ${FAIL_COUNT}"
echo "成功率: ${SUCCESS_RATE}%"
echo "流式完整数: ${STREAM_COMPLETE_COUNT}"
echo "流式不完整数: ${STREAM_INCOMPLETE_COUNT}"
echo "流式完整率: ${STREAM_COMPLETE_RATE}%"
echo "最大并发数: ${CONCURRENT_TEST_MAX_CONCURRENT}"
echo "----------------------------------------"
echo "响应时间统计:"
echo "  平均: ${AVG_RESPONSE_TIME} ms"
echo "  最小: ${MIN_RESPONSE_TIME} ms"
echo "  最大: ${MAX_RESPONSE_TIME} ms"
echo "----------------------------------------"
echo "流式 chunk 统计:"
echo "  平均: ${AVG_CHUNK_COUNT}"
echo "  最小: ${MIN_CHUNK_COUNT}"
echo "  最大: ${MAX_CHUNK_COUNT}"
echo "========================================"
echo ""

# 打印测试摘要
print_summary
