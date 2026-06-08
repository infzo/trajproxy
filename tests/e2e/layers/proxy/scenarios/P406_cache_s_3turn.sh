#!/bin/bash
# F306: TITO 流式 无Parser 3轮缓存
# 矩阵: TITO×s×无Parser×3轮×session

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "=== F306: TITO 流式 3轮缓存 ==="

RUN_ID="run-f306"
SESS_ID="sess-f306-$(date +%s%N | md5sum | head -c 8)"
MODEL_NAME="${DEFAULT_MODEL_NAME}"

log_step "注册 TITO 模型（无parser）"
curl_with_log -s -X POST "${BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{\"run_id\":\"${RUN_ID}\",\"model_name\":\"${MODEL_NAME}\",\"url\":\"${BACKEND_MODEL_URL}\",\"api_key\":\"${CHAT_API_KEY}\",\"token_in_token_out\":true,\"tokenizer_path\":\"${DEFAULT_TOKENIZER_PATH}\"}" > /dev/null
sleep 1

# ===== 第1轮: 流式 =====
log_step "第1轮 流式请求"
R1_STREAM=$(curl_with_log -s --no-buffer -X POST "${BASE_URL}/s/${RUN_ID}/${SESS_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{\"model\":\"${MODEL_NAME}\",\"messages\":[{\"role\":\"user\",\"content\":\"What is 10 + 5?\"}],\"max_tokens\":64,\"stream\":true}")
assert_contains "$R1_STREAM" "data:" "第1轮流式包含 data:"
R1_CONTENT=$(extract_stream_assistant_content "$R1_STREAM")
[ -n "$R1_CONTENT" ] && log_success "第1轮 assistant content非空" || { log_error "第1轮 assistant content为空"; TESTS_TOTAL=$((TESTS_TOTAL+1)); TESTS_FAILED=$((TESTS_FAILED+1)); }
sleep 1

# ===== 第2轮: 构造增量 messages =====
log_step "第2轮 流式请求（增量messages）"
R2_MESSAGES=$(R1_CONTENT="$R1_CONTENT" python3 -c "
import json, os
messages = [{'role':'user','content':'What is 10 + 5?'},
            {'role':'assistant','content':os.environ['R1_CONTENT']},
            {'role':'user','content':'What is 20 + 8?'}]
print(json.dumps(messages, ensure_ascii=False))
")
R2_STREAM=$(curl_with_log -s --no-buffer -X POST "${BASE_URL}/s/${RUN_ID}/${SESS_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{\"model\":\"${MODEL_NAME}\",\"messages\":${R2_MESSAGES},\"max_tokens\":64,\"stream\":true}")
assert_contains "$R2_STREAM" "data:" "第2轮流式包含 data:"
R2_CONTENT=$(extract_stream_assistant_content "$R2_STREAM")
[ -n "$R2_CONTENT" ] && log_success "第2轮 assistant content非空" || { log_error "第2轮 assistant content为空"; TESTS_TOTAL=$((TESTS_TOTAL+1)); TESTS_FAILED=$((TESTS_FAILED+1)); }
sleep 1

# ===== 第3轮: 构造增量 messages =====
log_step "第3轮 流式请求（增量messages）"
R3_MESSAGES=$(R1_CONTENT="$R1_CONTENT" R2_CONTENT="$R2_CONTENT" python3 -c "
import json, os
messages = [{'role':'user','content':'What is 10 + 5?'},
            {'role':'assistant','content':os.environ['R1_CONTENT']},
            {'role':'user','content':'What is 20 + 8?'},
            {'role':'assistant','content':os.environ['R2_CONTENT']},
            {'role':'user','content':'What is 30 + 12?'}]
print(json.dumps(messages, ensure_ascii=False))
")
R3_STREAM=$(curl_with_log -s --no-buffer -X POST "${BASE_URL}/s/${RUN_ID}/${SESS_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{\"model\":\"${MODEL_NAME}\",\"messages\":${R3_MESSAGES},\"max_tokens\":64,\"stream\":true}")
assert_contains "$R3_STREAM" "data:" "第3轮流式包含 data:"
sleep 1

# ===== 缓存校验 =====
verify_cache_incremental "$SESS_ID" 3 "incremental"

curl_with_log -s -X DELETE "${BASE_URL}/models?model_name=${MODEL_NAME}&run_id=${RUN_ID}" > /dev/null
print_summary