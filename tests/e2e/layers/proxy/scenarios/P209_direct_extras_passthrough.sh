#!/bin/bash
# 场景 P209: DirectPipeline 额外字段透传（Proxy 层）
# 测试流程：启动mock服务 -> 注册模型(直接模式) -> 发送非流式/流式请求 ->
#           验证客户端响应含 routed_experts -> 验证轨迹存储含 routed_experts -> 删除模型
#
# 验证要点：
#   1. 服务端流式返回的额外字段（routed_experts，在 choices 数组内与 logprobs 同层级）
#      被正确透传给客户端（非流式和流式）
#   2. 轨迹存储的 raw_response 中包含该额外字段
#   3. 该字段不影响已知字段（logprobs, token_ids, content 等）的正常处理

# 获取脚本目录并加载utils
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 P209: DirectPipeline 额外字段透传（Proxy 层）"
echo "========================================"
echo ""

# 测试配置
SCENARIO_ID=$(basename "${BASH_SOURCE[0]}" .sh | grep -oE '[A-Z][0-9]+' | tr '[:upper:]' '[:lower:]')
TEST_BASE_URL="${BASE_URL}"
TEST_MODEL_NAME="extras-direct-model"
TEST_RUN_ID="run-${SCENARIO_ID}"
TEST_SESSION_ID="session-${SCENARIO_ID}-$(date +%s%N | md5sum | head -c 8)"

# Mock服务配置
MOCK_PORT=19995  # 避免与 P201(19990), P202(19991), P204(19993/19994) 冲突
MOCK_URL="http://127.0.0.1:${MOCK_PORT}"
MOCK_PID=""
MOCK_INFER_URL="http://${MOCK_INFER_HOST}:${MOCK_PORT}/v1"

# 确保退出时停止mock服务
trap stop_mock EXIT

