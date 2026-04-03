#!/bin/bash
# 场景 001: 模型注册-列表-删除
# 测试流程：注册2个模型 -> 列表 -> 删除 -> 列表验证

# 获取脚本目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 001: 模型注册-列表-删除"
echo "========================================"
echo ""

# 步骤 1: 注册模型 A（不带 run_id，使用默认 DEFAULT）
log_step "步骤 1: 注册模型 A（不带 run_id）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X POST '${API_MODELS}/register' \\
    -H 'Content-Type: application/json' \\
    -d '{
        \"run_id\": \"\",
        \"model_name\": \"${TEST_MODEL_A_NAME}\",
        \"url\": \"${TEST_MODEL_URL}\",
        \"api_key\": \"${TEST_MODEL_API_KEY}\",
        \"token_in_token_out\": false
    }'"
log_separator

REGISTER_A_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${API_MODELS}/register" \
    -H "Content-Type: application/json" \
    -d "{
        \"run_id\": \"${TEST_MODEL_A_RUN_ID}\",
        \"model_name\": \"${TEST_MODEL_A_NAME}\",
        \"url\": \"${TEST_MODEL_URL}\",
        \"api_key\": \"${TEST_MODEL_API_KEY}\",
        \"token_in_token_out\": false
    }")

# 兼容 macOS 的方式分离响应体和状态码
REGISTER_A_BODY=$(echo "$REGISTER_A_RESPONSE" | sed '$d')
REGISTER_A_STATUS=$(echo "$REGISTER_A_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${REGISTER_A_STATUS}"
log_response "${REGISTER_A_BODY}"
log_separator

assert_http_status "200" "$REGISTER_A_STATUS" "HTTP 状态码应为 200"

REGISTER_A_RESULT=$(json_get "$REGISTER_A_BODY" "status")
assert_eq "success" "$REGISTER_A_RESULT" "注册模型 A 应返回 success"

REGISTER_A_RUN_ID=$(json_get "$REGISTER_A_BODY" "run_id")
assert_eq "DEFAULT" "$REGISTER_A_RUN_ID" "run_id 应为 DEFAULT"

echo ""

# 步骤 2: 注册模型 B（带 run_id）
log_step "步骤 2: 注册模型 B（带 run_id: ${TEST_MODEL_B_RUN_ID}）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X POST '${API_MODELS}/register' \\
    -H 'Content-Type: application/json' \\
    -d '{
        \"run_id\": \"${TEST_MODEL_B_RUN_ID}\",
        \"model_name\": \"${TEST_MODEL_B_NAME}\",
        \"url\": \"${TEST_MODEL_URL}\",
        \"api_key\": \"${TEST_MODEL_API_KEY}\",
        \"token_in_token_out\": false
    }'"
log_separator

REGISTER_B_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${API_MODELS}/register" \
    -H "Content-Type: application/json" \
    -d "{
        \"run_id\": \"${TEST_MODEL_B_RUN_ID}\",
        \"model_name\": \"${TEST_MODEL_B_NAME}\",
        \"url\": \"${TEST_MODEL_URL}\",
        \"api_key\": \"${TEST_MODEL_API_KEY}\",
        \"token_in_token_out\": false
    }")

REGISTER_B_BODY=$(echo "$REGISTER_B_RESPONSE" | sed '$d')
REGISTER_B_STATUS=$(echo "$REGISTER_B_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${REGISTER_B_STATUS}"
log_response "${REGISTER_B_BODY}"
log_separator

assert_http_status "200" "$REGISTER_B_STATUS" "HTTP 状态码应为 200"

REGISTER_B_RESULT=$(json_get "$REGISTER_B_BODY" "status")
assert_eq "success" "$REGISTER_B_RESULT" "注册模型 B 应返回 success"

REGISTER_B_RUN_ID=$(json_get "$REGISTER_B_BODY" "run_id")
assert_eq "$TEST_MODEL_B_RUN_ID" "$REGISTER_B_RUN_ID" "run_id 应为 ${TEST_MODEL_B_RUN_ID}"

echo ""

# 步骤 3: 列出所有模型
log_step "步骤 3: 列出所有模型"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X GET '${API_MODELS}'"
log_separator

LIST_RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "${API_MODELS}")

