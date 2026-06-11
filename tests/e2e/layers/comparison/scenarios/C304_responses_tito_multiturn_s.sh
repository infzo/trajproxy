#!/bin/bash
# C304: 多轮 T+R 流式一致性 - Responses 格式 (对等C107)
# Pipeline: TITO | Parser: T+R | 格式: Responses (s)
# 3轮流式请求，每轮独立对比 vLLM vs proxy
# 不含 kwargs/tool_choice 子场景（Responses API 不支持 chat_template_kwargs）

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "=== C304: 多轮 T+R 流式一致性 (Responses) ==="

SCENARIO_ID=$(basename "${BASH_SOURCE[0]}" .sh | grep -oE '[A-Z][0-9]+' | tr '[:upper:]' '[:lower:]')
RUN_ID="run-${SCENARIO_ID}"
SESS_ID="sess-${SCENARIO_ID}-$(date +%s%N | md5sum | head -c 8)"
SESS_PATH="s/${RUN_ID}/${SESS_ID}"

check_nginx_health || { log_error "NGINX 不可用，跳过 C304"; exit 0; }

log_step "注册 TITO + tool_parser + reasoning_parser"
register_model "$RUN_ID" "$COMPARISON_MODEL_NAME" "true" "${COMPARISON_TOOL_PARSER}" "${COMPARISON_REASONING_PARSER}"

# 第1轮
log_step "第1轮: 基础 R+T 流式"
R1_REQ=$(build_responses_reasoning_tool_stream_request "$COMPARISON_MODEL_NAME")
VLLM_R1=$(send_responses_stream "$VLLM_URL" "$R1_REQ")
PROXY_R1=$(send_responses_stream "$NGINX_URL" "$R1_REQ" "$SESS_PATH")
compare_responses_stream "$VLLM_R1" "$PROXY_R1" "C304-round1-stream"
rm -f "$VLLM_R1" "$PROXY_R1"

# 第2轮: 不同问题
log_step "第2轮: 不同问题 流式"
R2_REQ=$(echo "$R1_REQ" | sed 's/Shanghai/Beijing/')
VLLM_R2=$(send_responses_stream "$VLLM_URL" "$R2_REQ")
PROXY_R2=$(send_responses_stream "$NGINX_URL" "$R2_REQ" "$SESS_PATH")
compare_responses_stream "$VLLM_R2" "$PROXY_R2" "C304-round2-stream"
rm -f "$VLLM_R2" "$PROXY_R2"

# 第3轮
log_step "第3轮 流式"
R3_REQ=$(echo "$R1_REQ" | sed 's/Shanghai/Shenzhen/')
VLLM_R3=$(send_responses_stream "$VLLM_URL" "$R3_REQ")
PROXY_R3=$(send_responses_stream "$NGINX_URL" "$R3_REQ" "$SESS_PATH")
compare_responses_stream "$VLLM_R3" "$PROXY_R3" "C304-round3-stream"
rm -f "$VLLM_R3" "$PROXY_R3"

delete_model "$COMPARISON_MODEL_NAME" "$RUN_ID"
print_summary