# ========================================
# 辅助函数：验证轨迹中存储了 routed_experts
# 参数：$1 - session_id
# 输出：RESULT:routed_experts=OK/FAIL(reason)
# ========================================
verify_trajectory_routed_experts() {
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
raw_response = record.get('raw_response')

if not raw_response:
    print('RESULT:routed_experts=FAIL(no_raw_response)')
    sys.exit(0)

# raw_response 可能是字符串或已解析的 dict
if isinstance(raw_response, str):
    try:
        raw_response = json.loads(raw_response)
    except json.JSONDecodeError:
        print('RESULT:routed_experts=FAIL(raw_response_parse_error)')
        sys.exit(0)

choices = raw_response.get('choices', [])
if not choices:
    print('RESULT:routed_experts=FAIL(no_choices)')
    sys.exit(0)

choice = choices[0]

# 检查 routed_experts
has_routed_experts = 'routed_experts' in choice and choice['routed_experts'] is not None
if has_routed_experts:
    experts = choice['routed_experts']
    # 验证结构：应为列表，含2个expert，expert_id/weight正确
    if isinstance(experts, list) and len(experts) == 2:
        e0 = experts[0]
        e1 = experts[1]
        if e0.get('expert_id') == 2 and e0.get('weight') == 0.7 and \
           e1.get('expert_id') == 5 and e1.get('weight') == 0.3:
            print('RESULT:routed_experts=OK')
        else:
            print(f'RESULT:routed_experts=FAIL(expert_content_mismatch)')
    else:
        print(f'RESULT:routed_experts=FAIL(list_len={len(experts) if isinstance(experts, list) else "not_list"})')
else:
    print('RESULT:routed_experts=FAIL(not_present)')
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
# 步骤 2: 注册模型（直接转发模式）
# ========================================
log_step "步骤 2: 注册模型（直接转发模式, token_in_token_out: false）"

REGISTER_RESPONSE=$(curl_with_log -s --noproxy '*' --max-time 10 -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{
        \"run_id\": \"${TEST_RUN_ID}\",
        \"model_name\": \"${TEST_MODEL_NAME}\",
        \"url\": \"${MOCK_INFER_URL}\",
        \"api_key\": \"mock-api-key\",
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
# 步骤 3: 非流式请求
# ========================================
SESSION_NS="session-${SCENARIO_ID}-ns-$(date +%s%N | md5sum | head -c 8)"

log_step "步骤 3: 发送非流式请求（带 logprobs=true）"

CHAT_RESPONSE=$(curl_with_log -s --noproxy '*' --max-time 30 -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${SESSION_NS}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Say hello.\"}],
        \"max_tokens\": 10,
        \"logprobs\": true,
        \"stream\": false
    }")

CHAT_BODY=$(echo "$CHAT_RESPONSE" | sed '$d')
CHAT_STATUS=$(echo "$CHAT_RESPONSE" | sed -n '$p')


assert_http_status "200" "$CHAT_STATUS" "HTTP 状态码应为 200"
assert_contains "$CHAT_BODY" "choices" "响应应包含 choices 字段"

echo ""

# ========================================
# 步骤 4: 验证非流式客户端响应包含 routed_experts
# ========================================
log_step "步骤 4: 验证非流式客户端响应包含 routed_experts"

NS_CHECK=$(echo "$CHAT_BODY" | python3 -c "
import sys, json
data = json.loads(sys.stdin.read())
choice = data['choices'][0]
# 验证 routed_experts 存在且结构正确
assert 'routed_experts' in choice, 'routed_experts missing from choice'
experts = choice['routed_experts']
assert isinstance(experts, list), 'routed_experts should be a list'
assert len(experts) == 2, f'expected 2 experts, got {len(experts)}'
assert experts[0]['expert_id'] == 2, 'first expert_id mismatch'
assert experts[0]['weight'] == 0.7, 'first weight mismatch'
assert experts[1]['expert_id'] == 5, 'second expert_id mismatch'
assert experts[1]['weight'] == 0.3, 'second weight mismatch'
# 验证已知字段仍然正常
assert 'message' in choice, 'message field missing'
assert choice.get('finish_reason') == 'stop', 'finish_reason should be stop'
print('OK')
" 2>&1)

if [ "$NS_CHECK" == "OK" ]; then
    log_success "非流式客户端响应包含 routed_experts 且结构正确，已知字段正常"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    assert_fail "非流式客户端响应 routed_experts 验证失败" "$NS_CHECK"
fi

echo ""

# ========================================
# 步骤 5: 验证非流式轨迹包含 routed_experts
# ========================================
log_step "步骤 5: 验证非流式轨迹存储包含 routed_experts"

sleep 0.5
log_info "查询非流式轨迹..."
TRAJ_VERIFY=$(verify_trajectory_routed_experts "${SESSION_NS}")

ROUTED_EXPERTS_RESULT=$(echo "$TRAJ_VERIFY" | grep "^RESULT:routed_experts=" | cut -d= -f2)
if [ "$ROUTED_EXPERTS_RESULT" == "OK" ]; then
    log_success "非流式轨迹中 routed_experts 存在且结构正确 → 额外字段被正确存储"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    assert_fail "非流式轨迹中 routed_experts 缺失或结构不正确" "$ROUTED_EXPERTS_RESULT"
fi

echo ""

# ========================================
# 步骤 6: 流式请求
# ========================================
SESSION_S="session-${SCENARIO_ID}-s-$(date +%s%N | md5sum | head -c 8)"

log_step "步骤 6: 发送流式请求（带 logprobs=true）"

STREAM_RESPONSE=$(curl_with_log -s --noproxy '*' --no-buffer --max-time 15 -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${SESSION_S}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Say hi.\"}],
        \"max_tokens\": 10,
        \"logprobs\": true,
        \"stream\": true
    }")

assert_contains "$STREAM_RESPONSE" "data:" "流式响应应包含 data: 前缀"
assert_contains "$STREAM_RESPONSE" "[DONE]" "流式响应应以 [DONE] 结束"

echo ""

# ========================================
# 步骤 7: 验证流式 chunks 包含 routed_experts
# ========================================
log_step "步骤 7: 验证流式 chunks 包含 routed_experts"

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
                experts = choice['routed_experts']
                # 验证结构
                if isinstance(experts, list) and len(experts) == 2:
                    if experts[0].get('expert_id') == 2 and experts[0].get('weight') == 0.7 and \
                       experts[1].get('expert_id') == 5 and experts[1].get('weight') == 0.3:
                        pass  # 结构正确
                    else:
                        print(f'FAIL:routed_experts content mismatch')
                        sys.exit(0)
                else:
                    print(f'FAIL:routed_experts not list or len mismatch')
                    sys.exit(0)
            delta = choice.get('delta', {})
            if delta.get('content'):
                found_content = True
    except json.JSONDecodeError:
        continue
if found_routed_experts and found_content:
    print('OK')
else:
    print(f'FAIL:routed_experts={found_routed_experts}, content={found_content}')
" 2>&1)

if [ "$STREAM_CHECK" == "OK" ]; then
    log_success "流式响应中包含 routed_experts 且结构正确，content 正常"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    assert_fail "流式响应 routed_experts 验证失败" "$STREAM_CHECK"
fi

echo ""

# ========================================
# 步骤 8: 验证流式轨迹包含 routed_experts
# ========================================
log_step "步骤 8: 验证流式轨迹存储包含 routed_experts"

sleep 0.5
log_info "查询流式轨迹..."
TRAJ_VERIFY=$(verify_trajectory_routed_experts "${SESSION_S}")

ROUTED_EXPERTS_RESULT=$(echo "$TRAJ_VERIFY" | grep "^RESULT:routed_experts=" | cut -d= -f2)
if [ "$ROUTED_EXPERTS_RESULT" == "OK" ]; then
    log_success "流式轨迹中 routed_experts 存在且结构正确 → 额外字段被正确存储"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    assert_fail "流式轨迹中 routed_experts 缺失或结构不正确" "$ROUTED_EXPERTS_RESULT"
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
