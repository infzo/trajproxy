#!/bin/bash
# 场景 C303: TITO 模式 - Reasoning 解析 API 一致性验证
# 测试流程：注册模型(TITO+qwen3) -> 非流式对比 -> 流式对比 -> 删除模型
# 核心看护: reasoning 字段结构/值在 trajproxy 与 vLLM 之间的一致性

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 C303: TITO 模式 - Reasoning 解析 API 一致性验证"
echo "========================================"
echo ""

SCENARIO_ID=$(basename "${BASH_SOURCE[0]}" .sh | grep -oE 'C[0-9]+' | tr '[:upper:]' '[:lower:]')
TEST_RUN_ID="run-${SCENARIO_ID}"
TEST_MODEL_NAME="tito-reasoning-${SCENARIO_ID}"

# ========================================
# 步骤 1: 注册模型（TITO + qwen3 reasoning_parser）
# ========================================
log_step "步骤 1: 注册模型（TITO + qwen3 reasoning_parser）"
register_model "$TEST_RUN_ID" "$TEST_MODEL_NAME" "true" "" "${COMPARISON_REASONING_PARSER}"

echo ""

# ========================================
# 步骤 2: 非流式对比（带 reasoning）
# ========================================
log_step "步骤 2: 非流式对比（带 reasoning）"

NS_REQUEST=$(build_reasoning_request_body "${COMPARISON_MODEL_NAME}")
PROXY_NS_REQUEST=$(build_reasoning_request_body "${TEST_MODEL_NAME}")

log_info "发送带 reasoning 非流式请求到 vLLM..."
VLLM_NS_FILE=$(send_nonstream_request "${VLLM_URL}" "${COMPARISON_MODEL_NAME}" "$NS_REQUEST")

log_info "发送带 reasoning 非流式请求到 trajproxy..."
PROXY_NS_FILE=$(send_nonstream_request "${PROXY_URL}" "${TEST_MODEL_NAME}" "$PROXY_NS_REQUEST")

compare_nonstream "$VLLM_NS_FILE" "$PROXY_NS_FILE" "C303-tito-reasoning"

rm -f "$VLLM_NS_FILE" "$PROXY_NS_FILE"

echo ""

# ========================================
# 步骤 3: 流式对比（带 reasoning）
# ========================================
log_step "步骤 3: 流式对比（带 reasoning）"

S_REQUEST=$(build_reasoning_stream_request_body "${COMPARISON_MODEL_NAME}")
PROXY_S_REQUEST=$(build_reasoning_stream_request_body "${TEST_MODEL_NAME}")

log_info "发送带 reasoning 流式请求到 vLLM..."
VLLM_S_FILE=$(send_stream_request "${VLLM_URL}" "${COMPARISON_MODEL_NAME}" "$S_REQUEST")

log_info "发送带 reasoning 流式请求到 trajproxy..."
PROXY_S_FILE=$(send_stream_request "${PROXY_URL}" "${TEST_MODEL_NAME}" "$PROXY_S_REQUEST")

compare_stream "$VLLM_S_FILE" "$PROXY_S_FILE" "C303-tito-reasoning-stream"

rm -f "$VLLM_S_FILE" "$PROXY_S_FILE"

echo ""

# ========================================
# 步骤 4: 删除模型
# ========================================
log_step "步骤 4: 删除模型"
delete_model "$TEST_MODEL_NAME" "$TEST_RUN_ID"

echo ""

print_summary