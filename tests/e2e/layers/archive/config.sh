#!/bin/bash
# Archive 层测试配置
# 归档进程已独立为 traj_archiver，与核心业务完全分离

# 数据库连接配置（从环境变量读取或使用默认值）
ARCHIVE_DB_HOST="${ARCHIVE_DB_HOST:-127.0.0.1}"
ARCHIVE_DB_PORT="${ARCHIVE_DB_PORT:-5432}"
ARCHIVE_DB_NAME="${ARCHIVE_DB_NAME:-traj_proxy}"
ARCHIVE_DB_USER="${ARCHIVE_DB_USER:-llmproxy}"
ARCHIVE_DB_PASSWORD="${ARCHIVE_DB_PASSWORD:-dbpassword9090}"

# 数据库连接 URL
ARCHIVE_DB_URL="postgresql://${ARCHIVE_DB_USER}:${ARCHIVE_DB_PASSWORD}@${ARCHIVE_DB_HOST}:${ARCHIVE_DB_PORT}/${ARCHIVE_DB_NAME}"

# 归档配置
ARCHIVE_RETENTION_DAYS="${ARCHIVE_RETENTION_DAYS:-30}"

# 测试数据配置
TEST_SESSION_PREFIX="test_archive_$(date +%s)"
TEST_MODEL_NAME="test-archive-model"

# 容器配置（自动检测归档容器）
ARCHIVER_CONTAINERS=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -E 'archiver' || true)
ARCHIVER_CONTAINER_COUNT=$(echo "$ARCHIVER_CONTAINERS" | grep -c . 2>/dev/null || echo "0")

if [ "${ARCHIVER_CONTAINER_COUNT}" -gt 1 ]; then
    echo "[WARN] 检测到 ${ARCHIVER_CONTAINER_COUNT} 个归档容器运行中: $(echo "$ARCHIVER_CONTAINERS" | tr '\n' ' ')"
    echo "       建议只保留 1 个归档容器，避免多个归档进程竞争同一数据库"
fi

if [ -z "${ARCHIVER_CONTAINER_NAME}" ]; then
    if docker ps --format '{{.Names}}' | grep -q '^traj-archiver-test-s3$'; then
        ARCHIVER_CONTAINER_NAME="traj-archiver-test-s3"
    elif docker ps --format '{{.Names}}' | grep -q '^traj-archiver$'; then
        ARCHIVER_CONTAINER_NAME="traj-archiver"
    else
        ARCHIVER_CONTAINER_NAME="traj-archiver"
    fi
fi

# 业务容器：用于检测核心业务不应包含归档代码
if [ -z "${PROXY_CONTAINER_NAME}" ]; then
    if docker ps --format '{{.Names}}' | grep -q '^traj-proxy$'; then
        PROXY_CONTAINER_NAME="traj-proxy"
    elif docker ps --format '{{.Names}}' | grep -q '^trajproxy-allinone$'; then
        PROXY_CONTAINER_NAME="trajproxy-allinone"
    else
        PROXY_CONTAINER_NAME="traj-proxy"
    fi
fi

# 数据库容器配置（用于执行 psql 命令）
if [ -z "${DB_CONTAINER_NAME}" ]; then
    if docker ps --format '{{.Names}}' | grep -q '^litellm_db$'; then
        DB_CONTAINER_NAME="litellm_db"
    elif [ "${PROXY_CONTAINER_NAME}" = "trajproxy-allinone" ]; then
        DB_CONTAINER_NAME="${PROXY_CONTAINER_NAME}"
    else
        DB_CONTAINER_NAME="litellm_db"
    fi
fi
