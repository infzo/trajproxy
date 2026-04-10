#!/bin/bash
# 场景 F106: 前缀缓存匹配场景（Nginx 层）
# 测试流程：注册模型（token_in_token_out=true）-> 三轮多轮对话 -> 查询轨迹验证缓存命中 -> 删除模型

# 获取脚本目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 F106: 前缀缓存匹配场景（Nginx 层）"
echo "========================================"
echo ""

# 测试配置
SCENARIO_ID=$(basename "${BASH_SOURCE[0]}" .sh | grep -oE '[FP][0-9]+' | tr '[:upper:]' '[:lower:]')
CACHE_TEST_BASE_URL="${BASE_URL}"
CACHE_TEST_MODEL_NAME="prefix-cache-test-model"
CACHE_TEST_RUN_ID="run-${SCENARIO_ID}"
CACHE_TEST_SESSION_ID="session-${SCENARIO_ID}-$(date +%s%N | md5sum | head -c 8)"
CACHE_TEST_TOKENIZER_PATH="Qwen/Qwen3.5-2B"

# ========== 步骤 1: 注册模型 ==========

log_step "步骤 1: 注册模型（run_id: ${CACHE_TEST_RUN_ID}, token_in_token_out: true）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X POST '${CACHE_TEST_BASE_URL}/models/register' \\
    -H 'Content-Type: application/json' \\
    -d '{
        \"run_id\": \"${CACHE_TEST_RUN_ID}\",
        \"model_name\": \"${CACHE_TEST_MODEL_NAME}\",
        \"url\": \"${BACKEND_MODEL_URL}\",
        \"api_key\": \"${TEST_MODEL_API_KEY}\",
        \"tokenizer_path\": \"${CACHE_TEST_TOKENIZER_PATH}\",
        \"token_in_token_out\": true
    }'"
log_separator