LIST_BODY=$(echo "$LIST_RESPONSE" | sed '$d')
LIST_STATUS=$(echo "$LIST_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${LIST_STATUS}"
log_response "${LIST_BODY}"
log_separator

assert_http_status "200" "$LIST_STATUS" "HTTP 状态码应为 200"

LIST_RESULT=$(json_get "$LIST_BODY" "status")
assert_eq "success" "$LIST_RESULT" "列出模型应返回 success"

assert_contains "$LIST_BODY" "$TEST_MODEL_A_NAME" "模型列表应包含模型 A"
assert_contains "$LIST_BODY" "$TEST_MODEL_B_NAME" "模型列表应包含模型 B"

echo ""

# 步骤 4: 删除模型 A
log_step "步骤 4: 删除模型 A（run_id: DEFAULT）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X DELETE '${API_MODELS}?model_name=${TEST_MODEL_A_NAME}&run_id='"
log_separator

DELETE_A_RESPONSE=$(curl -s -w "\n%{http_code}" -X DELETE "${API_MODELS}?model_name=${TEST_MODEL_A_NAME}&run_id=")

DELETE_A_BODY=$(echo "$DELETE_A_RESPONSE" | sed '$d')
DELETE_A_STATUS=$(echo "$DELETE_A_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${DELETE_A_STATUS}"
log_response "${DELETE_A_BODY}"
log_separator

assert_http_status "200" "$DELETE_A_STATUS" "HTTP 状态码应为 200"

DELETE_A_RESULT=$(json_get "$DELETE_A_BODY" "status")
assert_eq "success" "$DELETE_A_RESULT" "删除模型 A 应返回 success"

DELETE_A_DELETED=$(json_get_bool "$DELETE_A_BODY" "deleted")
assert_eq "true" "$DELETE_A_DELETED" "deleted 应为 true"

echo ""

# 步骤 5: 删除模型 B
log_step "步骤 5: 删除模型 B（run_id: ${TEST_MODEL_B_RUN_ID}）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X DELETE '${API_MODELS}?model_name=${TEST_MODEL_B_NAME}&run_id=${TEST_MODEL_B_RUN_ID}'"
log_separator

DELETE_B_RESPONSE=$(curl -s -w "\n%{http_code}" -X DELETE "${API_MODELS}?model_name=${TEST_MODEL_B_NAME}&run_id=${TEST_MODEL_B_RUN_ID}")

DELETE_B_BODY=$(echo "$DELETE_B_RESPONSE" | sed '$d')
DELETE_B_STATUS=$(echo "$DELETE_B_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${DELETE_B_STATUS}"
log_response "${DELETE_B_BODY}"
log_separator

assert_http_status "200" "$DELETE_B_STATUS" "HTTP 状态码应为 200"

DELETE_B_RESULT=$(json_get "$DELETE_B_BODY" "status")
assert_eq "success" "$DELETE_B_RESULT" "删除模型 B 应返回 success"

DELETE_B_DELETED=$(json_get_bool "$DELETE_B_BODY" "deleted")
assert_eq "true" "$DELETE_B_DELETED" "deleted 应为 true"

echo ""

# 步骤 6: 再次列出模型，验证删除成功
log_step "步骤 6: 再次列出模型，验证删除成功"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X GET '${API_MODELS}'"
log_separator

LIST_FINAL_RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "${API_MODELS}")

LIST_FINAL_BODY=$(echo "$LIST_FINAL_RESPONSE" | sed '$d')
LIST_FINAL_STATUS=$(echo "$LIST_FINAL_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${LIST_FINAL_STATUS}"
log_response "${LIST_FINAL_BODY}"
log_separator

assert_http_status "200" "$LIST_FINAL_STATUS" "HTTP 状态码应为 200"

LIST_FINAL_RESULT=$(json_get "$LIST_FINAL_BODY" "status")
assert_eq "success" "$LIST_FINAL_RESULT" "列出模型应返回 success"

assert_not_contains "$LIST_FINAL_BODY" "$TEST_MODEL_A_NAME" "模型列表不应包含模型 A"
assert_not_contains "$LIST_FINAL_BODY" "$TEST_MODEL_B_NAME" "模型列表不应包含模型 B"

echo ""

# 打印测试摘要
print_summary
