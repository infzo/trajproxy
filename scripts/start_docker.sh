#!/bin/bash
# Docker Compose 启动脚本
# 使用 docker-compose 拉起所有服务容器（litellm、postgresdb、traj_proxy、prometheus）

# 切换到 dockers 目录
cd "$(dirname "$0")/../dockers"

# 停止所有服务
docker-compose down

# 启动所有服务
docker-compose up -d

echo "=== TrajProxy 服务已启动 ==="
echo "查看日志: docker-compose logs -f"
echo "停止服务: docker-compose down"
