#!/bin/bash
# 公共配置参数

# 服务地址
BASE_URL="${TRAJ_PROXY_URL:-http://127.0.0.1:12300}"

# API 路径
API_MODELS="${BASE_URL}/models"

# 默认超时时间（秒）
DEFAULT_TIMEOUT=30

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 测试数据
TEST_MODEL_A_NAME="test-model-default"
TEST_MODEL_A_RUN_ID=""  # 空字符串，使用默认 DEFAULT
TEST_MODEL_B_NAME="test-model-run"
TEST_MODEL_B_RUN_ID="test-run-001"
TEST_MODEL_URL="http://test.example.com/v1"
TEST_MODEL_API_KEY="test-api-key-12345"

# 推理请求 API Key
CHAT_API_KEY="sk-1234"

# 后端推理服务地址（通过环境变量配置）
BACKEND_MODEL_URL="${BACKEND_MODEL_URL:-http://host.docker.internal:8000/v1}"