REGISTER_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${CACHE_TEST_BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{
        \"run_id\": \"${CACHE_TEST_RUN_ID}\",
        \"model_name\": \"${CACHE_TEST_MODEL_NAME}\",
        \"url\": \"${BACKEND_MODEL_URL}\",
        \"api_key\": \"${TEST_MODEL_API_KEY}\",
        \"tokenizer_path\": \"${CACHE_TEST_TOKENIZER_PATH}\",
        \"token_in_token_out\": true
    }")

REGISTER_BODY=$(echo "$REGISTER_RESPONSE" | sed '$d')
REGISTER_STATUS=$(echo "$REGISTER_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${REGISTER_STATUS}"
log_response "${REGISTER_BODY}"
log_separator

assert_http_status "200" "$REGISTER_STATUS" "注册模型 HTTP 状态码应为 200"

REGISTER_RESULT=$(json_get "$REGISTER_BODY" "status")
assert_eq "success" "$REGISTER_RESULT" "注册模型应返回 success"

REGISTER_TITO=$(json_get_bool "$REGISTER_BODY" "token_in_token_out")
assert_eq "true" "$REGISTER_TITO" "token_in_token_out 应为 true"

sleep 2  # 等待模型注册生效

echo ""

# ========== 步骤 2: 第1轮对话 ==========

log_step "步骤 2: 第1轮对话（无历史，cache_hit_tokens 应为 0）"
ROUND1_MESSAGES='[{"role": "user", "content": "你好"}]'
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X POST '${CACHE_TEST_BASE_URL}/s/${CACHE_TEST_RUN_ID}/${CACHE_TEST_SESSION_ID}/v1/chat/completions' \\
    -H 'Content-Type: application/json' \\
    -H 'Authorization: Bearer ${CHAT_API_KEY}' \\
    -d '{
        \"model\": \"${CACHE_TEST_MODEL_NAME}\",
        \"messages\": ${ROUND1_MESSAGES},
        \"stream\": false
    }'"
log_separator

ROUND1_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${CACHE_TEST_BASE_URL}/s/${CACHE_TEST_RUN_ID}/${CACHE_TEST_SESSION_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${CACHE_TEST_MODEL_NAME}\",
        \"messages\": ${ROUND1_MESSAGES},
        \"stream\": false
    }")

ROUND1_BODY=$(echo "$ROUND1_RESPONSE" | sed '$d')
ROUND1_STATUS=$(echo "$ROUND1_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${ROUND1_STATUS}"
log_response "${ROUND1_BODY}"
log_separator

assert_http_status "200" "$ROUND1_STATUS" "第1轮对话 HTTP 状态码应为 200"
assert_contains "$ROUND1_BODY" "choices" "第1轮响应应包含 choices 字段"

# 提取第1轮 assistant 响应内容
ROUND1_ASSISTANT_CONTENT=$(echo "$ROUND1_BODY" | python3 -c "
import sys, json
data = json.loads(sys.stdin.read())
print(data['choices'][0]['message']['content'])
" 2>/dev/null)

if [ -n "$ROUND1_ASSISTANT_CONTENT" ]; then
    log_success "提取第1轮 assistant 响应成功"
else
    log_error "提取第1轮 assistant 响应失败"
    TEST_FAILED=1
fi

echo ""

# ========== 步骤 3: 第2轮对话 ==========

log_step "步骤 3: 第2轮对话（应复用第1轮缓存）"

# 构造第2轮 messages（使用环境变量传递数据避免 shell 引号问题）
ROUND2_MESSAGES=$(ROUND1_CONTENT="$ROUND1_ASSISTANT_CONTENT" python3 -c "
import json, os
round1 = os.environ['ROUND1_CONTENT']
messages = [
    {'role': 'user', 'content': '你好'},
    {'role': 'assistant', 'content': round1},
    {'role': 'user', 'content': '今天天气怎么样'}
]
print(json.dumps(messages, ensure_ascii=False))
" 2>/dev/null)

if [ -z "$ROUND2_MESSAGES" ]; then
    log_error "构造第2轮 messages 失败"
    TEST_FAILED=1
fi

log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X POST '${CACHE_TEST_BASE_URL}/s/${CACHE_TEST_RUN_ID}/${CACHE_TEST_SESSION_ID}/v1/chat/completions' \\
    -H 'Content-Type: application/json' \\
    -H 'Authorization: Bearer ${CHAT_API_KEY}' \\
    -d '{
        \"model\": \"${CACHE_TEST_MODEL_NAME}\",
        \"messages\": ${ROUND2_MESSAGES},
        \"stream\": false
    }'"
log_separator

# shellcheck disable=SC2086
ROUND2_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${CACHE_TEST_BASE_URL}/s/${CACHE_TEST_RUN_ID}/${CACHE_TEST_SESSION_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${CACHE_TEST_MODEL_NAME}\",
        \"messages\": ${ROUND2_MESSAGES},
        \"stream\": false
    }")

ROUND2_BODY=$(echo "$ROUND2_RESPONSE" | sed '$d')
ROUND2_STATUS=$(echo "$ROUND2_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${ROUND2_STATUS}"
log_response "${ROUND2_BODY}"
log_separator

assert_http_status "200" "$ROUND2_STATUS" "第2轮对话 HTTP 状态码应为 200"
assert_contains "$ROUND2_BODY" "choices" "第2轮响应应包含 choices 字段"

# 提取第2轮 assistant 响应内容
ROUND2_ASSISTANT_CONTENT=$(echo "$ROUND2_BODY" | python3 -c "
import sys, json
data = json.loads(sys.stdin.read())
print(data['choices'][0]['message']['content'])
" 2>/dev/null)

if [ -n "$ROUND2_ASSISTANT_CONTENT" ]; then
    log_success "提取第2轮 assistant 响应成功"
else
    log_error "提取第2轮 assistant 响应失败"
    TEST_FAILED=1
fi

echo ""

# ========== 步骤 4: 第3轮对话 ==========

log_step "步骤 4: 第3轮对话（应复用第2轮缓存）"

# 构造第3轮 messages（使用环境变量传递数据避免 shell 引号问题）
ROUND3_MESSAGES=$(ROUND1_CONTENT="$ROUND1_ASSISTANT_CONTENT" ROUND2_CONTENT="$ROUND2_ASSISTANT_CONTENT" python3 -c "
import json, os
round1 = os.environ['ROUND1_CONTENT']
round2 = os.environ['ROUND2_CONTENT']
messages = [
    {'role': 'user', 'content': '你好'},
    {'role': 'assistant', 'content': round1},
    {'role': 'user', 'content': '今天天气怎么样'},
    {'role': 'assistant', 'content': round2},
    {'role': 'user', 'content': '谢谢'}
]
print(json.dumps(messages, ensure_ascii=False))
" 2>/dev/null)

if [ -z "$ROUND3_MESSAGES" ]; then
    log_error "构造第3轮 messages 失败"
    TEST_FAILED=1
fi

log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X POST '${CACHE_TEST_BASE_URL}/s/${CACHE_TEST_RUN_ID}/${CACHE_TEST_SESSION_ID}/v1/chat/completions' \\
    -H 'Content-Type: application/json' \\
    -H 'Authorization: Bearer ${CHAT_API_KEY}' \\
    -d '{
        \"model\": \"${CACHE_TEST_MODEL_NAME}\",
        \"messages\": ${ROUND3_MESSAGES},
        \"stream\": false
    }'"
log_separator

# shellcheck disable=SC2086
ROUND3_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${CACHE_TEST_BASE_URL}/s/${CACHE_TEST_RUN_ID}/${CACHE_TEST_SESSION_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${CACHE_TEST_MODEL_NAME}\",
        \"messages\": ${ROUND3_MESSAGES},
        \"stream\": false
    }")

ROUND3_BODY=$(echo "$ROUND3_RESPONSE" | sed '$d')
ROUND3_STATUS=$(echo "$ROUND3_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${ROUND3_STATUS}"
log_response "${ROUND3_BODY}"
log_separator

assert_http_status "200" "$ROUND3_STATUS" "第3轮对话 HTTP 状态码应为 200"
assert_contains "$ROUND3_BODY" "choices" "第3轮响应应包含 choices 字段"

echo ""

# ========== 步骤 5: 查询轨迹，验证缓存命中 ==========

log_step "步骤 5: 查询轨迹验证前缀缓存命中"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X GET '${CACHE_TEST_BASE_URL}/trajectory?session_id=${CACHE_TEST_SESSION_ID}&limit=100'"
log_separator

TRAJ_RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "${CACHE_TEST_BASE_URL}/trajectory?session_id=${CACHE_TEST_SESSION_ID}&limit=100")

TRAJ_BODY=$(echo "$TRAJ_RESPONSE" | sed '$d')
TRAJ_STATUS=$(echo "$TRAJ_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${TRAJ_STATUS}"
log_response "${TRAJ_BODY}"
log_separator

assert_http_status "200" "$TRAJ_STATUS" "查询轨迹 HTTP 状态码应为 200"

# 验证轨迹记录数为3
TRAJ_COUNT=$(json_get_number "$TRAJ_BODY" "count")
assert_eq "3" "$TRAJ_COUNT" "轨迹记录数应为 3"

# 使用 python3 提取每条记录的 cache_hit_tokens（按 start_time DESC 排序）
# records[0] = 第3轮（最新），records[1] = 第2轮，records[2] = 第1轮（最早）
CACHE_INFO=$(echo "$TRAJ_BODY" | python3 -c "
import sys, json
data = json.loads(sys.stdin.read())
records = data.get('records', [])
if len(records) >= 3:
    # 按 start_time 升序重新排列（最早的在前）
    sorted_records = sorted(records, key=lambda r: r.get('start_time', ''))
    r1 = sorted_records[0]  # 第1轮（最早）
    r2 = sorted_records[1]  # 第2轮
    r3 = sorted_records[2]  # 第3轮（最新）
    print(f\"{r1.get('cache_hit_tokens', -1)} {r2.get('cache_hit_tokens', -1)} {r3.get('cache_hit_tokens', -1)}\")
else:
    print('-1 -1 -1')
" 2>/dev/null)

ROUND1_CACHE=$(echo "$CACHE_INFO" | awk '{print $1}')
ROUND2_CACHE=$(echo "$CACHE_INFO" | awk '{print $2}')
ROUND3_CACHE=$(echo "$CACHE_INFO" | awk '{print $3}')

log_info "第1轮 cache_hit_tokens: ${ROUND1_CACHE}"
log_info "第2轮 cache_hit_tokens: ${ROUND2_CACHE}"
log_info "第3轮 cache_hit_tokens: ${ROUND3_CACHE}"

# 验证第1轮：cache_hit_tokens == 0（无历史对话）
assert_eq "0" "$ROUND1_CACHE" "第1轮 cache_hit_tokens 应为 0（无历史）"

# 验证第2轮：cache_hit_tokens > 0（复用第1轮）
if [ "$ROUND2_CACHE" -gt 0 ] 2>/dev/null; then
    log_success "第2轮 cache_hit_tokens=${ROUND2_CACHE} > 0，已复用第1轮缓存"
else
    log_error "第2轮 cache_hit_tokens 应 > 0（复用第1轮），实际为: ${ROUND2_CACHE}"
    TEST_FAILED=1
fi

# 验证第3轮：cache_hit_tokens > 第2轮的 cache_hit_tokens（复用第2轮，对话更长）
if [ "$ROUND3_CACHE" -gt "$ROUND2_CACHE" ] 2>/dev/null; then
    log_success "第3轮 cache_hit_tokens=${ROUND3_CACHE} > 第2轮 ${ROUND2_CACHE}，已复用第2轮缓存"
else
    log_error "第3轮 cache_hit_tokens 应 > 第2轮 ${ROUND2_CACHE}（复用第2轮），实际为: ${ROUND3_CACHE}"
    TEST_FAILED=1
fi

echo ""

# ========== 步骤 6: 删除模型 ==========

log_step "步骤 6: 删除模型（run_id: ${CACHE_TEST_RUN_ID}）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X DELETE '${CACHE_TEST_BASE_URL}/models?model_name=${CACHE_TEST_MODEL_NAME}&run_id=${CACHE_TEST_RUN_ID}'"
log_separator

DELETE_RESPONSE=$(curl -s -w "\n%{http_code}" -X DELETE "${CACHE_TEST_BASE_URL}/models?model_name=${CACHE_TEST_MODEL_NAME}&run_id=${CACHE_TEST_RUN_ID}")

DELETE_BODY=$(echo "$DELETE_RESPONSE" | sed '$d')
DELETE_STATUS=$(echo "$DELETE_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${DELETE_STATUS}"
log_response "${DELETE_BODY}"
log_separator

assert_http_status "200" "$DELETE_STATUS" "删除模型 HTTP 状态码应为 200"

DELETE_RESULT=$(json_get "$DELETE_BODY" "status")
assert_eq "success" "$DELETE_RESULT" "删除模型应返回 success"

DELETE_DELETED=$(json_get_bool "$DELETE_BODY" "deleted")
assert_eq "true" "$DELETE_DELETED" "deleted 应为 true"

echo ""

# 打印测试摘要
print_summary
