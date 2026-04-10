#!/bin/bash
# Layer 1 配置: Nginx 入口 (port 12345)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../config.sh"

# 覆盖为 nginx 入口地址
BASE_URL="${NGINX_URL:-http://127.0.0.1:12345}"
API_MODELS="${BASE_URL}/models"
