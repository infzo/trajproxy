#!/usr/bin/env python3
"""组装脚本: 把 fragment 文件重新拼装为 trajproxy-overview.json。

本脚本位于 configs/grafana/dashboard-src/ 下，源文件也在此目录，
输出到 configs/grafana/dashboards/trajproxy-overview.json（Grafana provisioning 扫描目录）。

用法:
    cd dockers/observability && make dashboard
    或
    python3 configs/grafana/dashboard-src/build_dashboard.py
"""
import json
import sys
from pathlib import Path

# 当前脚本所在目录即 fragment 源文件目录
SRC_DIR = Path(__file__).resolve().parent
ROOT = SRC_DIR.parent  # configs/grafana/

SHELL = SRC_DIR / "dashboard.json"
if not SHELL.exists():
    sys.exit(f"找不到 shell 文件: {SHELL}")

OUT = ROOT / "dashboards" / "trajproxy-overview.json"

with SHELL.open(encoding="utf-8") as f:
    dashboard = json.load(f)

# 按文件名排序加载 row fragments (row-0-* 在前, row-4-* 在后)
row_files = sorted(SRC_DIR.glob("row-*.json"))
if not row_files:
    sys.exit(f"dashboard-src/ 下未找到 row-*.json fragment 文件")

panels: list[dict] = []
for rf in row_files:
    with rf.open(encoding="utf-8") as f:
        group = json.load(f)
    if not isinstance(group, list):
        sys.exit(f"{rf} 不是 JSON 数组")
    panels.extend(group)
    print(f"  + {rf.name:28s} {len(group):3d} panel(s)")

# 替换占位空 panels 数组
dashboard["panels"] = panels

with OUT.open("w", encoding="utf-8") as f:
    json.dump(dashboard, f, ensure_ascii=False, indent=2)
    f.write("\n")

print(f"\n[OK] 已组装 {OUT} (共 {len(panels)} 面板)")
