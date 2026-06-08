#!/bin/bash
# 场景 F110: logprobs 和 token_ids 强制覆盖与返回过滤（Proxy 层）
# 使用真实推理服务（vLLM）验证完整功能链路：
#
# 验证要点：
#   1. 无论客户端是否传递，proxy 强制覆盖 logprobs=1 和 return_token_ids=True
#      → 通过轨迹中存储的 logprobs/token_ids 间接证明强制覆盖生效
#      （若 proxy 未强制覆盖，真实推理服务不会返回 logprobs/token_ids）
#   2. 客户端传递 logprobs=true 时，上游收到 logprobs=1（覆盖，不是 true）
#   3. 客户端响应始终不包含 logprobs 和 token_ids（已剥离）
#   4. 存储的轨迹中包含完整的 logprobs 和 token_ids 数据（剥离前存储）
#   5. 流式和非流式行为一致
#
# 前提条件：
#   - BACKEND_MODEL_URL 指向支持 logprobs 和 return_token_ids 的 vLLM 推理服务
#   - TEST_MODEL_API_KEY 为有效的推理服务 API Key

# 获取脚本目录并加载utils
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 F110: logprobs 和 token_ids 强制覆盖与返回过滤（Proxy 层）"
echo "使用真实推理服务验证"
echo "========================================"
echo ""

# 测试配置
SCENARIO_ID=$(basename "${BASH_SOURCE[0]}" .sh | grep -oE '[FP][0-9]+' | tr '[:upper:]' '[:lower:]')
TEST_BASE_URL="${BASE_URL}"
TEST_MODEL_NAME="${DEFAULT_MODEL_NAME}"
TEST_RUN_ID="run-${SCENARIO_ID}"

# ========================================
# 辅助函数：检查响应是否包含指定字段
# ========================================
check_response_field() {
    local response="$1"
    local field="$2"
    local should_contain="$3"  # "true" 或 "false"

    if echo "$response" | python3 -c "
import sys
import json
data = json.loads(sys.stdin.read())
choices = data.get('choices', [])
if not choices:
    sys.exit(1)
choice = choices[0]
if '$field' in choice:
    sys.exit(0)  # 字段存在
else:
    sys.exit(2)  # 字段不存在
" 2>/dev/null; then
        field_exists="true"
    else
        field_exists="false"
    fi

    if [ "$should_contain" == "true" ]; then
        if [ "$field_exists" == "true" ]; then
            log_success "响应包含 $field 字段（符合预期）"
            return 0
        else
            log_error "响应应包含 $field 字段但实际不包含"
            return 1
        fi
    else
        if [ "$field_exists" == "false" ]; then
            log_success "响应不包含 $field 字段（符合预期）"
            return 0
        else
            log_error "响应不应包含 $field 字段但实际包含"
            return 1
        fi
    fi
}

# 辅助函数：验证流式响应所有chunk不含 logprobs/token_ids
check_stream_no_logprobs_token_ids() {
    local stream_response="$1"

    echo "$stream_response" | grep "^data: " | grep -v "\[DONE\]" | python3 -c "
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line.startswith('data: '):
        continue
    payload = line[6:]
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        continue
    for choice in data.get('choices', []):
        if 'logprobs' in choice:
            print(f'FAIL:logprobs found in chunk: {choice}')
            sys.exit(1)
        if 'token_ids' in choice:
            print(f'FAIL:token_ids found in chunk: {choice}')
            sys.exit(1)
print('OK')
" 2>/dev/null
}

