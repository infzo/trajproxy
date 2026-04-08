#!/bin/bash
# TrajProxy 镜像构建脚本
# 构建并导出 amd64 和 arm64 架构的 Docker 镜像

set -e

# 项目根目录
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGES_DIR="${PROJECT_ROOT}/dockers/images"
DOCKERFILE="${PROJECT_ROOT}/dockers/Dockerfile"

# 检查版本号参数
if [ -z "$1" ]; then
    echo "错误: 必须指定版本号"
    echo "用法: $0 <version>"
    echo "示例: $0 0.0.9"
    exit 1
fi

VERSION="$1"

# 转换版本号为文件名格式 (0.0.9 -> 009)
VERSION_NUM=$(echo "$VERSION" | tr -d '.' | sed 's/^0*//')
VERSION_NUM=$(printf "%03d" "$VERSION_NUM")

echo "=== 构建参数 ==="
echo "版本号: $VERSION"
echo "版本标识: $VERSION_NUM"
echo "Dockerfile: $DOCKERFILE"
echo "输出目录: $IMAGES_DIR"
echo ""

# 切换到项目根目录
cd "$PROJECT_ROOT"

# 确保 images 目录存在
mkdir -p "$IMAGES_DIR"

# 构建 amd64 镜像
echo "=== 构建 amd64 镜像 ==="
docker build --platform linux/amd64 -t traj-proxy-x86:${VERSION} -f "$DOCKERFILE" .
docker save -o "${IMAGES_DIR}/traj-proxy.amd64.${VERSION_NUM}.tar" traj-proxy-x86:${VERSION}
echo "amd64 镜像已导出: ${IMAGES_DIR}/traj-proxy.amd64.${VERSION_NUM}.tar"
echo ""

# 构建 arm64 镜像
echo "=== 构建 arm64 镜像 ==="
docker build --platform linux/arm64 -t traj-proxy-arm:${VERSION} -f "$DOCKERFILE" .
docker save -o "${IMAGES_DIR}/traj-proxy.arm64.${VERSION_NUM}.tar" traj-proxy-arm:${VERSION}
echo "arm64 镜像已导出: ${IMAGES_DIR}/traj-proxy.arm64.${VERSION_NUM}.tar"
echo ""

echo "=== 构建完成 ==="
echo "生成的镜像文件:"
ls -lh "${IMAGES_DIR}/traj-proxy."*.${VERSION_NUM}.tar 2>/dev/null || echo "无镜像文件"