#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCENARIOS_DIR="${SCRIPT_DIR}/scenarios"

echo "=== Performance Layer ==="
for f in "${SCENARIOS_DIR}"/*.sh; do
    if [ -f "$f" ]; then
        echo "Running: $(basename "$f")"
        bash "$f" || echo "FAILED: $(basename "$f")"
    fi
done