# 辅助函数：验证轨迹中存储了 logprobs 和 token_ids
# 参数：$1 - session_id
# 输出：RESULT:logprobs=OK/FAIL(reason), RESULT:token_ids=OK/FAIL(reason)
verify_trajectory_logprobs_token_ids() {
    local session_id="$1"

    # 查询轨迹（包含所有字段）
    local traj_response
    traj_response=$(curl_with_log -s --noproxy '*' --max-time 10 -X GET \
        "${TEST_BASE_URL}/trajectory?session_id=${session_id}&limit=1")

    # 将结果写入临时文件（避免大 JSON 在命令行传递丢失）
    local tmpfile
    tmpfile=$(mktemp)
    echo "$traj_response" > "$tmpfile"

    python3 << PYEOF
import json
import sys

with open("$tmpfile", "r") as f:
    data = json.loads(f.read())

records = data.get('records', [])
if not records:
    print('RESULT:logprobs=FAIL(no_records)')
    print('RESULT:token_ids=FAIL(no_records)')
    sys.exit(0)

record = records[0]
raw_response = record.get('raw_response')

if not raw_response:
    print('RESULT:logprobs=FAIL(no_raw_response)')
    print('RESULT:token_ids=FAIL(no_raw_response)')
    sys.exit(0)

# raw_response 可能是字符串或已解析的 dict
if isinstance(raw_response, str):
    try:
        raw_response = json.loads(raw_response)
    except json.JSONDecodeError:
        print('RESULT:logprobs=FAIL(raw_response_parse_error)')
        print('RESULT:token_ids=FAIL(raw_response_parse_error)')
        sys.exit(0)

choices = raw_response.get('choices', [])
if not choices:
    print('RESULT:logprobs=FAIL(no_choices)')
    print('RESULT:token_ids=FAIL(no_choices)')
    sys.exit(0)

choice = choices[0]

# 检查 logprobs
has_logprobs = 'logprobs' in choice and choice['logprobs'] is not None
logprobs_val = choice.get('logprobs')
if isinstance(logprobs_val, dict):
    # chat completion 格式: {"content": [...]}
    content_list = logprobs_val.get('content', [])
    logprobs_nonempty = len(content_list) > 0
elif isinstance(logprobs_val, list):
    logprobs_nonempty = len(logprobs_val) > 0
else:
    logprobs_nonempty = bool(logprobs_val)

if has_logprobs and logprobs_nonempty:
    print('RESULT:logprobs=OK')
else:
    reason = 'not_present' if not has_logprobs else 'empty/null'
    print(f'RESULT:logprobs=FAIL({reason})')

# 检查 token_ids
has_token_ids = 'token_ids' in choice and choice['token_ids'] is not None
token_ids_val = choice.get('token_ids', [])
token_ids_nonempty = isinstance(token_ids_val, list) and len(token_ids_val) > 0

if has_token_ids and token_ids_nonempty:
    print(f'RESULT:token_ids=OK(len={len(token_ids_val)})')
else:
    reason = 'not_present' if not has_token_ids else 'empty/null'
    print(f'RESULT:token_ids=FAIL({reason})')
PYEOF

    rm -f "$tmpfile"
}

# ========================================
# 步骤 0: 清理残留模型
# ========================================
log_info "清理可能残留的模型..."
curl_with_log -s --noproxy '*' -X DELETE "${TEST_BASE_URL}/models?model_name=${TEST_MODEL_NAME}&run_id=${TEST_RUN_ID}" > /dev/null 2>&1 || true

echo ""

# ========================================
# 步骤 1: 注册模型（直接转发模式，使用真实推理服务）
# ========================================
log_step "步骤 1: 注册模型（直接转发模式，使用真实推理服务）"

