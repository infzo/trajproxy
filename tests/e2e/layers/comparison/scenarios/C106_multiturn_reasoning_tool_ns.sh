#!/bin/bash
# C106: 多轮 T+R 非流式一致性 - OpenAI 格式
# Pipeline: TITO | Parser: T+R | 格式: OpenAI (ns)
# 子场景内嵌: tool_choice=required, tool_choice=named, enable_thinking=false
# 每轮对比 vLLM，C 系列不验证 cache_hit

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "=== C106: 多轮 T+R 非流式一致性 (OpenAI) ==="

SCENARIO_ID=$(basename "${BASH_SOURCE[0]}" .sh | grep -oE '[A-Z][0-9]+' | tr '[:upper:]' '[:lower:]')
RUN_ID="run-${SCENARIO_ID}"
SESS_ID="sess-${SCENARIO_ID}-$(date +%s%N | md5sum | head -c 8)"
SESS_PATH="s/${RUN_ID}/${SESS_ID}"

check_nginx_health || { log_error "NGINX 不可用，跳过 C106"; exit 0; }

log_step "注册 TITO + tool_parser + reasoning_parser"
register_model "$RUN_ID" "$COMPARISON_MODEL_NAME" "true" "${COMPARISON_TOOL_PARSER}" "${COMPARISON_REASONING_PARSER}"

# 第1轮: 基础 T+R 请求
log_step "第1轮: 基础 T+R 请求"
R1_REQ=$(build_openai_reasoning_tool_request "$COMPARISON_MODEL_NAME")
VLLM_R1=$(send_openai_nonstream "$VLLM_URL" "$R1_REQ")
PROXY_R1=$(send_openai_nonstream "$NGINX_URL" "$R1_REQ" "$SESS_PATH")
compare_openai_nonstream "$VLLM_R1" "$PROXY_R1" "C106-round1" "true"
rm -f "$VLLM_R1" "$PROXY_R1"

# 第2轮: tool_choice=required 强制调用 (子场景)
log_step "第2轮: tool_choice=required"
R2_REQ=$(echo "$R1_REQ" | sed 's/"stream":false/"stream":false,"tool_choice":"required"/')
VLLM_R2=$(send_openai_nonstream "$VLLM_URL" "$R2_REQ")
PROXY_R2=$(send_openai_nonstream "$NGINX_URL" "$R2_REQ" "$SESS_PATH")
compare_openai_nonstream "$VLLM_R2" "$PROXY_R2" "C106-round2-required" "true"
rm -f "$VLLM_R2" "$PROXY_R2"

# 第3轮: enable_thinking=false 关闭推理 (子场景)
log_step "第3轮: enable_thinking=false"
R3_REQ=$(echo "$R1_REQ" | sed 's/"enable_thinking":true/"enable_thinking":false/')
VLLM_R3=$(send_openai_nonstream "$VLLM_URL" "$R3_REQ")
PROXY_R3=$(send_openai_nonstream "$NGINX_URL" "$R3_REQ" "$SESS_PATH")
compare_openai_nonstream "$VLLM_R3" "$PROXY_R3" "C106-round3-nothink" "true"
rm -f "$VLLM_R3" "$PROXY_R3"

# 第4轮: tool_choice=named 指定工具 (子场景)
log_step "第4轮: tool_choice=named"
R4_REQ=$(echo "$R1_REQ" | sed 's/"stream":false/"stream":false,"tool_choice":{"type":"function","function":{"name":"get_weather"}}/')
VLLM_R4=$(send_openai_nonstream "$VLLM_URL" "$R4_REQ")
PROXY_R4=$(send_openai_nonstream "$NGINX_URL" "$R4_REQ" "$SESS_PATH")
compare_openai_nonstream "$VLLM_R4" "$PROXY_R4" "C106-round4-named" "true" "finish_reason,arguments"
rm -f "$VLLM_R4" "$PROXY_R4"

delete_model "$COMPARISON_MODEL_NAME" "$RUN_ID"
print_summary
