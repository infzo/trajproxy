#!/bin/bash
# 场景 C301: TITO 模式 - 纯文本 API 一致性验证（preserve_thinking=true）
# 测试流程：注册模型(TITO+qwen3) -> 非流式对比 -> 流式对比 -> 删除模型
# 核心看护: 转换管线(TITO)模式下 trajproxy 响应与 vLLM 原始响应的字段一致性
# 关键参数: preserve_thinking=true, enable_thinking=true, reasoning_parser=qwen3

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 C301: TITO 模式 - 纯文本 API 一致性验证"
echo "========================================"
echo ""

SCENARIO_ID=$(basename "${BASH_SOURCE[0]}" .sh | grep -oE 'C[0-9]+' | tr '[:upper:]' '[:lower:]')
TEST_RUN_ID="run-${SCENARIO_ID}"
TEST_SESSION_ID="sess-${SCENARIO_ID}-$(date +%s%N | md5sum | head -c 8)"
TEST_SESSION_PATH="s/${TEST_RUN_ID}/${TEST_SESSION_ID}"

# ========================================
# 步骤 1: 注册模型（TITO 模式）
# ========================================
log_step "步骤 1: 注册模型（TITO 模式 + qwen3 reasoning_parser, preserve_thinking=true）"
register_model "$TEST_RUN_ID" "$COMPARISON_MODEL_NAME" "true" "" "${COMPARISON_REASONING_PARSER}"

echo ""

# ========================================
# 步骤 2: 非流式对比
# ========================================
log_step "步骤 2: 非流式对比（vLLM vs trajproxy）"

NS_REQUEST=$(build_plain_request_body "${COMPARISON_MODEL_NAME}")

log_info "发送非流式请求到 vLLM..."
VLLM_NS_FILE=$(send_nonstream_request "${VLLM_URL}" "${COMPARISON_MODEL_NAME}" "$NS_REQUEST")

log_info "发送非流式请求到 trajproxy..."
PROXY_NS_FILE=$(send_nonstream_request "${PROXY_URL}" "${COMPARISON_MODEL_NAME}" "$NS_REQUEST" "${TEST_SESSION_PATH}")

compare_nonstream "$VLLM_NS_FILE" "$PROXY_NS_FILE" "C301-tito-plain"

rm -f "$VLLM_NS_FILE" "$PROXY_NS_FILE"

echo ""

# ========================================
# 步骤 3: 流式对比
# ========================================
log_step "步骤 3: 流式对比（vLLM vs trajproxy）"

S_REQUEST=$(build_plain_stream_request_body "${COMPARISON_MODEL_NAME}")

log_info "发送流式请求到 vLLM..."
VLLM_S_FILE=$(send_stream_request "${VLLM_URL}" "${COMPARISON_MODEL_NAME}" "$S_REQUEST")

log_info "发送流式请求到 trajproxy..."
PROXY_S_FILE=$(send_stream_request "${PROXY_URL}" "${COMPARISON_MODEL_NAME}" "$S_REQUEST" "${TEST_SESSION_PATH}")

compare_stream "$VLLM_S_FILE" "$PROXY_S_FILE" "C301-tito-plain-stream"

rm -f "$VLLM_S_FILE" "$PROXY_S_FILE"

echo ""

# ========================================
# 步骤 4: 删除模型
# ========================================
log_step "步骤 4: 删除模型"
delete_model "$COMPARISON_MODEL_NAME" "$TEST_RUN_ID"

echo ""

print_summary