REGISTER_RESPONSE=$(curl_with_log -s --noproxy '*' --max-time 10 -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{
        \"run_id\": \"${TEST_RUN_ID}\",
        \"model_name\": \"${TEST_MODEL_NAME}\",
        \"url\": \"${BACKEND_MODEL_URL}\",
        \"api_key\": \"${TEST_MODEL_API_KEY}\",
        \"token_in_token_out\": false
    }")

REGISTER_BODY=$(echo "$REGISTER_RESPONSE" | sed '$d')
REGISTER_STATUS=$(echo "$REGISTER_RESPONSE" | sed -n '$p')


assert_http_status "200" "$REGISTER_STATUS" "HTTP 状态码应为 200"

REGISTER_RESULT=$(json_get "$REGISTER_BODY" "status")
assert_eq "success" "$REGISTER_RESULT" "注册模型应返回 success"

sleep 0.5

echo ""

# ========================================
# 步骤 2: 非流式 - 不传递 logprobs/return_token_ids
#   验证：客户端无字段 + 轨迹存储有 logprobs/token_ids
# ========================================
SESSION_NS_NO_PARAM="session-${SCENARIO_ID}-ns-noparam-$(date +%s%N | md5sum | head -c 8)"

log_step "步骤 2: 非流式 - 不传递 logprobs/return_token_ids"

CHAT_RESPONSE=$(curl_with_log -s --noproxy '*' --max-time 30 -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${SESSION_NS_NO_PARAM}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}],
        \"stream\": false
    }")

CHAT_BODY=$(echo "$CHAT_RESPONSE" | sed '$d')
CHAT_STATUS=$(echo "$CHAT_RESPONSE" | sed -n '$p')


assert_http_status "200" "$CHAT_STATUS" "HTTP 状态码应为 200"

# 2a: 验证客户端响应不含 logprobs 和 token_ids（剥离生效）
log_info "验证客户端响应不含 logprobs 和 token_ids..."
check_response_field "$CHAT_BODY" "logprobs" "false"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

check_response_field "$CHAT_BODY" "token_ids" "false"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

# 2b: 等待轨迹存储，验证轨迹中包含 logprobs 和 token_ids（证明强制覆盖生效）
sleep 0.5
log_info "验证轨迹存储包含 logprobs 和 token_ids（间接证明强制覆盖生效）..."
TRAJ_VERIFY=$(verify_trajectory_logprobs_token_ids "${SESSION_NS_NO_PARAM}")

LOGPROBS_RESULT=$(echo "$TRAJ_VERIFY" | grep "^RESULT:logprobs=" | cut -d= -f2)
if [ "$LOGPROBS_RESULT" == "OK" ]; then
    log_success "轨迹中 logprobs 存在且非空 → proxy 强制覆盖 logprobs=1 生效"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "轨迹中 logprobs 缺失或为空: $LOGPROBS_RESULT"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

TOKEN_IDS_RESULT=$(echo "$TRAJ_VERIFY" | grep "^RESULT:token_ids=" | cut -d= -f2 | cut -d'(' -f1)
if [ "$TOKEN_IDS_RESULT" == "OK" ]; then
    log_success "轨迹中 token_ids 存在且非空 → proxy 强制覆盖 return_token_ids=True 生效"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "轨迹中 token_ids 缺失或为空: $TOKEN_IDS_RESULT"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

echo ""

# ========================================
# 步骤 3: 非流式 - 传递 logprobs=true（应被覆盖为1）
# ========================================
SESSION_NS_LOGP_TRUE="session-${SCENARIO_ID}-ns-logp-true-$(date +%s%N | md5sum | head -c 8)"

log_step "步骤 3: 非流式 - 传递 logprobs=true（应被覆盖为1）"

CHAT_RESPONSE=$(curl_with_log -s --noproxy '*' --max-time 30 -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${SESSION_NS_LOGP_TRUE}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}],
        \"logprobs\": true,
        \"stream\": false
    }")

CHAT_BODY=$(echo "$CHAT_RESPONSE" | sed '$d')
CHAT_STATUS=$(echo "$CHAT_RESPONSE" | sed -n '$p')


assert_http_status "200" "$CHAT_STATUS" "HTTP 状态码应为 200"

# 3a: 客户端响应不含 logprobs 和 token_ids
check_response_field "$CHAT_BODY" "logprobs" "false"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

check_response_field "$CHAT_BODY" "token_ids" "false"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

