#!/bin/bash
# C101: Direct T+R Parser 一致性 - OpenAI 格式
# Pipeline: Direct | Parser: T+R | 轮次: 单轮 | 格式: OpenAI (ns+s)
# 关键: Direct 模式下 reasoning + tool_calls 全字段对比

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "=== C101: Direct T+R Parser 一致性 (OpenAI) ==="

SCENARIO_ID=$(basename "${BASH_SOURCE[0]}" .sh | grep -oE '[A-Z][0-9]+' | tr '[:upper:]' '[:lower:]')
RUN_ID="run-${SCENARIO_ID}"
SESS_ID="sess-${SCENARIO_ID}-$(date +%s%N | md5sum | head -c 8)"
SESS_PATH="s/${RUN_ID}/${SESS_ID}"

check_nginx_health || { log_error "NGINX 不可用，跳过 C101"; exit 0; }

log_step "注册 Direct + tool_parser + reasoning_parser"
register_model "$RUN_ID" "$COMPARISON_MODEL_NAME" "false" "${COMPARISON_TOOL_PARSER}" "${COMPARISON_REASONING_PARSER}"

log_step "非流式对比 (direct+tr)"
NS_REQ=$(build_openai_reasoning_tool_request "$COMPARISON_MODEL_NAME")
VLLM_NS=$(send_openai_nonstream "$VLLM_URL" "$NS_REQ")
PROXY_NS=$(send_openai_nonstream "$NGINX_URL" "$NS_REQ" "$SESS_PATH")
compare_openai_nonstream "$VLLM_NS" "$PROXY_NS" "C101-direct-reasoning-tool" "true" "content"
rm -f "$VLLM_NS" "$PROXY_NS"

log_step "流式对比 (direct+tr)"
S_REQ=$(build_openai_reasoning_tool_stream_request "$COMPARISON_MODEL_NAME")
VLLM_S=$(send_openai_stream "$VLLM_URL" "$S_REQ")
PROXY_S=$(send_openai_stream "$NGINX_URL" "$S_REQ" "$SESS_PATH")
compare_openai_stream "$VLLM_S" "$PROXY_S" "C101-direct-reasoning-tool-stream" "true" "content"
rm -f "$VLLM_S" "$PROXY_S"

delete_model "$COMPARISON_MODEL_NAME" "$RUN_ID"
print_summary