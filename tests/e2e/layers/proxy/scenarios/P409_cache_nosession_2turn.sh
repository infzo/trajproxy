#!/bin/bash
# P409: TITO ns Tool 2轮无session缓存
# 矩阵: TITO×ns×Tool×2轮×session=无
# 来源: F3xx异常缓存矩阵 - 无session时cache_hit_tokens恒为0

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "=== P409: TITO ns Tool 2轮无session缓存 ==="

RUN_ID="run-p409"
MODEL_NAME="${DEFAULT_MODEL_NAME}"

# Tool定义：get_weather
TOOLS='[{"type":"function","function":{"name":"get_weather","parameters":{"type":"object","properties":{"location":{"type":"string"}},"required":["location"]}}}]'

log_step "注册 TITO + tool_parser + reasoning_parser"
curl_with_log -s -X POST "${BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{\"run_id\":\"${RUN_ID}\",\"model_name\":\"${MODEL_NAME}\",\"url\":\"${BACKEND_MODEL_URL}\",\"api_key\":\"${CHAT_API_KEY}\",\"token_in_token_out\":true,\"tokenizer_path\":\"${DEFAULT_TOKENIZER_PATH}\",\"tool_parser\":\"${DEFAULT_TOOL_PARSER}\",\"reasoning_parser\":\"${DEFAULT_REASONING_PARSER}\"}" > /dev/null
sleep 1

# ===== 第1轮: 非流式 Tool，无session路径 =====
log_step "第1轮 非流式 Tool 请求（无session路径）"
R1_RESP=$(curl_with_log -s -w "\n%{http_code}" -X POST "${BASE_URL}/v1/chat/completions" \
    -H "Content-Type: application/json" -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{\"model\":\"${MODEL_NAME}\",\"messages\":[{\"role\":\"user\",\"content\":\"Weather in Beijing?\"}],\"tools\":${TOOLS},\"max_tokens\":512}")
R1_STATUS=$(echo "$R1_RESP" | sed -n '$p')
assert_http_status "200" "$R1_STATUS" "第1轮 非流式 200"
R1_BODY=$(echo "$R1_RESP" | sed '$d')
R1_CONTENT=$(extract_assistant_content "$R1_BODY")
sleep 1

# ===== 第2轮: 非流式 Tool，无session路径（不同content） =====
log_step "第2轮 非流式 Tool 请求（无session路径，不同content）"
R2_RESP=$(curl_with_log -s -w "\n%{http_code}" -X POST "${BASE_URL}/v1/chat/completions" \
    -H "Content-Type: application/json" -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{\"model\":\"${MODEL_NAME}\",\"messages\":[{\"role\":\"user\",\"content\":\"Weather in Shanghai?\"}],\"tools\":${TOOLS},\"max_tokens\":512}")
R2_STATUS=$(echo "$R2_RESP" | sed -n '$p')
assert_http_status "200" "$R2_STATUS" "第2轮 非流式 200"
R2_BODY=$(echo "$R2_RESP" | sed '$d')
R2_CONTENT=$(extract_assistant_content "$R2_BODY")
sleep 1

# ===== 缓存校验：无session时cache_hit_tokens恒为0 =====
log_step "验证无session时cache_hit_tokens恒为0"
# 查询run_id下的所有轨迹，找到各session_id
TRAJ_LIST=$(curl_with_log -s "${BASE_URL}/trajectories?run_id=${RUN_ID}")

ALL_CACHE_ZERO=true
ERRORS=""

# 提取所有session_id，逐个查询轨迹记录
SESSION_IDS=$(echo "$TRAJ_LIST" | python3 -c "
import json, sys
try:
    data = json.loads(sys.stdin.read())
    for t in data.get('trajectories', []):
        sid = t.get('session_id', '')
        if sid:
            print(sid)
except:
    pass
" 2>/dev/null)

while read -r sid; do
    [ -z "$sid" ] && continue
    TRAJ=$(curl_with_log -s "${BASE_URL}/trajectory?session_id=${sid}&limit=10")
    HIT_RESULT=$(echo "$TRAJ" | python3 -c "
import json, sys
try:
    data = json.loads(sys.stdin.read())
    records = sorted(data.get('records', []), key=lambda r: r.get('start_time', ''))
    for rec in records:
        ch = rec.get('cache_hit_tokens', -1)
        fids = rec.get('full_conversation_token_ids', [])
        tids = rec.get('token_ids', [])
        rids = rec.get('response_ids', [])
        # 校验2: len(full_conversation_token_ids) == len(token_ids) + len(response_ids)
        if fids and tids and rids:
            if len(fids) != len(tids) + len(rids):
                print(f'FAIL校验2: full_conv={len(fids)} != token_ids={len(tids)}+response_ids={len(rids)}')
                sys.exit(1)
        # 无session时cache_hit_tokens恒为0
        if ch != 0:
            print(f'FAIL: cache_hit={ch}, 期望=0(无session)')
            sys.exit(1)
    print(f'OK: {len(records)}条记录cache_hit=0')
except Exception as e:
    print(f'ERROR:查询失败:{e}')
    sys.exit(1)
" 2>/dev/null)

    if ! echo "$HIT_RESULT" | grep -q "^OK:"; then
        ERRORS="${ERRORS} ${sid}:${HIT_RESULT}"
        ALL_CACHE_ZERO=false
    else
        log_success "${sid}: ${HIT_RESULT}"
    fi
done <<< "$SESSION_IDS"

if [ "$ALL_CACHE_ZERO" = true ]; then
    TESTS_TOTAL=$((TESTS_TOTAL + 1)); TESTS_PASSED=$((TESTS_PASSED + 1))
    log_success "所有轨迹记录cache_hit_tokens=0（无session确认）"
else
    assert_fail "cache_hit_tokens不为0" "${ERRORS}"
fi

# 清理
curl_with_log -s -X DELETE "${BASE_URL}/models?model_name=${MODEL_NAME}&run_id=${RUN_ID}" > /dev/null
print_summary