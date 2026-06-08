#!/bin/bash
# P401: TITO非流式3轮缓存（无Parser）
# 矩阵: TITO×ns×无Parser×3轮×session
# 来源: TEST_CASE_CATALOG §6
# 校验公式:
#   校验1: cache_hit_tokens[R_n] == len(full_conversation_token_ids[R_{n-1}])
#   校验2: len(full_conversation_token_ids) == len(token_ids) + len(response_ids)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "=== P401: TITO非流式3轮缓存 ==="

RUN_ID="run-p401"
SESS_ID="sess-f301-$(date +%s%N | md5sum | head -c 8)"
MODEL_NAME="${DEFAULT_MODEL_NAME}"

log_step "注册TITO模型（无Parser）"
curl_with_log -s -X POST "${BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{\"run_id\":\"${RUN_ID}\",\"model_name\":\"${MODEL_NAME}\",\"url\":\"${BACKEND_MODEL_URL}\",\"api_key\":\"${CHAT_API_KEY}\",\"token_in_token_out\":true,\"tokenizer_path\":\"${DEFAULT_TOKENIZER_PATH}\"}" > /dev/null
sleep 1

# 第1轮：仅user消息
log_step "第1轮非流式请求"
R1_RESP=$(curl_with_log -s -w "
%{http_code}" -X POST "${BASE_URL}/s/${RUN_ID}/${SESS_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{\"model\":\"${MODEL_NAME}\",\"messages\":[{\"role\":\"user\",\"content\":\"What is 10 plus 5?\"}],\"max_tokens\":64}")
R1_BODY=$(echo "$R1_RESP" | sed '$d')
assert_http_status "200" "$(echo "$R1_RESP" | sed -n '$p')" "第1轮 200"
R1_CONTENT=$(extract_assistant_content "$R1_BODY")
sleep 1

# 第2轮：含第1轮user+assistant历史
log_step "第2轮非流式请求（含历史）"
R2_MESSAGES=$(R1_CONTENT="$R1_CONTENT" python3 -c "
import json, os
r1 = os.environ['R1_CONTENT']
messages = [
    {'role':'user','content':'What is 10 plus 5?'},
    {'role':'assistant','content':r1},
    {'role':'user','content':'What is 25 plus 5?'}
]
print(json.dumps(messages, ensure_ascii=False))
" 2>/dev/null)
R2_RESP=$(curl_with_log -s -w "
%{http_code}" -X POST "${BASE_URL}/s/${RUN_ID}/${SESS_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{\"model\":\"${MODEL_NAME}\",\"messages\":${R2_MESSAGES},\"max_tokens\":64}")
R2_BODY=$(echo "$R2_RESP" | sed '$d')
assert_http_status "200" "$(echo "$R2_RESP" | sed -n '$p')" "第2轮 200"
R2_CONTENT=$(extract_assistant_content "$R2_BODY")
sleep 1

# 第3轮：含第1+2轮user+assistant历史
log_step "第3轮非流式请求（含历史）"
R3_MESSAGES=$(R1_CONTENT="$R1_CONTENT" R2_CONTENT="$R2_CONTENT" python3 -c "
import json, os
r1 = os.environ['R1_CONTENT']
r2 = os.environ['R2_CONTENT']
messages = [
    {'role':'user','content':'What is 10 plus 5?'},
    {'role':'assistant','content':r1},
    {'role':'user','content':'What is 25 plus 5?'},
    {'role':'assistant','content':r2},
    {'role':'user','content':'What is 35 plus 5?'}
]
print(json.dumps(messages, ensure_ascii=False))
" 2>/dev/null)
R3_RESP=$(curl_with_log -s -w "
%{http_code}" -X POST "${BASE_URL}/s/${RUN_ID}/${SESS_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{\"model\":\"${MODEL_NAME}\",\"messages\":${R3_MESSAGES},\"max_tokens\":64}")
R3_BODY=$(echo "$R3_RESP" | sed '$d')
assert_http_status "200" "$(echo "$R3_RESP" | sed -n '$p')" "第3轮 200"

# 验证缓存递增（校验1+校验2）
verify_cache_incremental "$SESS_ID" 3 "incremental"

curl_with_log -s -X DELETE "${BASE_URL}/models?model_name=${MODEL_NAME}&run_id=${RUN_ID}" > /dev/null
print_summary