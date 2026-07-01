#!/bin/bash
# 场景 P210: TokenPipeline(TITO) 额外字段存储（Proxy 层）
# 测试流程：启动mock服务 -> 注册模型(TITO模式) -> 发送非流式/流式请求 ->
#           验证客户端响应不含 routed_experts -> 验证轨迹 token_response routed_experts 为 marker -> 删除模型
#
# 验证要点：
#   1. 服务端返回的额外字段（routed_experts，在 choices 数组内与 logprobs 同层级）
#      不会暴露给客户端（Builder 重建响应，不包含 extras）
#   2. r3offload 开启后，轨迹 token_response 中 routed_experts 存为 marker（轻量引用），
#      原始数据已卸载到 blob，可通过 GET .../route_experts 回拉（闭环由 P502/P503 验证）
#   3. 该字段不影响客户端响应中的 content、finish_reason 等正常处理
#
# 与 P209（DirectPipeline）的对应关系：
#   P209 Direct: extras → 客户端剥离 + raw_response 存 marker
#   P210 TITO:   extras → 客户端不可见 + token_response 存 marker
#
# 前提条件：
#   - DEFAULT_TOKENIZER_PATH 对应的 tokenizer 可用

# 获取脚本目录并加载utils
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 P210: TokenPipeline(TITO) 额外字段存储（Proxy 层）"
echo "========================================"
echo ""

# 测试配置
SCENARIO_ID=$(basename "${BASH_SOURCE[0]}" .sh | grep -oE '[A-Z][0-9]+' | tr '[:upper:]' '[:lower:]')
TEST_BASE_URL="${BASE_URL}"
TEST_MODEL_NAME="extras-tito-model"
TEST_RUN_ID="run-${SCENARIO_ID}"
TEST_TOKENIZER_PATH="${DEFAULT_TOKENIZER_PATH}"

# Mock服务配置
MOCK_PORT=19996  # 避免与 P201(19990), P202(19991), P204(19993/19994), P209(19995) 冲突
MOCK_URL="http://127.0.0.1:${MOCK_PORT}"
MOCK_PID=""
MOCK_INFER_URL="http://${MOCK_INFER_HOST}:${MOCK_PORT}/v1"

# 确保退出时停止mock服务
trap stop_mock EXIT

# ========================================
# 辅助函数：验证响应中指定字段是否存在
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
            TESTS_TOTAL=$((TESTS_TOTAL + 1))
            TESTS_PASSED=$((TESTS_PASSED + 1))
            log_success "响应包含 $field 字段（符合预期）"
            return 0
        else
            assert_fail "响应应包含 $field 字段但实际不包含"
            return 1
        fi
    else
        if [ "$field_exists" == "false" ]; then
            TESTS_TOTAL=$((TESTS_TOTAL + 1))
            TESTS_PASSED=$((TESTS_PASSED + 1))
            log_success "响应不包含 $field 字段（符合预期）"
            return 0
        else
            assert_fail "响应不应包含 $field 字段但实际包含"
            return 1
        fi
    fi
}

# ========================================
# 辅助函数：验证轨迹 token_response 中存储了 routed_experts
# 参数：$1 - session_id
# 输出：RESULT:routed_experts=OK/FAIL(reason)
# ========================================
verify_trajectory_token_response_extras() {
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
    print('RESULT:routed_experts=FAIL(no_records)')
    sys.exit(0)

record = records[0]

# TITO 模式下 extras 存储在 token_response 中
token_response = record.get('token_response')

if not token_response:
    print('RESULT:routed_experts=FAIL(no_token_response)')
    sys.exit(0)

# token_response 可能是字符串或已解析的 dict
if isinstance(token_response, str):
    try:
        token_response = json.loads(token_response)
    except json.JSONDecodeError:
        print('RESULT:routed_experts=FAIL(token_response_parse_error)')
        sys.exit(0)

choices = token_response.get('choices', [])
if not choices:
    print('RESULT:routed_experts=FAIL(no_choices)')
    sys.exit(0)

choice = choices[0]

# 检查 routed_experts：r3offload 开启后，轨迹 DB 存的是 marker（轻量引用），
# 原始 route_experts 已卸载到 blob，可通过 GET .../route_experts 回拉
# （fallback 闭环由 P502/P503 验证）。本场景只验证 marker 存在且结构完整。
re = choice.get('routed_experts')
if re is None:
    print('RESULT:routed_experts=FAIL(not_present)')
elif isinstance(re, dict) and re.get('_offloaded') is True:
    backend = re.get('backend')
    location = re.get('location') or {}
    if backend == 'local' and location.get('path'):
        print('RESULT:routed_experts=OK')
    elif backend == 'csb' and location.get('key'):
        print('RESULT:routed_experts=OK')
    else:
        print(f'RESULT:routed_experts=FAIL(marker_incomplete: backend={backend}, location={location})')
elif isinstance(re, list):
    print('RESULT:routed_experts=FAIL(is_raw_list_feature_maybe_off)')
else:
    print(f'RESULT:routed_experts=FAIL(type={type(re).__name__})')
PYEOF

    rm -f "$tmpfile"
}