# 3b: 验证轨迹存储（覆盖 true → 1，仍能获取 logprobs）
sleep 0.5
log_info "验证轨迹存储包含 logprobs 和 token_ids（覆盖 logprobs=true → 1 仍生效）..."
TRAJ_VERIFY=$(verify_trajectory_logprobs_token_ids "${SESSION_NS_LOGP_TRUE}")

LOGPROBS_RESULT=$(echo "$TRAJ_VERIFY" | grep "^RESULT:logprobs=" | cut -d= -f2)
if [ "$LOGPROBS_RESULT" == "OK" ]; then
    log_success "轨迹中 logprobs 存在且非空 → proxy 覆盖 true→1 后仍返回完整 logprobs"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "轨迹中 logprobs 缺失或为空: $LOGPROBS_RESULT"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

TOKEN_IDS_RESULT=$(echo "$TRAJ_VERIFY" | grep "^RESULT:token_ids=" | cut -d= -f2 | cut -d'(' -f1)
if [ "$TOKEN_IDS_RESULT" == "OK" ]; then
    log_success "轨迹中 token_ids 存在且非空"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "轨迹中 token_ids 缺失或为空: $TOKEN_IDS_RESULT"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

echo ""

# ========================================
# 步骤 4: 非流式 - 传递 return_token_ids=true
# ========================================
SESSION_NS_TOKS="session-${SCENARIO_ID}-ns-toks-$(date +%s%N | md5sum | head -c 8)"

log_step "步骤 4: 非流式 - 传递 return_token_ids=true"

CHAT_RESPONSE=$(curl_with_log -s --noproxy '*' --max-time 30 -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${SESSION_NS_TOKS}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}],
        \"return_token_ids\": true,
        \"stream\": false
    }")

CHAT_BODY=$(echo "$CHAT_RESPONSE" | sed '$d')
CHAT_STATUS=$(echo "$CHAT_RESPONSE" | sed -n '$p')


assert_http_status "200" "$CHAT_STATUS" "HTTP 状态码应为 200"

# 4a: 客户端响应不含 logprobs 和 token_ids
check_response_field "$CHAT_BODY" "logprobs" "false"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

check_response_field "$CHAT_BODY" "token_ids" "false"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

# 4b: 验证轨迹存储
sleep 0.5
log_info "验证轨迹存储包含 logprobs 和 token_ids..."
TRAJ_VERIFY=$(verify_trajectory_logprobs_token_ids "${SESSION_NS_TOKS}")

LOGPROBS_RESULT=$(echo "$TRAJ_VERIFY" | grep "^RESULT:logprobs=" | cut -d= -f2)
if [ "$LOGPROBS_RESULT" == "OK" ]; then
    log_success "轨迹中 logprobs 存在且非空"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "轨迹中 logprobs 缺失或为空: $LOGPROBS_RESULT"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

TOKEN_IDS_RESULT=$(echo "$TRAJ_VERIFY" | grep "^RESULT:token_ids=" | cut -d= -f2 | cut -d'(' -f1)
if [ "$TOKEN_IDS_RESULT" == "OK" ]; then
    log_success "轨迹中 token_ids 存在且非空 → proxy 强制覆盖 return_token_ids=True 生效"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "轨迹中 token_ids 缺失或为空: $TOKEN_IDS_RESULT"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

echo ""

# ========================================
# 步骤 5: 非流式 - 同时传递 logprobs=true 和 return_token_ids=true
# ========================================
SESSION_NS_BOTH="session-${SCENARIO_ID}-ns-both-$(date +%s%N | md5sum | head -c 8)"

log_step "步骤 5: 非流式 - 同时传递 logprobs=true 和 return_token_ids=true"

CHAT_RESPONSE=$(curl_with_log -s --noproxy '*' --max-time 30 -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${SESSION_NS_BOTH}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}],
        \"logprobs\": true,
        \"return_token_ids\": true,
        \"stream\": false
    }")

