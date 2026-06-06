#!/bin/bash
# Comparison Layer 配置: 对比测试层
# C1xx: OpenAI 格式 → vLLM:8080 vs trajproxy:12300
# C2xx: Claude 格式 → vLLM:8080 vs NGINX:12345

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../config.sh"

# trajproxy 地址（C1xx OpenAI 对比使用）
PROXY_URL="${TRAJ_PROXY_URL:-http://127.0.0.1:12300}"

# NGINX 入口地址（C2xx Claude 对比使用，Nginx → LiteLLM → trajproxy）
NGINX_URL="${NGINX_URL:-http://127.0.0.1:12345}"

# vLLM 原始推理服务（两者共用，vLLM 原生支持 /v1/chat/completions 和 /v1/messages）
VLLM_URL="${VLLM_URL:-http://127.0.0.1:8080}"

# 对比测试通用配置
COMPARISON_MODEL_NAME="${DEFAULT_MODEL_NAME}"
COMPARISON_TOKENIZER_PATH="${DEFAULT_TOKENIZER_PATH}"
COMPARISON_TOOL_PARSER="${DEFAULT_TOOL_PARSER}"
COMPARISON_REASONING_PARSER="${DEFAULT_REASONING_PARSER}"

# 固化采样参数（消除推理随机性）
COMPARISON_SAMPLING_PARAMS="${E2E_SAMPLING_PARAMS}"

# 推理请求 API Key
COMPARISON_API_KEY="${CHAT_API_KEY:-sk-1234}"
