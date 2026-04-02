#!/bin/bash
# ============================================
# TrajProxy 验证测试 - 配置文件
# ============================================
# 用途: 集中管理测试参数配置
# 使用: source tests/verify/config.sh
# ============================================

# ============================================
# 服务地址配置
# ============================================

# TrajProxy Worker 地址（直接访问）
export PROXY_URL="${PROXY_URL:-http://localhost:12300}"

# Nginx 网关地址（完整链路）
export NGINX_URL="${NGINX_URL:-http://localhost:12345}"

# 推理服务地址
export INFERENCE_URL="${INFERENCE_URL:-http://localhost:8000/v1}"

# ============================================
# API 密钥配置
# ============================================

# 测试用 API Key
export API_KEY="${API_KEY:-sk-test-key}"

# LiteLLM API Key（用于 Claude 测试）
export LITELLM_API_KEY="${LITELLM_API_KEY:-sk-1234}"

# ============================================
# 模型配置
# ============================================

# 测试模型名称（不带 run_id）
export TEST_MODEL="${TEST_MODEL:-test_model_$(date +%s)}"

# Token 模式测试模型名称
export TEST_MODEL_TOKEN="${TEST_MODEL_TOKEN:-test_token_model_$(date +%s)}"

# Tool/Reason 测试模型名称
export TEST_MODEL_TOOL="${TEST_MODEL_TOOL:-test_tool_model_$(date +%s)}"

# 使用的 Tokenizer 路径
export TOKENIZER_PATH="${TOKENIZER_PATH:-Qwen/Qwen2.5-3B}"

# ============================================
# Parser 配置
# ============================================

# Tool Parser
export TOOL_PARSER="${TOOL_PARSER:-deepseek_v3}"

# Reasoning Parser
export REASONING_PARSER="${REASONING_PARSER:-deepseek_r1}"

# ============================================
# 测试参数配置
# ============================================

# 测试运行 ID
export TEST_RUN_ID="verify_$(date +%Y%m%d_%H%M%S)"

# 默认 Session ID 格式: run_id,sample_id,task_id
export DEFAULT_SESSION_ID="${TEST_RUN_ID},sample_001,task_001"

# 是否显示详细输出
export VERBOSE="${VERBOSE:-true}"

# ============================================
# 辅助函数
# ============================================

# 生成 Session ID
# 用法: generate_session_id <sample_id> [task_id]
generate_session_id() {
    local sample_id="${1:-sample_001}"
    local task_id="${2:-task_001}"
    echo "${TEST_RUN_ID},${sample_id},${task_id}"
}

# 显示配置信息
show_config() {
    echo -e "${CYAN}当前测试配置:${NC}"
    echo -e "  PROXY_URL:         ${PROXY_URL}"
    echo -e "  NGINX_URL:         ${NGINX_URL}"
    echo -e "  INFERENCE_URL:     ${INFERENCE_URL}"
    echo -e "  API_KEY:           ${API_KEY}"
    echo -e "  TEST_MODEL:        ${TEST_MODEL}"
    echo -e "  TOKENIZER_PATH:    ${TOKENIZER_PATH}"
    echo -e "  TEST_RUN_ID:       ${TEST_RUN_ID}"
    echo ""
}

# 显示帮助信息
show_config_help() {
    cat << EOF
配置说明:

环境变量:
  PROXY_URL           TrajProxy Worker 地址 (默认: http://localhost:12300)
  NGINX_URL           Nginx 网关地址 (默认: http://localhost:12345)
  INFERENCE_URL       推理服务地址 (默认: http://localhost:8000/v1)
  API_KEY             API 密钥 (默认: sk-test-key)
  LITELLM_API_KEY     LiteLLM API 密钥 (默认: sk-1234)
  TEST_MODEL          测试模型名称 (默认: test_model_<timestamp>)
  TOKENIZER_PATH      Tokenizer 路径 (默认: Qwen/Qwen2.5-3B)
  TOOL_PARSER         Tool Parser (默认: deepseek_v3)
  REASONING_PARSER    Reasoning Parser (默认: deepseek_r1)
  VERBOSE             显示详细输出 (默认: true)

使用示例:
  # 自定义配置运行测试
  PROXY_URL=http://192.168.1.100:12300 TEST_MODEL=my_model ./run_all.sh

  # 查看当前配置
  source config.sh && show_config

EOF
}