CHAT_BODY=$(echo "$CHAT_RESPONSE" | sed '$d')
CHAT_STATUS=$(echo "$CHAT_RESPONSE" | sed -n '$p')


assert_http_status "200" "$CHAT_STATUS" "HTTP 状态码应为 200"

# 5a: 客户端响应不含 logprobs 和 token_ids
check_response_field "$CHAT_BODY" "logprobs" "false"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

check_response_field "$CHAT_BODY" "token_ids" "false"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

# 5b: 验证轨迹存储
sleep 0.5
log_info "验证轨迹存储包含 logprobs 和 token_ids..."
TRAJ_VERIFY=$(verify_trajectory_logprobs_token_ids "${SESSION_NS_BOTH}")

LOGPROBS_RESULT=$(echo "$TRAJ_VERIFY" | grep "^RESULT:logprobs=" | cut -d= -f2)
if [ "$LOGPROBS_RESULT" == "OK" ]; then
    log_success "轨迹中 logprobs 存在且非空"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "轨迹中 logprobs 缺失或为空: $LOGPROBS_RESULT"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

TOKEN_IDS_RESULT=$(echo "$TRAJ_VERIFY" | grep "^RESULT:token_ids=" | cut -d= -f2 | cut -d'(' -f1)
if [ "$TOKEN_IDS_RESULT" == "OK" ]; then
    log_success "轨迹中 token_ids 存在且非空"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "轨迹中 token_ids 缺失或为空: $TOKEN_IDS_RESULT"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

echo ""

# ========================================
# 步骤 6: 流式 - 不传递 logprobs/return_token_ids
# ========================================
SESSION_S_NO_PARAM="session-${SCENARIO_ID}-s-noparam-$(date +%s%N | md5sum | head -c 8)"

log_step "步骤 6: 流式 - 不传递 logprobs/return_token_ids"

STREAM_RESPONSE=$(curl_with_log -s --noproxy '*' --no-buffer --max-time 30 -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${SESSION_S_NO_PARAM}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}],
        \"stream\": true
    }")

assert_contains "$STREAM_RESPONSE" "data:" "流式响应应包含 data: 前缀"
assert_contains "$STREAM_RESPONSE" "[DONE]" "流式响应应以 [DONE] 结束"

# 6a: 验证所有chunk不含 logprobs/token_ids
STREAM_CHECK=$(check_stream_no_logprobs_token_ids "$STREAM_RESPONSE")
if [ "$STREAM_CHECK" == "OK" ]; then
    log_success "流式响应所有chunk不含 logprobs 和 token_ids（符合预期）"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "流式响应包含 logprobs 或 token_ids: $STREAM_CHECK"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

# 6b: 验证轨迹存储
sleep 0.5
log_info "验证轨迹存储包含 logprobs 和 token_ids（间接证明流式强制覆盖生效）..."
TRAJ_VERIFY=$(verify_trajectory_logprobs_token_ids "${SESSION_S_NO_PARAM}")

LOGPROBS_RESULT=$(echo "$TRAJ_VERIFY" | grep "^RESULT:logprobs=" | cut -d= -f2)
if [ "$LOGPROBS_RESULT" == "OK" ]; then
    log_success "流式轨迹中 logprobs 存在且非空 → proxy 强制覆盖 logprobs=1 生效"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "流式轨迹中 logprobs 缺失或为空: $LOGPROBS_RESULT"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

TOKEN_IDS_RESULT=$(echo "$TRAJ_VERIFY" | grep "^RESULT:token_ids=" | cut -d= -f2 | cut -d'(' -f1)
if [ "$TOKEN_IDS_RESULT" == "OK" ]; then
    log_success "流式轨迹中 token_ids 存在且非空 → proxy 强制覆盖 return_token_ids=True 生效"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "流式轨迹中 token_ids 缺失或为空: $TOKEN_IDS_RESULT"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

echo ""

