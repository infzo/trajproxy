#!/bin/bash
# C204: Claude TITO Reasoning 一致性 (对等C104)
# Pipeline: TITO | Parser: Reasoning | 轮次: 单轮 | 格式: Claude (ns+s)
# 关键: thinking 块一致性 + text 块中禁止 </think> 泄漏

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "=== C204: Claude TITO Reasoning 一致性 ==="

SCENARIO_ID=$(basename "${BASH_SOURCE[0]}" .sh | grep -oE '[A-Z][0-9]+' | tr '[:upper:]' '[:lower:]')
RUN_ID="run-${SCENARIO_ID}"
SESS_ID="sess-${SCENARIO_ID}-$(date +%s%N | md5sum | head -c 8)"
SESS_PATH="s/${RUN_ID}/${SESS_ID}"

check_nginx_health || { log_error "NGINX 不可用，跳过 C204"; exit 0; }

log_step "注册 TITO + reasoning_parser"
register_model "$RUN_ID" "$COMPARISON_MODEL_NAME" "true" "" "${COMPARISON_REASONING_PARSER}"

log_step "Claude 非流式对比 (reasoning)"
NS_REQ=$(build_claude_reasoning_request "$COMPARISON_MODEL_NAME")
VLLM_NS=$(send_claude_nonstream "$VLLM_URL" "$NS_REQ")
PROXY_NS=$(send_claude_nonstream "$NGINX_URL" "$NS_REQ" "$SESS_PATH")
compare_claude_nonstream "$VLLM_NS" "$PROXY_NS" "C204-claude-tito-reasoning"
rm -f "$VLLM_NS" "$PROXY_NS"

log_step "Claude 流式对比 (reasoning)"
S_REQ=$(build_claude_reasoning_stream_request "$COMPARISON_MODEL_NAME")
VLLM_S=$(send_claude_stream "$VLLM_URL" "$S_REQ")
PROXY_S=$(send_claude_stream "$NGINX_URL" "$S_REQ" "$SESS_PATH")
compare_claude_stream "$VLLM_S" "$PROXY_S" "C204-claude-tito-reasoning-stream"
rm -f "$VLLM_S" "$PROXY_S"

delete_model "$COMPARISON_MODEL_NAME" "$RUN_ID"
print_summary
