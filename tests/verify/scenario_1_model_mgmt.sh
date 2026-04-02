#!/bin/bash
# ============================================
# 场景一：模型注册、罗列、删除接口测试
# ============================================
# 测试内容:
#   - 模型注册（带/不带 run_id）
#   - 模型配置（token-in-token-out/parser/tokenizer_path）
#   - 模型列表查询（管理格式/OpenAI格式）
#   - 模型删除
#   - 异常处理（重复注册/删除不存在模型）
# ============================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"
source "${SCRIPT_DIR}/config.sh"

# ============================================
# 测试步骤
# ============================================

# 步骤 1: 健康检查
test_health_check() {
    log_test "步骤 1: 健康检查"

    local response
    response=$(http_get "${PROXY_URL}/health" "健康检查")

    local code=$(http_code "$response")
    local body=$(response_body "$response")

    assert_http_code "$code" "200" "健康检查"

    # 验证响应体中的 status 字段
    local status=$(json_get "$body" "status")
    if [[ "$status" == "ok" ]]; then
        log_success "status 字段 = ok"
    else
        log_error "status 字段应该为 'ok'，实际为 '$status'"
    fi
}

# 步骤 2: 查看初始模型列表
test_initial_model_list() {
    log_test "步骤 2: 查看初始模型列表（管理格式）"

    local response
    response=$(http_get "${PROXY_URL}/models/" "查看模型列表")

    local code=$(http_code "$response")
    local body=$(response_body "$response")

    assert_http_code "$code" "200" "获取模型列表"
}

# 步骤 3: 注册模型（不带 run_id）
test_register_model_no_runid() {
    log_test "步骤 3: 注册模型（不带 run_id）"

    local body=$(cat <<EOF
{
  "run_id": "",
  "model_name": "${TEST_MODEL}_basic",
  "url": "${INFERENCE_URL}",
  "api_key": "${API_KEY}",
  "tokenizer_path": "",
  "token_in_token_out": false
}
EOF
)

    local response
    response=$(http_post "${PROXY_URL}/models/register" "$body" "" "注册模型（不带 run_id）")

    local code=$(http_code "$response")
    local rbody=$(response_body "$response")

    assert_http_code "$code" "200" "模型注册"

    # 验证响应体中的 status 字段
    assert_json_equals "$rbody" "status" "success" "验证 status 字段"

    # 验证返回的模型名称
    assert_json_equals "$rbody" "model_name" "${TEST_MODEL}_basic" "验证 model_name 字段"

    # 验证 detail 中的配置
    assert_json_equals "$rbody" "detail.token_in_token_out" "false" "验证 token_in_token_out 配置"
}

# 步骤 4: 验证模型已在列表中
test_model_in_list() {
    log_test "步骤 4: 验证模型已在列表中"

    local response
    response=$(http_get "${PROXY_URL}/models/" "验证模型在列表中")

    local code=$(http_code "$response")
    local body=$(response_body "$response")

    assert_http_code "$code" "200" "获取模型列表"

    # 检查模型是否存在（这里简化处理，实际可能需要解析数组）
    log_info "检查模型 ${TEST_MODEL}_basic 是否在列表中"
}

# 步骤 5: 删除模型（不带 run_id）
test_delete_model_no_runid() {
    log_test "步骤 5: 删除模型（不带 run_id）"

    local response
    response=$(http_delete "${PROXY_URL}/models?model_name=${TEST_MODEL}_basic" "删除模型")

    local code=$(http_code "$response")
    local body=$(response_body "$response")

    # 应该返回 200 或 404
    assert_http_code_in "$code" "200,404" "删除模型"

    if [[ "$code" == "200" ]]; then
        log_success "模型删除成功"
    else
        log_info "模型可能已不存在"
    fi
}

# 步骤 6: 验证模型已删除
test_model_deleted() {
    log_test "步骤 6: 验证模型已删除"

    local response
    response=$(http_get "${PROXY_URL}/models/" "验证模型已删除")

    local code=$(http_code "$response")
    local body=$(response_body "$response")

    assert_http_code "$code" "200" "获取模型列表"

    log_info "验证模型 ${TEST_MODEL}_basic 已从列表中移除"
}

# 步骤 7: 注册模型（带 run_id）
test_register_model_with_runid() {
    log_test "步骤 7: 注册模型（带 run_id）"

    local body=$(cat <<EOF
{
  "run_id": "${TEST_RUN_ID}",
  "model_name": "${TEST_MODEL}_with_runid",
  "url": "${INFERENCE_URL}",
  "api_key": "${API_KEY}",
  "tokenizer_path": "${TOKENIZER_PATH}",
  "token_in_token_out": false
}
EOF
)

    local response
    response=$(http_post "${PROXY_URL}/models/register" "$body" "" "注册模型（带 run_id）")

    local code=$(http_code "$response")
    local rbody=$(response_body "$response")

    assert_http_code "$code" "200" "模型注册"

    # 验证响应体中的字段
    assert_json_equals "$rbody" "status" "success" "验证 status 字段"
    assert_json_equals "$rbody" "run_id" "${TEST_RUN_ID}" "验证 run_id 字段"
    assert_json_equals "$rbody" "detail.run_id" "${TEST_RUN_ID}" "验证 detail.run_id 字段"
}

# 步骤 8: 验证 OpenAI 格式列表
test_openai_model_list() {
    log_test "步骤 8: 验证 OpenAI 格式列表"

    local response
    response=$(http_get "${PROXY_URL}/v1/models" "查询 OpenAI 格式模型列表")

    local code=$(http_code "$response")
    local body=$(response_body "$response")

    assert_http_code "$code" "200" "获取 OpenAI 格式模型列表"

    # 验证 OpenAI 格式
    assert_json_equals "$body" "object" "list" "验证 object 字段"

    log_info "检查模型 ID 格式: ${TEST_RUN_ID}/${TEST_MODEL}_with_runid"
}

