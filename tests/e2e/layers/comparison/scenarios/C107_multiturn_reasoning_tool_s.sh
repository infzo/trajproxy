#!/bin/bash
# C107: 多轮 T+R 流式一致性 - OpenAI 格式
# Pipeline: TITO | Parser: T+R | 轮次: 3轮 | 格式: OpenAI (s)
# 每轮流式重建后对比，同时验证流式缓存

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "=== C107: 多轮 T+R 流式一致性 (OpenAI) ==="

SCENARIO_ID=$(basename "${BASH_SOURCE[0]}" .sh | grep -oE '[A-Z][0-9]+' | tr '[:upper:]' '[:lower:]')
RUN_ID="run-${SCENARIO_ID}"
SESS_ID="sess-${SCENARIO_ID}-$(date +%s%N | md5sum | head -c 8)"
SESS_PATH="s/${RUN_ID}/${SESS_ID}"

check_nginx_health || { log_error "NGINX 不可用，跳过 C107"; exit 0; }

log_step "注册 TITO + tool_parser + reasoning_parser"
register_model "$RUN_ID" "$COMPARISON_MODEL_NAME" "true" "${COMPARISON_TOOL_PARSER}" "${COMPARISON_REASONING_PARSER}"

# 第1轮
log_step "第1轮: 基础 T+R 流式"
R1_REQ=$(build_openai_reasoning_tool_stream_request "$COMPARISON_MODEL_NAME")
VLLM_R1=$(send_openai_stream "$VLLM_URL" "$R1_REQ")
PROXY_R1=$(send_openai_stream "$NGINX_URL" "$R1_REQ" "$SESS_PATH")
compare_openai_stream "$VLLM_R1" "$PROXY_R1" "C107-round1-stream"
rm -f "$VLLM_R1" "$PROXY_R1"

# 第2轮: tool_choice=required
log_step "第2轮: tool_choice=required 流式"
R2_REQ=$(echo "$R1_REQ" | sed 's/"stream":true/"stream":true,"tool_choice":"required"/')
VLLM_R2=$(send_openai_stream "$VLLM_URL" "$R2_REQ")
PROXY_R2=$(send_openai_stream "$NGINX_URL" "$R2_REQ" "$SESS_PATH")
compare_openai_stream "$VLLM_R2" "$PROXY_R2" "C107-round2-required-stream"
rm -f "$VLLM_R2" "$PROXY_R2"

# 第3轮: enable_thinking=false
log_step "第3轮: enable_thinking=false 流式"
R3_REQ=$(echo "$R1_REQ" | sed 's/"enable_thinking":true/"enable_thinking":false/')
VLLM_R3=$(send_openai_stream "$VLLM_URL" "$R3_REQ")
PROXY_R3=$(send_openai_stream "$NGINX_URL" "$R3_REQ" "$SESS_PATH")
compare_openai_stream "$VLLM_R3" "$PROXY_R3" "C107-round3-nothink-stream"
rm -f "$VLLM_R3" "$PROXY_R3"

# 第4轮: tool_choice=named 流式
log_step "第4轮: tool_choice=named 流式"
R4_REQ=$(echo "$R1_REQ" | sed 's/"stream":true/"stream":true,"tool_choice":{"type":"function","name":"get_weather"}/')
VLLM_R4=$(send_openai_stream "$VLLM_URL" "$R4_REQ")
PROXY_R4=$(send_openai_stream "$NGINX_URL" "$R4_REQ" "$SESS_PATH")
compare_openai_stream "$VLLM_R4" "$PROXY_R4" "C107-round4-named-stream"
rm -f "$VLLM_R4" "$PROXY_R4"

# 验证流式缓存递增（4轮含子场景）
verify_cache_incremental "$SESS_ID" 4 "incremental"

delete_model "$COMPARISON_MODEL_NAME" "$RUN_ID"
print_summary
