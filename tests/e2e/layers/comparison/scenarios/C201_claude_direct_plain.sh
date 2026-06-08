#!/bin/bash
# C201: Claude Direct 纯文本一致性 (迁移+升级原F101/F109)
# Pipeline: Direct | Parser: 无 | 轮次: 单轮 | 格式: Claude (ns+s)
# 基线: vLLM /v1/messages | 被测: NGINX /v1/messages

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "=== C201: Claude Direct 纯文本一致性 ==="

SCENARIO_ID=$(basename "${BASH_SOURCE[0]}" .sh | grep -oE '[A-Z][0-9]+' | tr '[:upper:]' '[:lower:]')
RUN_ID="run-${SCENARIO_ID}"
SESS_ID="sess-${SCENARIO_ID}-$(date +%s%N | md5sum | head -c 8)"
SESS_PATH="s/${RUN_ID}/${SESS_ID}"

# 前置检查
check_nginx_health || { log_error "NGINX 不可用，跳过 C201"; exit 0; }

# 注册 Direct 模型
log_step "注册 Direct 模型"
register_model "$RUN_ID" "$COMPARISON_MODEL_NAME" "false"

# 非流式对比
log_step "Claude 非流式对比"
NS_REQ=$(build_claude_plain_request "$COMPARISON_MODEL_NAME")
VLLM_NS=$(send_claude_nonstream "$VLLM_URL" "$NS_REQ")
PROXY_NS=$(send_claude_nonstream "$NGINX_URL" "$NS_REQ" "$SESS_PATH")
compare_claude_nonstream "$VLLM_NS" "$PROXY_NS" "C201-claude-direct-plain"
rm -f "$VLLM_NS" "$PROXY_NS"

# 流式对比
log_step "Claude 流式对比"
S_REQ=$(build_claude_plain_stream_request "$COMPARISON_MODEL_NAME")
VLLM_S=$(send_claude_stream "$VLLM_URL" "$S_REQ")
PROXY_S=$(send_claude_stream "$NGINX_URL" "$S_REQ" "$SESS_PATH")
compare_claude_stream "$VLLM_S" "$PROXY_S" "C201-claude-direct-plain-stream"
rm -f "$VLLM_S" "$PROXY_S"

delete_model "$COMPARISON_MODEL_NAME" "$RUN_ID"
print_summary