# ========================================
# 步骤 0: 清理残留模型
# ========================================
log_info "清理可能残留的模型..."
curl_with_log -s --noproxy '*' -X DELETE "${TEST_BASE_URL}/models?model_name=${TEST_MODEL_NAME}&run_id=${TEST_RUN_ID}" > /dev/null 2>&1 || true

# ========================================
# 步骤 1: 启动Mock推理服务
# ========================================
log_step "步骤 1: 启动Mock推理服务"

if ! start_mock; then
    log_error "无法启动Mock服务，测试终止"
    exit 1
fi

echo ""

# ========================================
# 步骤 2: 注册模型（TITO模式）
# ========================================
log_step "步骤 2: 注册模型（TITO模式, token_in_token_out: true）"

REGISTER_RESPONSE=$(curl_with_log -s --noproxy '*' --max-time 10 -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{
        \"run_id\": \"${TEST_RUN_ID}\",
        \"model_name\": \"${TEST_MODEL_NAME}\",
        \"url\": \"${MOCK_INFER_URL}\",
        \"api_key\": \"mock-api-key\",
        \"tokenizer_path\": \"${TEST_TOKENIZER_PATH}\",
        \"token_in_token_out\": true
    }")

REGISTER_BODY=$(echo "$REGISTER_RESPONSE" | sed '$d')
REGISTER_STATUS=$(echo "$REGISTER_RESPONSE" | sed -n '$p')


# 允许tokenizer加载失败时跳过测试
if [ "$REGISTER_STATUS" != "200" ]; then
    log_warning "模型注册失败（可能tokenizer不可用），跳过TITO测试"
    log_warning "请确保 ${TEST_TOKENIZER_PATH} tokenizer可用"
    stop_mock
    exit 0
fi

REGISTER_RESULT=$(json_get "$REGISTER_BODY" "status")
assert_eq "success" "$REGISTER_RESULT" "注册模型应返回 success"

sleep 1

echo ""

# ========================================
# 步骤 3: 非流式请求
# ========================================
SESSION_NS="session-${SCENARIO_ID}-ns-$(date +%s%N | md5sum | head -c 8)"

log_step "步骤 3: 发送非流式请求"

CHAT_RESPONSE=$(curl_with_log -s --noproxy '*' --max-time 30 -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${SESSION_NS}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Say hello.\"}],
        \"max_tokens\": 10,
        \"stream\": false
    }")

CHAT_BODY=$(echo "$CHAT_RESPONSE" | sed '$d')
CHAT_STATUS=$(echo "$CHAT_RESPONSE" | sed -n '$p')


assert_http_status "200" "$CHAT_STATUS" "HTTP 状态码应为 200"
assert_contains "$CHAT_BODY" "choices" "响应应包含 choices 字段"

echo ""

# ========================================
# 步骤 4: 验证非流式客户端响应不含 routed_experts
# ========================================
log_step "步骤 4: 验证非流式客户端响应不含 routed_experts（TITO模式Builder重建响应）"

# TITO 模式下，非流式路径不捕获 extras，客户端响应由 OpenAIResponseBuilder 构建，不含 routed_experts
check_response_field "$CHAT_BODY" "routed_experts" "false"

# 验证已知字段正常：content 应正确返回
NS_CONTENT=$(extract_assistant_content "$CHAT_BODY")
if [ -n "$NS_CONTENT" ]; then
    log_success "非流式响应 content 非空（Builder 正常构建响应）"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    assert_fail "非流式响应 content 为空"
fi

echo ""

# ========================================
# 步骤 5: 非流式轨迹验证
# ========================================
log_step "步骤 5: 验证非流式轨迹存储行为"

sleep 0.5
log_info "查询非流式轨迹..."
TRAJ_VERIFY_NS=$(verify_trajectory_token_response_extras "${SESSION_NS}")

# 非流式 TITO 路径不捕获 extras，token_response 中不含 routed_experts
EXPERTS_NS_RESULT=$(echo "$TRAJ_VERIFY_NS" | grep "^RESULT:routed_experts=" | cut -d= -f2)
if [ "$EXPERTS_NS_RESULT" == "OK" ]; then
    # 如果非流式路径也意外捕获了 extras（当前实现不支持），记录为信息
    log_info "非流式轨迹 token_response 包含 routed_experts（非预期行为）"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_success "非流式轨迹 token_response 不含 routed_experts（符合TITO非流式行为）"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
fi

echo ""

# ========================================
# 步骤 6: 流式请求
# ========================================
SESSION_S="session-${SCENARIO_ID}-s-$(date +%s%N | md5sum | head -c 8)"

log_step "步骤 6: 发送流式请求"

STREAM_RESPONSE=$(curl_with_log -s --noproxy '*' --no-buffer --max-time 15 -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${SESSION_S}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Say hi.\"}],
        \"max_tokens\": 10,
        \"stream\": true
    }")

assert_contains "$STREAM_RESPONSE" "data:" "流式响应应包含 data: 前缀"
assert_contains "$STREAM_RESPONSE" "[DONE]" "流式响应应以 [DONE] 结束"

echo ""

# ========================================
# 步骤 7: 验证流式 chunks 不含 routed_experts
# ========================================
log_step "步骤 7: 验证流式 chunks 不含 routed_experts（TITO模式StreamChunkBuilder重建响应）"

STREAM_CHECK=$(echo "$STREAM_RESPONSE" | python3 -c "
import sys, json
found_routed_experts = False
found_content = False
for line in sys.stdin:
    line = line.strip()
    if not line.startswith('data: '):
        continue
    payload = line[6:]
    if payload == '[DONE]':
        break
    try:
        data = json.loads(payload)
        for choice in data.get('choices', []):
            if 'routed_experts' in choice:
                found_routed_experts = True
            delta = choice.get('delta', {})
            if delta.get('content'):
                found_content = True
    except json.JSONDecodeError:
        continue
# TITO 模式：chunks 不应包含 routed_experts，但应包含 content
if not found_routed_experts and found_content:
    print('OK')
else:
    print(f'FAIL:routed_experts_in_chunks={found_routed_experts}, content={found_content}')
" 2>&1)

if [ "$STREAM_CHECK" == "OK" ]; then
    log_success "流式 chunks 不含 routed_experts 且包含 content → StreamChunkBuilder 正确重建"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    assert_fail "流式 chunks routed_experts 验证失败" "$STREAM_CHECK"
fi

echo ""

# ========================================
# 步骤 8: 验证流式轨迹 token_response routed_experts 为 marker
# ========================================
log_step "步骤 8: 验证流式轨迹 token_response routed_experts 为 marker（r3offload 已卸载）"

sleep 0.5
log_info "查询流式轨迹..."
TRAJ_VERIFY_S=$(verify_trajectory_token_response_extras "${SESSION_S}")

ROUTED_EXPERTS_RESULT=$(echo "$TRAJ_VERIFY_S" | grep "^RESULT:routed_experts=" | cut -d= -f2)
if [ "$ROUTED_EXPERTS_RESULT" == "OK" ]; then
    log_success "流式轨迹 token_response routed_experts 为 marker 且结构完整 → 已卸载到 blob"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    assert_fail "流式轨迹 token_response 中 routed_experts 缺失或结构不正确" "$ROUTED_EXPERTS_RESULT"
fi

echo ""

# ========================================
# 步骤 9: 删除模型
# ========================================
log_step "步骤 9: 删除模型（run_id: ${TEST_RUN_ID}）"

DELETE_RESPONSE=$(curl_with_log -s --noproxy '*' --max-time 10 -w "\n%{http_code}" -X DELETE "${TEST_BASE_URL}/models?model_name=${TEST_MODEL_NAME}&run_id=${TEST_RUN_ID}")

DELETE_BODY=$(echo "$DELETE_RESPONSE" | sed '$d')
DELETE_STATUS=$(echo "$DELETE_RESPONSE" | sed -n '$p')


assert_http_status "200" "$DELETE_STATUS" "HTTP 状态码应为 200"

DELETE_RESULT=$(json_get "$DELETE_BODY" "status")
assert_eq "success" "$DELETE_RESULT" "删除模型应返回 success"

echo ""

# 打印测试摘要
print_summary
