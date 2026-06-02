#!/bin/bash
# 场景 H100: CacheInferServer 基本功能验证
#
# 验证缓存服务器的核心功能:
#   1. 缓存未命中 → 转发到真实后端，响应正确
#   2. 缓存命中 → 直接返回缓存结果，响应一致
#   3. 缓存清除 → 清除后重新未命中
#   4. 流式请求 → 缓存和回放均正确
#
# 架构:
#   traj_proxy → CacheInferServer (:18999) → Mock推理服务 (:19988)
#
# 前置条件:
#   - traj_proxy 已启动
#   - 本测试使用 mock_infer_server.py 作为真实后端（无需真实 vLLM）

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 H100: CacheInferServer 基本功能验证"
echo "========================================"
echo ""

# 测试配置
SCENARIO_ID="h100"
TEST_MODEL_NAME="${DEFAULT_MODEL_NAME}"
TEST_RUN_ID="run-${SCENARIO_ID}"
TEST_SESSION_ID="session-${SCENARIO_ID}-$(date +%s%N | md5sum | head -c 8)"
TEST_MAX_TOKENS="${E2E_MAX_TOKENS:-512}"

# Mock 推理服务配置（作为真实后端）
MOCK_PORT=19988
MOCK_URL="http://127.0.0.1:${MOCK_PORT}"

# 引用 proxy 层的 mock_infer_server.py
PROXY_LAYER_DIR="${SCRIPT_DIR}/../../proxy"
MOCK_INFER_SERVER="${PROXY_LAYER_DIR}/mock_infer_server.py"

# ========================================
# 启动 Mock 推理服务（作为"真实后端"）
# ========================================
log_step "启动 Mock 推理服务（作为真实后端，端口 ${MOCK_PORT}）"

# 清理占用端口的残留进程
occupying_pid=$(lsof -ti :"${MOCK_PORT}" 2>/dev/null)
if [ -n "$occupying_pid" ]; then
    log_warning "端口 ${MOCK_PORT} 被残留进程 (PID: $occupying_pid) 占用，正在清理..."
    kill -9 $occupying_pid 2>/dev/null
    sleep 1
fi

python3 "${MOCK_INFER_SERVER}" "${MOCK_PORT}" &
MOCK_PID=$!

# 等待 mock 服务启动
for i in $(seq 1 10); do
    if ! kill -0 "$MOCK_PID" 2>/dev/null; then
        log_error "Mock 推理服务进程异常退出"
        MOCK_PID=""
        exit 1
    fi
    health_result=$(curl -s --noproxy '*' --max-time 3 "${MOCK_URL}/mock/health" 2>&1)
    if echo "$health_result" | grep -q "ok"; then
        log_success "Mock 推理服务已启动 (PID: ${MOCK_PID}, Port: ${MOCK_PORT})"
        break
    fi
    sleep 1
done

if [ -z "$MOCK_PID" ] || ! kill -0 "$MOCK_PID" 2>/dev/null; then
    log_error "Mock 推理服务启动失败"
    exit 1
fi

echo ""

# ========================================
# 启动 CacheInferServer
# ========================================
log_step "启动 CacheInferServer（转发到 Mock 推理服务）"

export REAL_INFER_URL="http://127.0.0.1:${MOCK_PORT}/v1"
export CACHE_INFER_PORT=18999
CACHE_INFER_URL="http://127.0.0.1:${CACHE_INFER_PORT}"

# 清理端口
occupying_pid=$(lsof -ti :"${CACHE_INFER_PORT}" 2>/dev/null)
if [ -n "$occupying_pid" ]; then
    kill -9 $occupying_pid 2>/dev/null
    sleep 1
fi

REAL_INFER_URL="${REAL_INFER_URL}" \
    python3 "${CACHE_INFER_SERVER}" "${CACHE_INFER_PORT}" &
CACHE_PID=$!

# 等待缓存服务启动
for i in $(seq 1 15); do
    if ! kill -0 "$CACHE_PID" 2>/dev/null; then
        log_error "CacheInferServer 进程异常退出"
        CACHE_PID=""
        exit 1
    fi
    health_result=$(curl -s --noproxy '*' --max-time 3 "${CACHE_INFER_URL}/cache/health" 2>&1)
    if echo "$health_result" | grep -q "ok"; then
        log_success "CacheInferServer 已启动 (PID: ${CACHE_PID}, Port: ${CACHE_INFER_PORT})"
        break
    fi
    sleep 1
