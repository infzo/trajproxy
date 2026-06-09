#!/bin/bash
# C104: TITO Reasoning 一致性 - OpenAI 格式 (对等原C303)
# Pipeline: TITO | Parser: Reasoning | 轮次: 单轮 | 格式: OpenAI (ns+s)
# 关键: 验证 reasoning_content 字段一致性 + content 剥离 </think>

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "=== C104: TITO Reasoning 一致性 (OpenAI) ==="

SCENARIO_ID=$(basename "${BASH_SOURCE[0]}" .sh | grep -oE '[A-Z][0-9]+' | tr '[:upper:]' '[:lower:]')
RUN_ID="run-${SCENARIO_ID}"
SESS_ID="sess-${SCENARIO_ID}-$(date +%s%N | md5sum | head -c 8)"
SESS_PATH="s/${RUN_ID}/${SESS_ID}"

check_nginx_health || { log_error "NGINX 不可用，跳过 C104"; exit 0; }

# 注册 TITO + reasoning_parser
log_step "注册 TITO + reasoning_parser"
register_model "$RUN_ID" "$COMPARISON_MODEL_NAME" "true" "" "${COMPARISON_REASONING_PARSER}"

# 非流式对比
log_step "非流式对比 (reasoning)"
NS_REQ=$(build_openai_reasoning_request "$COMPARISON_MODEL_NAME")
VLLM_NS=$(send_openai_nonstream "$VLLM_URL" "$NS_REQ")
PROXY_NS=$(send_openai_nonstream "$NGINX_URL" "$NS_REQ" "$SESS_PATH")
compare_openai_nonstream "$VLLM_NS" "$PROXY_NS" "C104-tito-reasoning" "true"
rm -f "$VLLM_NS" "$PROXY_NS"

# 流式对比
log_step "流式对比 (reasoning)"
S_REQ=$(build_openai_reasoning_stream_request "$COMPARISON_MODEL_NAME")
VLLM_S=$(send_openai_stream "$VLLM_URL" "$S_REQ")
PROXY_S=$(send_openai_stream "$NGINX_URL" "$S_REQ" "$SESS_PATH")
compare_openai_stream "$VLLM_S" "$PROXY_S" "C104-tito-reasoning-stream" "true"
rm -f "$VLLM_S" "$PROXY_S"

delete_model "$COMPARISON_MODEL_NAME" "$RUN_ID"
print_summary
