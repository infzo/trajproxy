#!/bin/bash
# F118: TITO Token 模式基础冒烟（非流式）
# 矩阵: TITO×ns×单轮×冒烟
# 来源: 保留, 原F104; 验证TITO基础请求+response_ids存在

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "=== F118: TITO Token 非流式基础冒烟 ==="

RUN_ID="run-f118"
SESS_ID="sess-f118-$(date +%s%N | md5sum | head -c 8)"
MODEL_NAME="${DEFAULT_MODEL_NAME}"

log_step "注册 TITO 模型（无 parser）"
curl_with_log -s -X POST "${BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{\"run_id\":\"${RUN_ID}\",\"model_name\":\"${MODEL_NAME}\",\"url\":\"${BACKEND_MODEL_URL}\",\"api_key\":\"${CHAT_API_KEY}\",\"token_in_token_out\":true,\"tokenizer_path\":\"${DEFAULT_TOKENIZER_PATH}\"}" > /dev/null
sleep 1

log_step "TITO 非流式基础请求"
R1=$(curl_with_log -s -w "\n%{http_code}" -X POST "${BASE_URL}/s/${RUN_ID}/${SESS_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{\"model\":\"${MODEL_NAME}\",\"messages\":[{\"role\":\"user\",\"content\":\"Hello\"}],\"max_tokens\":64}")
R1_BODY=$(echo "$R1" | sed '$d')
R1_STATUS=$(echo "$R1" | sed -n '$p')
assert_http_status "200" "$R1_STATUS" "TITO ns 请求 200"

# 验证 response_ids 存在（TITO 模式关键特征）
log_step "验证轨迹中有 response_ids"
sleep 0.5
TRAJ=$(curl_with_log -s "${BASE_URL}/trajectory?session_id=${SESS_ID}&limit=10")
HAS_RESPONSE_IDS=$(echo "$TRAJ" | python3 -c "
import json, sys
data = json.loads(sys.stdin.read())
recs = data.get('records', [])
if recs:
    r = recs[0]
    ids = r.get('response_ids', [])
    print(f'response_ids长度={len(ids)}')
else:
    print('ERROR:无轨迹记录')
" 2>/dev/null)
log_info "$HAS_RESPONSE_IDS"

curl_with_log -s -X DELETE "${BASE_URL}/models?model_name=${MODEL_NAME}&run_id=${RUN_ID}" > /dev/null
print_summary