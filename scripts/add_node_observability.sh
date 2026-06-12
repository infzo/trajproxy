#!/usr/bin/env bash
# ============================================================
# add_node_observability.sh — 向 Prometheus targets.json 添加新 TrajProxy 节点
#
# 用法:
#   ./scripts/add_node_observability.sh <NODE_IP> [PORT_START] [PORT_COUNT]
#
# 示例:
#   ./scripts/add_node_observability.sh 192.168.1.100              # 默认 12300, 10 个端口
#   ./scripts/add_node_observability.sh 192.168.1.100 12300 10      # 自定义
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGETS_FILE="${SCRIPT_DIR}/../docker/observability/prometheus/targets.json"

# --- 参数解析 ---
NODE_IP="${1:?❌ 请提供节点 IP: $0 <IP> [PORT_START] [PORT_COUNT]}"
PORT_START="${2:-12300}"
PORT_COUNT="${3:-10}"
END_PORT=$((PORT_START + PORT_COUNT - 1))

# --- 校验 IP 格式 ---
if ! echo "$NODE_IP" | grep -qE '^[0-9]{1,3}(\.[0-9]{1,3}){3}$'; then
    echo "❌ 无效的 IP 地址: $NODE_IP"
    exit 1
fi

# --- 构建 targets 数组 ---
TARGETS="[]"
for i in $(seq 0 $((PORT_COUNT - 1))); do
    PORT=$((PORT_START + i))
    TARGETS=$(echo "$TARGETS" | python3 -c "
import sys, json
t = json.load(sys.stdin)
t.append('${NODE_IP}:${PORT}')
json.dump(t, sys.stdout)
")
done

# --- 构建新条目 ---
NEW_ENTRY=$(python3 -c "
import json
print(json.dumps({
    'targets': $(echo "$TARGETS"),
    'labels': {
        'job': 'trajproxy',
        'node': '${NODE_IP}'
    }
}, indent=2, ensure_ascii=False))
")

# --- 更新 targets.json ---
if [ ! -f "$TARGETS_FILE" ]; then
    echo "[]" > "$TARGETS_FILE"
fi

# 检查节点是否已存在
EXISTING=$(python3 -c "
import json
with open('$TARGETS_FILE') as f:
    data = json.load(f)
for i, entry in enumerate(data):
    if entry.get('labels', {}).get('node') == '${NODE_IP}':
        print(i)
        break
else:
    print(-1)
")

python3 -c "
import json

with open('$TARGETS_FILE') as f:
    data = json.load(f)

new_entry = json.loads('''$NEW_ENTRY''')

existing_idx = ${EXISTING}
if existing_idx >= 0:
    data[existing_idx] = new_entry
    print('♻️  已更新节点 ${NODE_IP} (${PORT_COUNT} 个端口)')
else:
    data.append(new_entry)
    print('✅ 已添加节点 ${NODE_IP} (${PORT_COUNT} 个端口: ${PORT_START}-${END_PORT})')

with open('$TARGETS_FILE', 'w') as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
"

echo "📋 当前 targets.json 已包含 $(python3 -c "import json; print(len(json.load(open('$TARGETS_FILE'))))" ) 个节点"
echo "⏳ Prometheus 将在 30 秒内自动重载..."
