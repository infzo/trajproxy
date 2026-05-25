#!/bin/bash
# 停止归档容器
#   ./scripts/stop_docker_archiver.sh          # 停止本地存储模式
#   ./scripts/stop_docker_archiver.sh --test   # 停止 MinIO 测试模式
#   ./scripts/stop_docker_archiver.sh --all    # 停止全部

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

if [ "$1" = "--all" ]; then
    echo "停止全部归档容器..."
    docker compose -f dockers/archiver/docker-compose.yml down 2>/dev/null || true
    docker compose -f dockers/archiver/docker-compose-test.yml down 2>/dev/null || true
elif [ "$1" = "--test" ]; then
    echo "停止测试环境..."
    docker compose -f dockers/archiver/docker-compose-test.yml down
else
    echo "停止归档容器..."
    docker compose -f dockers/archiver/docker-compose.yml down
fi
