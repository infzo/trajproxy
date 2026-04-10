#!/bin/bash
# Layer 2 配置: 直连 Proxy (port 12300)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../config.sh"

# 保持直连 proxy 地址
BASE_URL="${TRAJ_PROXY_URL:-http://127.0.0.1:12300}"
API_MODELS="${BASE_URL}/models"
