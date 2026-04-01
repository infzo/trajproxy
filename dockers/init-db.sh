#!/bin/bash
# 数据库初始化脚本
# 用于创建 traj_proxy 数据库

set -e

# 等待 PostgreSQL 启动
until pg_isready -d litellm -U llmproxy; do
    echo "等待 PostgreSQL 启动..."
    sleep 1
done

# 创建 traj_proxy 数据库（如果不存在）
echo "检查并创建 traj_proxy 数据库..."
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    SELECT 'CREATE DATABASE traj_proxy OWNER llmproxy'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'traj_proxy')\gexec
EOSQL

echo "数据库初始化完成"
