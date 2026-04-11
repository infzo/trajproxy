#!/bin/bash
# Docker All-in-One 启动脚本
# 单容器部署，包含 nginx + litellm + postgres + traj_proxy

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
IMAGE_NAME="${1:-trajproxy-allinone}"
IMAGE_TAG="${2:-latest}"
CONTAINER_NAME="traj-proxy"

echo "=== TrajProxy All-in-One 启动脚本 ==="
echo "项目目录: ${PROJECT_DIR}"
echo "镜像名称: ${IMAGE_NAME}:${IMAGE_TAG}"
echo "容器名称: ${CONTAINER_NAME}"
echo ""

# 检查镜像是否存在，不存在则构建
if ! docker image inspect "${IMAGE_NAME}:${IMAGE_TAG}" &>/dev/null; then
    echo "镜像不存在，正在构建..."
    "${SCRIPT_DIR}/build_image.sh" "${IMAGE_NAME}" "${IMAGE_TAG}"
    echo ""
fi

# 停止并删除旧容器
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "停止并删除旧容器..."
    docker stop "${CONTAINER_NAME}" 2>/dev/null || true
    docker rm "${CONTAINER_NAME}" 2>/dev/null || true
fi

# 创建数据目录
mkdir -p "${PROJECT_DIR}/models"
mkdir -p "${PROJECT_DIR}/logs"

echo "启动容器..."
docker run -d --name "${CONTAINER_NAME}" \
    --shm-size=1g \
    --add-host=host.docker.internal:host-gateway \
    -p 12345:12345 \
    -p 12300:12300 \
    -v trajproxy_pgdata:/data/postgres \
    -v trajproxy_logs:/app/logs \
    -v "${PROJECT_DIR}/models:/app/models" \
    "${IMAGE_NAME}:${IMAGE_TAG}"

echo ""
echo "=== TrajProxy 服务已启动 ==="
echo "查看日志: docker logs -f ${CONTAINER_NAME}"
echo "停止服务: docker stop ${CONTAINER_NAME}"
echo "删除容器: docker rm ${CONTAINER_NAME}"