done
echo ""

# ========================================
# 步骤 1: 注册模型（url 指向 CacheInferServer）
# ========================================
log_step "步骤 1: 注册模型（url 指向 CacheInferServer）"

log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X POST '${BASE_URL}/models/register' \\
    -H 'Content-Type: application/json' \\
    -d '{...url: ${CACHE_INFER_URL}/v1...}'"
log_separator

REGISTER_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{
        \"run_id\": \"${TEST_RUN_ID}\",
        \"model_name\": \"${TEST_MODEL_NAME}\",
        \"url\": \"${CACHE_INFER_URL}/v1\",
        \"api_key\": \"${TEST_MODEL_API_KEY}\"
    }")

REGISTER_BODY=$(echo "$REGISTER_RESPONSE" | sed '$d')
REGISTER_STATUS=$(echo "$REGISTER_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${REGISTER_STATUS}"
log_response "${REGISTER_BODY}"
log_separator

assert_http_status "200" "$REGISTER_STATUS" "注册模型 HTTP 状态码应为 200"
assert_contains "$REGISTER_BODY" "success" "注册模型应返回 success"

sleep 2  # 等待模型注册生效
echo ""

# ========================================
# 步骤 2: 首次请求（缓存未命中）
# ========================================
log_step "步骤 2: 首次非流式请求（预期: 缓存未命中）"

REQUEST_BODY='{
    "model": "'"${TEST_MODEL_NAME}"'",
    "messages": [{"role": "user", "content": "Hello, cache test!"}],
    "stream": false,
    "max_tokens": '"${TEST_MAX_TOKENS}"',
    '"${E2E_SAMPLING_PARAMS}"'
}'

log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X POST '${BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}/v1/chat/completions' \\
    -H 'Content-Type: application/json' \\
    -H 'Authorization: Bearer ${CHAT_API_KEY}' \\
    -d '...非流式请求...'"
log_separator