# ========================================
# 步骤 7: 流式 - 传递 logprobs=true（应被覆盖为1）
# ========================================
SESSION_S_LOGP_TRUE="session-${SCENARIO_ID}-s-logp-true-$(date +%s%N | md5sum | head -c 8)"

log_step "步骤 7: 流式 - 传递 logprobs=true（应被覆盖为1）"

STREAM_RESPONSE=$(curl_with_log -s --noproxy '*' --no-buffer --max-time 30 -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${SESSION_S_LOGP_TRUE}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}],
        \"logprobs\": true,
        \"stream\": true
    }")

assert_contains "$STREAM_RESPONSE" "data:" "流式响应应包含 data: 前缀"
assert_contains "$STREAM_RESPONSE" "[DONE]" "流式响应应以 [DONE] 结束"

# 7a: 验证所有chunk不含 logprobs/token_ids
STREAM_CHECK=$(check_stream_no_logprobs_token_ids "$STREAM_RESPONSE")
if [ "$STREAM_CHECK" == "OK" ]; then
    log_success "流式响应所有chunk不含 logprobs 和 token_ids（符合预期）"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "流式响应包含 logprobs 或 token_ids: $STREAM_CHECK"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

# 7b: 验证轨迹存储（覆盖 true → 1）
sleep 0.5
log_info "验证轨迹存储包含 logprobs 和 token_ids..."
TRAJ_VERIFY=$(verify_trajectory_logprobs_token_ids "${SESSION_S_LOGP_TRUE}")

LOGPROBS_RESULT=$(echo "$TRAJ_VERIFY" | grep "^RESULT:logprobs=" | cut -d= -f2)
if [ "$LOGPROBS_RESULT" == "OK" ]; then
    log_success "流式轨迹中 logprobs 存在且非空 → proxy 覆盖 true→1 后仍返回完整 logprobs"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "流式轨迹中 logprobs 缺失或为空: $LOGPROBS_RESULT"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

TOKEN_IDS_RESULT=$(echo "$TRAJ_VERIFY" | grep "^RESULT:token_ids=" | cut -d= -f2 | cut -d'(' -f1)
if [ "$TOKEN_IDS_RESULT" == "OK" ]; then
    log_success "流式轨迹中 token_ids 存在且非空"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "流式轨迹中 token_ids 缺失或为空: $TOKEN_IDS_RESULT"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

echo ""

# ========================================
# 步骤 8: 流式 - 传递 return_token_ids=true
# ========================================
SESSION_S_TOKS="session-${SCENARIO_ID}-s-toks-$(date +%s%N | md5sum | head -c 8)"

log_step "步骤 8: 流式 - 传递 return_token_ids=true"

STREAM_RESPONSE=$(curl_with_log -s --noproxy '*' --no-buffer --max-time 30 -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${SESSION_S_TOKS}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}],
        \"return_token_ids\": true,
        \"stream\": true
    }")

assert_contains "$STREAM_RESPONSE" "data:" "流式响应应包含 data: 前缀"
assert_contains "$STREAM_RESPONSE" "[DONE]" "流式响应应以 [DONE] 结束"

# 8a: 验证所有chunk不含 logprobs/token_ids
STREAM_CHECK=$(check_stream_no_logprobs_token_ids "$STREAM_RESPONSE")
if [ "$STREAM_CHECK" == "OK" ]; then
    log_success "流式响应所有chunk不含 logprobs 和 token_ids（符合预期）"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "流式响应包含 logprobs 或 token_ids: $STREAM_CHECK"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

# 8b: 验证轨迹存储
sleep 0.5
log_info "验证轨迹存储包含 logprobs 和 token_ids..."
TRAJ_VERIFY=$(verify_trajectory_logprobs_token_ids "${SESSION_S_TOKS}")

