#!/bin/bash
# 归档进程入口脚本
set -e

echo "=== 启动 TrajArchiver ==="
exec python -m traj_archiver
