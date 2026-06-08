#!/bin/bash
# C203: Claude TITO Tool 一致性 (对等C103)
# Pipeline: TITO | Parser: Tool | 轮次: 单轮 | 格式: Claude (ns+s)
# 关键: tool_use 块结构对比 + arguments 禁止特殊 token

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "=== C203: Claude TITO Tool 一致性 ==="

SCENARIO_ID=$(basename "${BASH_SOURCE[0]}" .sh | grep -oE '[A-Z][0-9]+' | tr '[:upper:]' '[:lower:]')
RUN_ID="run-${SCENARIO_ID}"
SESS_ID="sess-${SCENARIO_ID}-$(date +%s%N | md5sum | head -c 8)"
SESS_PATH="s/${RUN_ID}/${SESS_ID}"

check_nginx_health || { log_error "NGINX 不可用，跳过 C203"; exit 0; }

# 注册 TITO + tool_parser（仅 Tool，不含 Reasoning）
log_step "注册 TITO + tool_parser"
register_model "$RUN_ID" "$COMPARISON_MODEL_NAME" "true" "${COMPARISON_TOOL_PARSER}" ""

log_step "Claude 非流式对比 (tool)"
NS_REQ=$(build_claude_tool_request "$COMPARISON_MODEL_NAME")
VLLM_NS=$(send_claude_nonstream "$VLLM_URL" "$NS_REQ")
PROXY_NS=$(send_claude_nonstream "$NGINX_URL" "$NS_REQ" "$SESS_PATH")
compare_claude_nonstream "$VLLM_NS" "$PROXY_NS" "C203-claude-tito-tool"
rm -f "$VLLM_NS" "$PROXY_NS"

log_step "Claude 流式对比 (tool)"
S_REQ=$(build_claude_tool_stream_request "$COMPARISON_MODEL_NAME")
VLLM_S=$(send_claude_stream "$VLLM_URL" "$S_REQ")
PROXY_S=$(send_claude_stream "$NGINX_URL" "$S_REQ" "$SESS_PATH")
compare_claude_stream "$VLLM_S" "$PROXY_S" "C203-claude-tito-tool-stream"
rm -f "$VLLM_S" "$PROXY_S"

delete_model "$COMPARISON_MODEL_NAME" "$RUN_ID"
print_summary
