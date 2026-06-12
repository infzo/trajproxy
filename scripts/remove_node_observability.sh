#!/usr/bin/env bash
# ============================================================
# remove_node_observability.sh — 从 Prometheus targets.json 移除 TrajProxy 节点
#
# 用法: ./scripts/remove_node_observability.sh <NODE_IP>
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGETS_FILE="${SCRIPT_DIR}/../dockers/observability/configs/prometheus/targets.json"

NODE_IP="${1:?❌ 请提供节点 IP: $0 <IP>}"

if [ ! -f "$TARGETS_FILE" ]; then
    echo "❌ targets.json 不存在"
    exit 1
fi

python3 -c "
import json

with open('$TARGETS_FILE') as f:
    data = json.load(f)

original_len = len(data)
data = [entry for entry in data if entry.get('labels', {}).get('node') != '$NODE_IP']

if len(data) < original_len:
    with open('$TARGETS_FILE', 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f'❌ 已移除节点 $NODE_IP')
else:
    print(f'⚠️  未找到节点 $NODE_IP')
"

echo "📋 剩余 $(python3 -c "import json; print(len(json.load(open('$TARGETS_FILE'))))" ) 个节点"