RESPONSE1=$(curl -s -w "\n%{http_code}" -X POST "${BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "${REQUEST_BODY}")

BODY1=$(echo "$RESPONSE1" | sed '$d')
STATUS1=$(echo "$RESPONSE1" | sed -n '$p')

log_response "HTTP Status: ${STATUS1}"
log_response "${BODY1}"
log_separator

assert_http_status "200" "$STATUS1" "首次非流式请求 HTTP 状态码应为 200"
assert_contains "$BODY1" "choices" "首次响应应包含 choices 字段"
echo ""

# ========================================
# 步骤 3: 第二次相同请求（缓存命中）
# ========================================
log_step "步骤 3: 第二次相同请求（预期: 缓存命中）"

RESPONSE2=$(curl -s -w "\n%{http_code}" -X POST "${BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "${REQUEST_BODY}")

BODY2=$(echo "$RESPONSE2" | sed '$d')
STATUS2=$(echo "$RESPONSE2" | sed -n '$p')

log_response "HTTP Status: ${STATUS2}"
log_response "${BODY2}"
log_separator

assert_http_status "200" "$STATUS2" "第二次非流式请求 HTTP 状态码应为 200"
assert_contains "$BODY2" "choices" "第二次响应应包含 choices 字段"

# 验证两次响应内容一致
if [ "$BODY1" = "$BODY2" ]; then
    log_success "两次非流式响应完全一致（缓存回放正确）"
else
    log_error "两次非流式响应不一致"
    echo "  首次: $(echo "$BODY1" | head -c 200)..."
    echo "  二次: $(echo "$BODY2" | head -c 200)..."
    TEST_FAILED=1
fi
echo ""

# ========================================
# 步骤 4: 验证缓存统计
# ========================================
log_step "步骤 4: 验证缓存统计（预期: 1 命中 + 1 未命中）"

CACHE_STATS=$(curl -s --noproxy '*' --max-time 5 "${CACHE_INFER_URL}/cache/stats")
log_response "${CACHE_STATS}"

CACHE_HITS=$(echo "$CACHE_STATS" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('hits', -1))" 2>/dev/null)
CACHE_MISSES=$(echo "$CACHE_STATS" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('misses', -1))" 2>/dev/null)
CACHE_ENTRIES=$(echo "$CACHE_STATS" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('entries', -1))" 2>/dev/null)

log_info "缓存命中: ${CACHE_HITS}, 未命中: ${CACHE_MISSES}, 条目数: ${CACHE_ENTRIES}"

assert_eq "1" "$CACHE_HITS" "缓存命中计数应为 1"
assert_eq "1" "$CACHE_MISSES" "缓存未命中计数应为 1"
assert_eq "1" "$CACHE_ENTRIES" "缓存条目数应为 1"
echo ""

# ========================================
# 步骤 5: 流式请求（缓存未命中 → 缓存 + 返回）
# ========================================
log_step "步骤 5: 流式请求（预期: 缓存未命中，流式响应正确）"

SESSION_ID_STREAM="session-${SCENARIO_ID}-stream-$(date +%s%N | md5sum | head -c 8)"

REQUEST_STREAM='{
    "model": "'"${TEST_MODEL_NAME}"'",
    "messages": [{"role": "user", "content": "Stream test"}],
    "stream": true,
    "max_tokens": '"${TEST_MAX_TOKENS}"',
    '"${E2E_SAMPLING_PARAMS}"'
}'

log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X POST '${BASE_URL}/s/${TEST_RUN_ID}/${SESSION_ID_STREAM}/v1/chat/completions' \\
    -H 'Content-Type: application/json' \\
    -H 'Authorization: Bearer ${CHAT_API_KEY}' \\
    -d '...流式请求...'"
log_separator

STREAM_RESPONSE1=$(curl -s -w "\n%{http_code}" -X POST "${BASE_URL}/s/${TEST_RUN_ID}/${SESSION_ID_STREAM}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "${REQUEST_STREAM}")

STREAM_BODY1=$(echo "$STREAM_RESPONSE1" | sed '$d')
STREAM_STATUS1=$(echo "$STREAM_RESPONSE1" | sed -n '$p')

log_response "HTTP Status: ${STREAM_STATUS1}"
# 只打印前几行 SSE 数据（避免输出过长）
echo "$STREAM_BODY1" | head -10
log_separator

assert_http_status "200" "$STREAM_STATUS1" "首次流式请求 HTTP 状态码应为 200"
assert_contains "$STREAM_BODY1" "data:" "流式响应应包含 SSE data: 行"
assert_contains "$STREAM_BODY1" "[DONE]" "流式响应应以 [DONE] 结束"
echo ""

# ========================================
# 步骤 6: 第二次流式请求（缓存命中）
# ========================================
log_step "步骤 6: 第二次流式请求（预期: 缓存命中，响应一致）"

STREAM_RESPONSE2=$(curl -s -w "\n%{http_code}" -X POST "${BASE_URL}/s/${TEST_RUN_ID}/${SESSION_ID_STREAM}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "${REQUEST_STREAM}")

STREAM_BODY2=$(echo "$STREAM_RESPONSE2" | sed '$d')
STREAM_STATUS2=$(echo "$STREAM_RESPONSE2" | sed -n '$p')

assert_http_status "200" "$STREAM_STATUS2" "第二次流式请求 HTTP 状态码应为 200"

# 验证两次流式响应完全一致（逐字节对比）
if [ "$STREAM_BODY1" = "$STREAM_BODY2" ]; then
    log_success "两次流式响应完全一致（缓存回放正确）"
else
    log_error "两次流式响应不一致"
    TEST_FAILED=1
fi
echo ""

# ========================================
# 步骤 7: 验证更新后的缓存统计
# ========================================
log_step "步骤 7: 验证缓存统计（预期: 2 命中 + 2 未命中，2 条目）"

CACHE_STATS2=$(curl -s --noproxy '*' --max-time 5 "${CACHE_INFER_URL}/cache/stats")
CACHE_HITS2=$(echo "$CACHE_STATS2" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('hits', -1))" 2>/dev/null)
CACHE_MISSES2=$(echo "$CACHE_STATS2" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('misses', -1))" 2>/dev/null)
CACHE_ENTRIES2=$(echo "$CACHE_STATS2" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('entries', -1))" 2>/dev/null)

log_info "缓存命中: ${CACHE_HITS2}, 未命中: ${CACHE_MISSES2}, 条目数: ${CACHE_ENTRIES2}"

assert_eq "2" "$CACHE_HITS2" "更新后缓存命中计数应为 2"
assert_eq "2" "$CACHE_MISSES2" "更新后缓存未命中计数应为 2"
assert_eq "2" "$CACHE_ENTRIES2" "缓存条目数应为 2（非流式 + 流式）"
echo ""

# ========================================
# 步骤 8: 清除缓存
# ========================================
log_step "步骤 8: 清除缓存并验证"

CLEAR_RESULT=$(curl -s --noproxy '*' --max-time 5 -X DELETE "${CACHE_INFER_URL}/cache/clear")
log_response "${CLEAR_RESULT}"

CLEAR_STATUS=$(echo "$CLEAR_RESULT" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('status', ''))" 2>/dev/null)
assert_eq "cleared" "$CLEAR_STATUS" "清除缓存应返回 cleared 状态"

# 验证缓存已清空
CACHE_STATS3=$(curl -s --noproxy '*' --max-time 5 "${CACHE_INFER_URL}/cache/stats")
CACHE_HITS3=$(echo "$CACHE_STATS3" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('hits', -1))" 2>/dev/null)
CACHE_ENTRIES3=$(echo "$CACHE_STATS3" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('entries', -1))" 2>/dev/null)

assert_eq "0" "$CACHE_HITS3" "清除后缓存命中计数应为 0"
assert_eq "0" "$CACHE_ENTRIES3" "清除后缓存条目数应为 0"
echo ""

# ========================================
# 步骤 9: 清除后再次请求（重新未命中）
# ========================================
log_step "步骤 9: 清除后再次请求（预期: 重新未命中，重新缓存）"

RESPONSE3=$(curl -s -w "\n%{http_code}" -X POST "${BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "${REQUEST_BODY}")

BODY3=$(echo "$RESPONSE3" | sed '$d')
STATUS3=$(echo "$RESPONSE3" | sed -n '$p')

assert_http_status "200" "$STATUS3" "清除后请求 HTTP 状态码应为 200"
assert_contains "$BODY3" "choices" "清除后响应应包含 choices 字段"

# 验证重新缓存
CACHE_STATS4=$(curl -s --noproxy '*' --max-time 5 "${CACHE_INFER_URL}/cache/stats")
CACHE_MISSES4=$(echo "$CACHE_STATS4" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('misses', -1))" 2>/dev/null)
CACHE_ENTRIES4=$(echo "$CACHE_STATS4" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('entries', -1))" 2>/dev/null)

assert_eq "1" "$CACHE_MISSES4" "清除后缓存未命中计数应为 1（重新计数）"
assert_eq "1" "$CACHE_ENTRIES4" "清除后缓存条目数应为 1（重新缓存）"
echo ""

# ========================================
# 步骤 10: 删除模型
# ========================================
log_step "步骤 10: 删除模型"

DELETE_RESPONSE=$(curl -s -w "\n%{http_code}" -X DELETE "${BASE_URL}/models?model_name=${TEST_MODEL_NAME}&run_id=${TEST_RUN_ID}")

DELETE_BODY=$(echo "$DELETE_RESPONSE" | sed '$d')
DELETE_STATUS=$(echo "$DELETE_RESPONSE" | sed -n '$p')

assert_http_status "200" "$DELETE_STATUS" "删除模型 HTTP 状态码应为 200"

DELETE_RESULT=$(json_get "$DELETE_BODY" "status")
assert_eq "success" "$DELETE_RESULT" "删除模型应返回 success"
echo ""

# ========================================
# 清理
# ========================================
log_step "清理：停止 CacheInferServer 和 Mock 推理服务"

# 停止缓存服务
if [ -n "$CACHE_PID" ]; then
    kill "$CACHE_PID" 2>/dev/null
    wait "$CACHE_PID" 2>/dev/null
    log_info "CacheInferServer 已停止 (PID: ${CACHE_PID})"
fi

# 停止 Mock 服务
if [ -n "$MOCK_PID" ]; then
    kill "$MOCK_PID" 2>/dev/null
    wait "$MOCK_PID" 2>/dev/null
    log_info "Mock 推理服务已停止 (PID: ${MOCK_PID})"
fi

# 清空 Mock 请求记录
curl -s --noproxy '*' --max-time 3 -X DELETE "${MOCK_URL}/mock/requests" > /dev/null 2>&1

echo ""

# ========================================
# 测试总结
# ========================================
print_summary

if [ "${TEST_FAILED:-0}" = "1" ]; then
    exit 1
fi
