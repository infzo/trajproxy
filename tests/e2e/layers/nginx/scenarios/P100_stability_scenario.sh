#!/bin/bash
# 场景 P100: 长稳场景（Nginx 层）
# 测试流程：注册模型 -> 持续发送100个请求 -> 统计响应时间和失败次数 -> 删除模型

# 获取脚本目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 P100: 长稳场景（Nginx 层）"
echo "========================================"
echo ""

# 测试配置
SCENARIO_ID=$(basename "${BASH_SOURCE[0]}" .sh | grep -oE '[FP][0-9]+' | tr '[:upper:]' '[:lower:]')
STABILITY_TEST_BASE_URL="${BASE_URL}"
STABILITY_TEST_MODEL_NAME="stability-test-model"
STABILITY_TEST_RUN_ID="run-${SCENARIO_ID}"
STABILITY_TEST_SESSION_ID="session-${SCENARIO_ID}-$(date +%s%N | md5sum | head -c 8)"
STABILITY_TEST_REQUEST_COUNT=100

# 性能统计变量
TOTAL_REQUESTS=0
SUCCESS_COUNT=0
FAIL_COUNT=0
RESPONSE_TIMES=()
TOTAL_RESPONSE_TIME=0

# 步骤 1: 注册模型（带 run_id）
log_step "步骤 1: 注册模型（run_id: ${STABILITY_TEST_RUN_ID}）"
log_curl_cmd "curl -s -w '\n%{http_code}' \
    -X POST '${STABILITY_TEST_BASE_URL}/models/register' \
    -H 'Content-Type: application/json' \
    -d '{
        \"run_id\": \"${STABILITY_TEST_RUN_ID}\",
        \"model_name\": \"${STABILITY_TEST_MODEL_NAME}\",
        \"url\": \"${BACKEND_MODEL_URL}\",
        \"api_key\": \"${TEST_MODEL_API_KEY}\",
        \"token_in_token_out\": false
    }'"
log_separator

REGISTER_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${STABILITY_TEST_BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{
        \"run_id\": \"${STABILITY_TEST_RUN_ID}\",
        \"model_name\": \"${STABILITY_TEST_MODEL_NAME}\",
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

# 步骤 2: 持续发送请求
log_step "步骤 2: 发送 ${STABILITY_TEST_REQUEST_COUNT} 个推理请求"

for ((i=1; i<=STABILITY_TEST_REQUEST_COUNT; i++)); do
    TOTAL_REQUESTS=$((TOTAL_REQUESTS + 1))

    # 记录开始时间（毫秒）
    START_TIME=$(python3 -c "import time; print(int(time.time() * 1000))")

    # 发送请求
    CHAT_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${STABILITY_TEST_BASE_URL}/s/${STABILITY_TEST_RUN_ID}/${STABILITY_TEST_SESSION_ID}/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${CHAT_API_KEY}" \
        -d "{
            \"model\": \"${STABILITY_TEST_MODEL_NAME}\",
            \"messages\": [{\"role\": \"user\", \"content\": \"Hello ${i}\"}],
            \"stream\": false
        }")

    # 记录结束时间
    END_TIME=$(python3 -c "import time; print(int(time.time() * 1000))")

    # 计算响应时间
    RESPONSE_TIME=$((END_TIME - START_TIME))
    RESPONSE_TIMES+=($RESPONSE_TIME)
    TOTAL_RESPONSE_TIME=$((TOTAL_RESPONSE_TIME + RESPONSE_TIME))

    CHAT_STATUS=$(echo "$CHAT_RESPONSE" | sed -n '$p')

    # 判断成功或失败
    if [ "$CHAT_STATUS" == "200" ]; then
        SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
        echo -ne "\r进度: ${i}/${STABILITY_TEST_REQUEST_COUNT} | 成功: ${SUCCESS_COUNT} | 失败: ${FAIL_COUNT} | 当前响应: ${RESPONSE_TIME}ms   "
    else
        FAIL_COUNT=$((FAIL_COUNT + 1))
        echo ""
        log_error "请求 ${i} 失败, HTTP Status: ${CHAT_STATUS}"
    fi
done

echo ""
echo ""

# 步骤 3: 删除模型
log_step "步骤 3: 删除模型（run_id: ${STABILITY_TEST_RUN_ID}）"
log_curl_cmd "curl -s -w '\n%{http_code}' \
    -X DELETE '${STABILITY_TEST_BASE_URL}/models?model_name=${STABILITY_TEST_MODEL_NAME}&run_id=${STABILITY_TEST_RUN_ID}'"
log_separator

DELETE_RESPONSE=$(curl -s -w "\n%{http_code}" -X DELETE "${STABILITY_TEST_BASE_URL}/models?model_name=${STABILITY_TEST_MODEL_NAME}&run_id=${STABILITY_TEST_RUN_ID}")

DELETE_BODY=$(echo "$DELETE_RESPONSE" | sed '$d')
DELETE_STATUS=$(echo "$DELETE_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${DELETE_STATUS}"
log_response "${DELETE_BODY}"
log_separator

assert_http_status "200" "$DELETE_STATUS" "HTTP 状态码应为 200"

DELETE_RESULT=$(json_get "$DELETE_BODY" "status")
assert_eq "success" "$DELETE_RESULT" "删除模型应返回 success"

echo ""

# 步骤 4: 计算并输出性能统计数据
log_step "步骤 4: 性能统计"

# 计算平均响应时间
if [ $SUCCESS_COUNT -gt 0 ]; then
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

# 计算成功率
if [ $TOTAL_REQUESTS -gt 0 ]; then
    SUCCESS_RATE=$(python3 -c "print(f'{$SUCCESS_COUNT / $TOTAL_REQUESTS * 100:.2f}')")
else
    SUCCESS_RATE="0.00"
fi

echo ""
echo "========================================"
echo "性能测试摘要"
echo "========================================"
echo "总请求数: ${TOTAL_REQUESTS}"
echo "成功次数: ${SUCCESS_COUNT}"
echo "失败次数: ${FAIL_COUNT}"
echo "成功率: ${SUCCESS_RATE}%"
echo "----------------------------------------"
echo "响应时间统计:"
echo "  平均: ${AVG_RESPONSE_TIME} ms"
echo "  最小: ${MIN_RESPONSE_TIME} ms"
echo "  最大: ${MAX_RESPONSE_TIME} ms"
echo "========================================"
echo ""

# 打印测试摘要
print_summary