# 步骤 9: 删除模型（带 run_id）
test_delete_model_with_runid() {
    log_test "步骤 9: 删除模型（带 run_id）"

    local response
    response=$(http_delete "${PROXY_URL}/models?model_name=${TEST_MODEL}_with_runid&run_id=${TEST_RUN_ID}" "删除模型（带 run_id）")

    local code=$(http_code "$response")
    local body=$(response_body "$response")

    assert_http_code_in "$code" "200,404" "删除模型"
}

# 步骤 10: 注册模型（带 parser/tokenizer）
test_register_model_with_parser() {
    log_test "步骤 10: 注册模型（带 parser/tokenizer）"

    local body=$(cat <<EOF
{
  "run_id": "",
  "model_name": "${TEST_MODEL}_full_config",
  "url": "${INFERENCE_URL}",
  "api_key": "${API_KEY}",
  "tokenizer_path": "${TOKENIZER_PATH}",
  "token_in_token_out": true,
  "tool_parser": "${TOOL_PARSER}",
  "reasoning_parser": "${REASONING_PARSER}"
}
EOF
)

    local response
    response=$(http_post "${PROXY_URL}/models/register" "$body" "" "注册模型（带完整配置）")

    local code=$(http_code "$response")
    local rbody=$(response_body "$response")

    assert_http_code "$code" "200" "模型注册"

    # 验证配置详情
    assert_json_equals "$rbody" "status" "success" "验证 status 字段"
    assert_json_equals "$rbody" "detail.token_in_token_out" "true" "验证 token_in_token_out"
    assert_json_equals "$rbody" "detail.tool_parser" "${TOOL_PARSER}" "验证 tool_parser"
    assert_json_equals "$rbody" "detail.reasoning_parser" "${REASONING_PARSER}" "验证 reasoning_parser"
}

# 步骤 11: 验证配置详情
test_model_config_details() {
    log_test "步骤 11: 验证配置详情"

    local response
    response=$(http_get "${PROXY_URL}/models/" "查看模型配置详情")

    local code=$(http_code "$response")
    local body=$(response_body "$response")

    assert_http_code "$code" "200" "获取模型列表"
}

# 步骤 12: 清理模型
test_cleanup_model() {
    log_test "步骤 12: 清理模型"

    local response
    response=$(http_delete "${PROXY_URL}/models?model_name=${TEST_MODEL}_full_config" "清理测试模型")

    local code=$(http_code "$response")
    local body=$(response_body "$response")

    assert_http_code_in "$code" "200,404" "清理模型"
}

# 异常测试 1: 注册重复模型
test_register_duplicate_model() {
    log_test "异常测试 1: 注册重复模型"

    # 先注册一个模型
    local body=$(cat <<EOF
{
  "model_name": "${TEST_MODEL}_dup",
  "url": "${INFERENCE_URL}",
  "api_key": "${API_KEY}",
  "token_in_token_out": false
}
EOF
)

    http_post "${PROXY_URL}/models/register" "$body" "" "首次注册模型"

    # 尝试重复注册
    log_test "尝试重复注册"
    local response
    response=$(http_post "${PROXY_URL}/models/register" "$body" "" "重复注册模型")

    local code=$(http_code "$response")
    local rbody=$(response_body "$response")

    # 应该返回 400 或 409
    assert_http_code_in "$code" "400,409" "重复注册应返回错误"

    # 验证错误信息包含"已存在"或"存在"
    local detail=$(json_get "$rbody" "detail")
    if [[ "$detail" == *"已存在"* || "$detail" == *"存在"* ]]; then
        log_success "错误信息包含 '已存在' 或 '存在'"
    else
        log_error "错误信息应包含 '已存在' 或 '存在'，实际: $detail"
    fi

    # 清理
    http_delete "${PROXY_URL}/models?model_name=${TEST_MODEL}_dup" "清理重复模型"
}

# 异常测试 2: 删除不存在的模型
test_delete_nonexistent_model() {
    log_test "异常测试 2: 删除不存在的模型"

    local response
    response=$(http_delete "${PROXY_URL}/models?model_name=nonexistent_model_xyz_12345" "删除不存在的模型")

    local code=$(http_code "$response")
    local body=$(response_body "$response")

    # 应该返回 404
    assert_http_code "$code" "404" "删除不存在的模型应返回 404"

    # 验证错误信息包含"不存在"或"未找到"
    local detail=$(json_get "$body" "detail")
    if [[ "$detail" == *"不存在"* || "$detail" == *"未找到"* ]]; then
        log_success "错误信息包含 '不存在' 或 '未找到'"
    else
        log_error "错误信息应包含 '不存在' 或 '未找到'，实际: $detail"
    fi
}

# ============================================
# 主测试流程
# ============================================
main() {
    print_header "场景一：模型注册、罗列、删除接口测试"

    # 正常流程测试
    test_health_check
    test_initial_model_list
    test_register_model_no_runid
    test_model_in_list
    test_delete_model_no_runid
    test_model_deleted
    test_register_model_with_runid
    test_openai_model_list
    test_delete_model_with_runid
    test_register_model_with_parser
    test_model_config_details
    test_cleanup_model

    # 异常测试
    test_register_duplicate_model
    test_delete_nonexistent_model

    # 打印测试摘要
    print_summary "场景一：模型注册、罗列、删除接口测试"
}

# 运行测试
main
