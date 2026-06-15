# Grafana Dashboard 源文件拆分方案

本目录 (`dashboard-src/`) 保存了 `trajproxy-overview.json` 的**片段化源文件**，通过构建脚本重新拼装为最终的 Dashboard JSON 文件。

## 动机

原始的 `trajproxy-overview.json` (~44KB / ~1900 行) 过大、难以维护。
为了在 **不改变 UI** 的前提下拆分，采用构建期合并策略：
- 每个 Grafana Row 独立为一个 JSON 文件
- Dashboard 元数据（uid、title、templating 等）存在 `dashboard.json`
- 运行 `make dashboard` 重新拼装为最终供 Grafana 加载的 JSON

## 为什么 src 不在 dashboards/ 目录下

Grafana provisioning 会**递归扫描** `dashboards/` 下的所有子目录。
如果把 `dashboard.json`（uid 与主文件相同的空面板 shell）放在 `dashboards/src/` 下，
Grafana 会同时加载两个 uid 相同的 dashboard，**后者覆盖前者导致面板消失**。

因此，fragment 源文件必须放在 provisioning 扫描路径之外：
```
configs/grafana/
├── dashboards/                # ← Grafana 会加载
│   └── trajproxy-overview.json
└── dashboard-src/             # ← Grafana 不会扫描（本目录）
    ├── dashboard.json
    ├── row-*.json
    └── build_dashboard.py
```

## 目录结构

```
configs/grafana/
├── dashboards/
│   └── trajproxy-overview.json   # 构建产物（由脚本生成）
└── dashboard-src/
    ├── dashboard.json            # Dashboard 元数据（无 panels）
    ├── row-0-overview.json       # Row 0: 全局总览
    ├── row-1-traffic.json        # Row 1: 流量概览
    ├── row-2-inference.json      # Row 2: 推理请求（最大）
    ├── row-3-trajectory.json     # Row 3: 轨迹请求
    ├── row-4-system.json         # Row 4: 系统资源
    ├── build_dashboard.py        # 构建脚本
    └── README.md
```

## 使用方法

### 修改面板
直接编辑对应的 `row-*.json` 文件。每个文件是一个 JSON 数组，
包含一个 `type: row` 的 Row header，以及属于该 Row 的所有面板。

### 重新组装
```bash
# 从项目根目录
cd dockers/observability
make dashboard

# 或直接运行脚本
python3 configs/grafana/dashboard-src/build_dashboard.py
```

Grafana provisioning 配置为每 30 秒扫描一次，通常不需要手动操作。
如需立刻生效：
```bash
curl -X POST http://localhost:3000/api/admin/provisioning/dashboards/reload
```

### 新增一个 Row
1. 创建新文件 `row-5-xxx.json`（命名 `row-N-*.json`，决定插入顺序）
2. 内容为 JSON 数组：`[{"type": "row", "title": "Row N", ...}, {...panels...}]`
3. 运行 `make dashboard`

## 约束与约定

- `row-*.json` 文件名按字典序排列，数字前缀决定 Row 顺序
- 不要手动编辑生成的 `trajproxy-overview.json`（会被下次构建覆盖）
- Grafana provisioning 自动加载 `dashboards/*.json`，无需额外配置

## 设计说明

该方案避免了 Grafana 原生 Library Panel 的单面板限制
（原生不支持"一组面板 = 一个 Library Panel"）。
构建期合并方案保持输出 JSON 与拆分前完全一致，UI 零影响。
