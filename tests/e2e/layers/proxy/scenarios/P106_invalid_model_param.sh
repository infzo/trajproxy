#!/bin/bash
# P106: 无效 model 参数（新增）
# 矩阵: 基础API×参数校验×422

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "=== P106: 无效 model 参数 ==="

# model=""
log_step "model 为空字符串"
CHAT=$(curl_with_log -s -w "\n%{http_code}" -X POST "${BASE_URL}/v1/chat/completions" \
    -H "Content-Type: application/json" -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{\"model\":\"\",\"messages\":[{\"role\":\"user\",\"content\":\"Hello\"}]}")
STATUS=$(echo "$CHAT" | sed -n '$p')
assert_one_of "$STATUS" "422 400" "model=\"\" 应返回 422/400"

# model=null
log_step "model 为 null"
CHAT3=$(curl_with_log -s -w "\n%{http_code}" -X POST "${BASE_URL}/v1/chat/completions" \
    -H "Content-Type: application/json" -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{\"model\":null,\"messages\":[{\"role\":\"user\",\"content\":\"Hello\"}]}")
STATUS3=$(echo "$CHAT3" | sed -n '$p')
assert_one_of "$STATUS3" "422 400" "model=null 应返回 422/400"

# 缺失 model
log_step "缺失 model 字段"
CHAT2=$(curl_with_log -s -w "\n%{http_code}" -X POST "${BASE_URL}/v1/chat/completions" \
    -H "Content-Type: application/json" -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{\"messages\":[{\"role\":\"user\",\"content\":\"Hello\"}]}")
STATUS2=$(echo "$CHAT2" | sed -n '$p')
assert_one_of "$STATUS2" "422 400" "缺失 model 应返回 422/400"

print_summary