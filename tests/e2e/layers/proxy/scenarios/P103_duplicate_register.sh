#!/bin/bash
# P103: 重复注册模型 - 幂等性验证（新增）
# 矩阵: 基础API×模型管理×幂等

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "=== P103: 重复注册模型 ==="

RUN_ID="run-p103"
MODEL_NAME="${DEFAULT_MODEL_NAME}"

log_step "首次注册"
REG1=$(curl_with_log -s -w "\n%{http_code}" -X POST "${BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{\"run_id\":\"${RUN_ID}\",\"model_name\":\"${MODEL_NAME}\",\"url\":\"${BACKEND_MODEL_URL}\",\"api_key\":\"${CHAT_API_KEY}\",\"token_in_token_out\":false}")
BODY1=$(echo "$REG1" | sed '$d'); STATUS1=$(echo "$REG1" | sed -n '$p')
assert_http_status "200" "$STATUS1" "首次注册 200"

log_step "重复注册（相同 run_id + model_name）"
REG2=$(curl_with_log -s -w "\n%{http_code}" -X POST "${BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{\"run_id\":\"${RUN_ID}\",\"model_name\":\"${MODEL_NAME}\",\"url\":\"${BACKEND_MODEL_URL}\",\"api_key\":\"${CHAT_API_KEY}\",\"token_in_token_out\":false}")
STATUS2=$(echo "$REG2" | sed -n '$p')
assert_one_of "$STATUS2" "200 409" "重复注册应返回 200/409（幂等）"

curl_with_log -s -X DELETE "${BASE_URL}/models?model_name=${MODEL_NAME}&run_id=${RUN_ID}" > /dev/null
print_summary