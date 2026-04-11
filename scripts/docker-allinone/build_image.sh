#!/bin/bash
# 构建 TrajProxy All-in-One 镜像
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
IMAGE_NAME="${1:-trajproxy-allinone}"
IMAGE_TAG="${2:-latest}"

echo "=== 构建 TrajProxy All-in-One 镜像 ==="
echo "项目目录: ${PROJECT_DIR}"
echo "镜像名称: ${IMAGE_NAME}:${IMAGE_TAG}"

docker build \
    -f "${PROJECT_DIR}/dockers/Dockerfile.allinone" \
    -t "${IMAGE_NAME}:${IMAGE_TAG}" \
    "${PROJECT_DIR}"

echo ""
echo "=== 构建完成 ==="
echo "镜像: ${IMAGE_NAME}:${IMAGE_TAG}"
echo ""
echo "运行方式:"
echo "  docker run -d --name trajproxy \\"
echo "    --shm-size=1g \\"
echo "    -p 12345:12345 \\"
echo "    -v trajproxy_pgdata:/data/postgres \\"
echo "    -v trajproxy_logs:/app/logs \\"
echo "    -v /path/to/models:/app/models \\"
echo "    ${IMAGE_NAME}:${IMAGE_TAG}"