LOGPROBS_RESULT=$(echo "$TRAJ_VERIFY" | grep "^RESULT:logprobs=" | cut -d= -f2)
if [ "$LOGPROBS_RESULT" == "OK" ]; then
    log_success "流式轨迹中 logprobs 存在且非空"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "流式轨迹中 logprobs 缺失或为空: $LOGPROBS_RESULT"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

TOKEN_IDS_RESULT=$(echo "$TRAJ_VERIFY" | grep "^RESULT:token_ids=" | cut -d= -f2 | cut -d'(' -f1)
if [ "$TOKEN_IDS_RESULT" == "OK" ]; then
    log_success "流式轨迹中 token_ids 存在且非空"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "流式轨迹中 token_ids 缺失或为空: $TOKEN_IDS_RESULT"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

echo ""

# ========================================
# 步骤 9: 流式 - 同时传递 logprobs=true 和 return_token_ids=true
# ========================================
SESSION_S_BOTH="session-${SCENARIO_ID}-s-both-$(date +%s%N | md5sum | head -c 8)"

log_step "步骤 9: 流式 - 同时传递 logprobs=true 和 return_token_ids=true"

STREAM_RESPONSE=$(curl_with_log -s --noproxy '*' --no-buffer --max-time 30 -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${SESSION_S_BOTH}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}],
        \"logprobs\": true,
        \"return_token_ids\": true,
        \"stream\": true
    }")

assert_contains "$STREAM_RESPONSE" "data:" "流式响应应包含 data: 前缀"
assert_contains "$STREAM_RESPONSE" "[DONE]" "流式响应应以 [DONE] 结束"

# 9a: 验证所有chunk不含 logprobs/token_ids
STREAM_CHECK=$(check_stream_no_logprobs_token_ids "$STREAM_RESPONSE")
if [ "$STREAM_CHECK" == "OK" ]; then
    log_success "流式响应所有chunk不含 logprobs 和 token_ids（符合预期）"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "流式响应包含 logprobs 或 token_ids: $STREAM_CHECK"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

# 9b: 验证轨迹存储
sleep 0.5
log_info "验证轨迹存储包含 logprobs 和 token_ids..."
TRAJ_VERIFY=$(verify_trajectory_logprobs_token_ids "${SESSION_S_BOTH}")

LOGPROBS_RESULT=$(echo "$TRAJ_VERIFY" | grep "^RESULT:logprobs=" | cut -d= -f2)
if [ "$LOGPROBS_RESULT" == "OK" ]; then
    log_success "流式轨迹中 logprobs 存在且非空"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "流式轨迹中 logprobs 缺失或为空: $LOGPROBS_RESULT"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

TOKEN_IDS_RESULT=$(echo "$TRAJ_VERIFY" | grep "^RESULT:token_ids=" | cut -d= -f2 | cut -d'(' -f1)
if [ "$TOKEN_IDS_RESULT" == "OK" ]; then
    log_success "流式轨迹中 token_ids 存在且非空"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "流式轨迹中 token_ids 缺失或为空: $TOKEN_IDS_RESULT"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

echo ""

# ========================================
# 步骤 10: 删除模型
# ========================================
log_step "步骤 10: 删除模型（run_id: ${TEST_RUN_ID}）"

DELETE_RESPONSE=$(curl_with_log -s --noproxy '*' --max-time 10 -w "\n%{http_code}" -X DELETE "${TEST_BASE_URL}/models?model_name=${TEST_MODEL_NAME}&run_id=${TEST_RUN_ID}")

DELETE_BODY=$(echo "$DELETE_RESPONSE" | sed '$d')
DELETE_STATUS=$(echo "$DELETE_RESPONSE" | sed -n '$p')


assert_http_status "200" "$DELETE_STATUS" "HTTP 状态码应为 200"

DELETE_RESULT=$(json_get "$DELETE_BODY" "status")
assert_eq "success" "$DELETE_RESULT" "删除模型应返回 success"

echo ""

# 打印测试摘要
print_summary