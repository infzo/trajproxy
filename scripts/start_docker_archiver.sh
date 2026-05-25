#!/bin/bash
# 启动归档容器
#   ./scripts/start_docker_archiver.sh          # 本地存储模式
#   ./scripts/start_docker_archiver.sh --test   # MinIO S3 测试模式

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

MODE="prod"
if [ "$1" = "--test" ]; then
    MODE="test"
fi

echo "=== 归档容器启动 ($MODE 模式) ==="

# 检查镜像
if ! docker images traj_proxy:latest --format "{{.Repository}}" | grep -q traj_proxy; then
    echo "构建 traj_proxy 镜像..."
    docker compose -f "$PROJECT_ROOT/dockers/compose/docker-compose.yml" build traj_proxy
fi

cd "$PROJECT_ROOT"

if [ "$MODE" = "test" ]; then
    docker compose -f dockers/archiver/docker-compose-test.yml up -d
    echo ""
    echo "归档容器 + MinIO 已启动（仅 S3 模式）"
    echo "  MinIO API:     http://localhost:9000"
    echo "  MinIO Console: http://localhost:9001 (minioadmin/minioadmin)"
    echo "  查看日志:      docker logs -f traj-archiver-test-s3"
else
    docker compose -f dockers/archiver/docker-compose.yml up -d
    echo ""
    echo "归档容器已启动（本地存储）"
    echo "  查看日志: docker logs -f traj-archiver"
fi
