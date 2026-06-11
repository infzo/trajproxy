#!/bin/bash
# C302: TITO R+T Parser 一致性 - Responses 格式 (对等C105)
# Pipeline: TITO | Parser: T+R | 轮次: 单轮 | 格式: Responses (ns+s)
# 关键: 全字段 reasoning + function_call 逐项对比

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "=== C302: TITO R+T Parser 一致性 (Responses) ==="

SCENARIO_ID=$(basename "${BASH_SOURCE[0]}" .sh | grep -oE '[A-Z][0-9]+' | tr '[:upper:]' '[:lower:]')
RUN_ID="run-${SCENARIO_ID}"
SESS_ID="sess-${SCENARIO_ID}-$(date +%s%N | md5sum | head -c 8)"
SESS_PATH="s/${RUN_ID}/${SESS_ID}"

check_nginx_health || { log_error "NGINX 不可用，跳过 C302"; exit 0; }

log_step "注册 TITO + tool_parser + reasoning_parser"
register_model "$RUN_ID" "$COMPARISON_MODEL_NAME" "true" "${COMPARISON_TOOL_PARSER}" "${COMPARISON_REASONING_PARSER}"

log_step "非流式对比 (reasoning+tool)"
NS_REQ=$(build_responses_reasoning_tool_request "$COMPARISON_MODEL_NAME")
VLLM_NS=$(send_responses_nonstream "$VLLM_URL" "$NS_REQ")
PROXY_NS=$(send_responses_nonstream "$NGINX_URL" "$NS_REQ" "$SESS_PATH")
compare_responses_nonstream "$VLLM_NS" "$PROXY_NS" "C302-tito-reasoning-tool"
rm -f "$VLLM_NS" "$PROXY_NS"

log_step "流式对比 (reasoning+tool)"
S_REQ=$(build_responses_reasoning_tool_stream_request "$COMPARISON_MODEL_NAME")
VLLM_S=$(send_responses_stream "$VLLM_URL" "$S_REQ")
PROXY_S=$(send_responses_stream "$NGINX_URL" "$S_REQ" "$SESS_PATH")
compare_responses_stream "$VLLM_S" "$PROXY_S" "C302-tito-reasoning-tool-stream"
rm -f "$VLLM_S" "$PROXY_S"

delete_model "$COMPARISON_MODEL_NAME" "$RUN_ID"
print_summary
