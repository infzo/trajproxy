#!/bin/bash
# Archive 层测试配置

# 数据库连接配置（从环境变量读取或使用默认值）
ARCHIVE_DB_HOST="${ARCHIVE_DB_HOST:-127.0.0.1}"
ARCHIVE_DB_PORT="${ARCHIVE_DB_PORT:-5432}"
ARCHIVE_DB_NAME="${ARCHIVE_DB_NAME:-traj_proxy}"
ARCHIVE_DB_USER="${ARCHIVE_DB_USER:-llmproxy}"
ARCHIVE_DB_PASSWORD="${ARCHIVE_DB_PASSWORD:-dbpassword9090}"

# 数据库连接 URL
ARCHIVE_DB_URL="postgresql://${ARCHIVE_DB_USER}:${ARCHIVE_DB_PASSWORD}@${ARCHIVE_DB_HOST}:${ARCHIVE_DB_PORT}/${ARCHIVE_DB_NAME}"

# 归档配置
ARCHIVE_STORAGE_PATH="${ARCHIVE_STORAGE_PATH:-/data/archives}"
ARCHIVE_RETENTION_DAYS="${ARCHIVE_RETENTION_DAYS:-30}"

# 测试数据配置
TEST_SESSION_PREFIX="test_archive_$(date +%s)"
TEST_MODEL_NAME="test-archive-model"

# 容器配置（自动检测运行中的容器）
if [ -z "${CONTAINER_NAME}" ]; then
    # 优先检测 allinone 模式
    if docker ps --format '{{.Names}}' | grep -q '^trajproxy-allinone$'; then
        CONTAINER_NAME="trajproxy-allinone"
    # 其次检测 compose 模式
    elif docker ps --format '{{.Names}}' | grep -q '^traj-proxy$'; then
        CONTAINER_NAME="traj-proxy"
    else
        CONTAINER_NAME="trajproxy-allinone"  # 默认值
    fi
fi

# 数据库容器配置（用于执行 psql 命令）
# allinone 模式: 数据库在应用容器内
# compose 模式: 数据库在独立容器 litellm_db
if [ -z "${DB_CONTAINER_NAME}" ]; then
    if [ "${CONTAINER_NAME}" = "traj-proxy" ]; then
        DB_CONTAINER_NAME="litellm_db"
    else
        DB_CONTAINER_NAME="${CONTAINER_NAME}"
    fi
fi
