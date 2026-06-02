#!/bin/bash
# Layer: Cache Infer Server 配置
#
# 此层验证 CacheInferServer 的缓存命中/未命中/清除功能。
# CacheInferServer 位于 traj_proxy 和真实推理服务之间，
# 通过缓存推理响应加速 E2E 测试执行。
#
# 集成方式：
#   将 BACKEND_MODEL_URL 指向 CacheInferServer 即可启用缓存：
#     export BACKEND_MODEL_URL="http://127.0.0.1:18999/v1"
#   CacheInferServer 内部使用 REAL_INFER_URL 转发到真实后端。

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../config.sh"

# CacheInferServer 监听地址
CACHE_INFER_PORT="${CACHE_INFER_PORT:-18999}"
CACHE_INFER_URL="${CACHE_INFER_URL:-http://127.0.0.1:${CACHE_INFER_PORT}}"

# 真实推理后端（CacheInferServer 在缓存未命中时转发到的地址）
REAL_INFER_URL="${REAL_INFER_URL:-${BACKEND_MODEL_URL}}"

# 真实推理后端 API Key（可选，不设置则透传）
REAL_INFER_API_KEY="${REAL_INFER_API_KEY:-}"

# CacheInferServer 脚本路径
CACHE_INFER_SERVER="${SCRIPT_DIR}/../../cache_infer_server.py"
