#!/bin/bash
# F119: TITO Token 流式基础冒烟（新增）
# 矩阵: TITO×s×单轮×冒烟

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "=== F119: TITO Token 流式 ==="

RUN_ID="run-f119"
MODEL_NAME="${DEFAULT_MODEL_NAME}"

log_step "注册 TITO 模型"
curl -s -X POST "${BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{\"run_id\":\"${RUN_ID}\",\"model_name\":\"${MODEL_NAME}\",\"url\":\"${BACKEND_MODEL_URL}\",\"api_key\":\"${CHAT_API_KEY}\",\"token_in_token_out\":true,\"tokenizer_path\":\"${DEFAULT_TOKENIZER_PATH}\"}" > /dev/null
sleep 1

log_step "TITO 流式请求"
STREAM=$(curl -s --no-buffer -X POST "${BASE_URL}/v1/chat/completions" \
    -H "Content-Type: application/json" -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{\"model\":\"${MODEL_NAME}\",\"messages\":[{\"role\":\"user\",\"content\":\"Hello\"}],\"max_tokens\":32,\"stream\":true}")
assert_contains "$STREAM" "data:" "流式响应包含 data:"
assert_contains "$STREAM" "[DONE]" "流式以 [DONE] 结束"

curl -s -X DELETE "${BASE_URL}/models?model_name=${MODEL_NAME}&run_id=${RUN_ID}" > /dev/null
print_summary
