# 可观测性运维指南

> **导航**: [文档中心](../README.md) | [部署指南](deployment.md) | [配置说明](configuration.md) | [可观测性设计](../design/observability.md)

本文档覆盖 TrajProxy 可观测性栈（Prometheus + Grafana + AlertManager + Viewer）的部署、配置和日常运维操作。

---

## 前置要求

- TrajProxy Workers 已运行并暴露 `/metrics` 端点（默认端口 12300-1230N）
- Docker >= 20.10 + Docker Compose V2
- 可观测性栈独立部署，**不与业务服务共用 Docker Compose**

---

## 快速开始

### 1. 初始化环境

```bash
# 从项目根目录执行
cd dockers/observability

# 创建 .env 配置文件
cp .env.example .env
```

### 2. 编辑 .env

至少确认以下配置：

```bash
# 端口（Docker Desktop 占用 9090，Prometheus 使用 19090）
PROMETHEUS_PORT=19090
GRAFANA_PORT=3000
ALERTMANAGER_PORT=9093
VIEWER_PORT=8081

# Grafana 管理员账号
GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=trajproxy

# Worker 端口范围（对应 config.yaml 中 proxy_workers.base_port）
WORKER_PORT_START=12300
WORKER_PORT_COUNT=10

# 监控节点（空格分隔 IP，留空则不抓取任何业务指标）
MONITOR_NODES="localhost"
```

### 3. 一键部署

```bash
# 方式一：Makefile（推荐）
cd dockers/observability
make start

# 方式二：脚本
bash scripts/start_docker_observability.sh start

# 方式三：临时指定节点（不写回 .env）
make start IP="192.168.1.100 10.0.0.5"
```

### 4. 验证

```bash
# 检查服务状态
make status

# 验证 Prometheus 健康
curl http://localhost:19090/-/healthy

# 验证 Grafana 健康
curl http://localhost:3000/api/health

# 验证 Prometheus 抓取目标
curl http://localhost:19090/api/v1/targets | python3 -m json.tool
```

### 5. 访问

| 服务 | URL | 说明 |
|------|-----|------|
| Prometheus | http://localhost:19090 | 指标查询、告警规则状态 |
| Grafana | http://localhost:3000 | Dashboard 可视化（admin / trajproxy） |
| AlertManager | http://localhost:9093 | 告警状态、静默管理 |
| Trajectory Viewer | http://localhost:8081 | 轨迹回放页面 |

---

## 多节点监控管理

### 添加监控节点

```bash
# 持久追加（写回 .env 的 MONITOR_NODES，自动生成 targets.json）
make add IP=192.168.1.100

# 查看当前节点
make targets
```

执行后：
1. `.env` 中的 `MONITOR_NODES` 追加该 IP
2. `configs/prometheus/targets.json` 自动重新生成
3. 该节点展开为 `[IP:12300, IP:12301, ..., IP:12300+PORT_COUNT-1]`

### 移除监控节点

```bash
make remove IP=192.168.1.100
```

### 仅重新生成 targets.json（不重启容器）

```bash
make sync
```

**适用场景**：手动修改 `.env` 中的 `MONITOR_NODES` 后，同步 targets 文件。

### 临时指定节点（不持久化）

```bash
# 启动时临时覆盖（不写回 .env）
make start IP="10.0.0.1 10.0.0.2"

# 重启时临时覆盖
make restart IP="10.0.0.1 10.0.0.2"
```

---

## Prometheus 配置

### 抓取配置

配置文件：`dockers/observability/configs/prometheus/prometheus.yml`

```yaml
global:
  scrape_interval: 15s          # 每 15s 抓一次指标
  evaluation_interval: 15s      # 每 15s 评估一次告警规则

scrape_configs:
  - job_name: "trajproxy"       # TrajProxy Workers
    file_sd_configs:
      - files: ["targets.json"]  # 动态加载，由管理脚本生成
        refresh_interval: 30s

  - job_name: "litellm"         # LiteLLM（可选）
    static_configs:
      - targets: ["host.docker.internal:4000"]

  - job_name: "prometheus"      # 自身监控
    static_configs:
      - targets: ["localhost:19090"]
```

### targets.json 格式

由管理脚本自动生成，格式如下：

```json
[
  {
    "targets": ["host.docker.internal:12300", "host.docker.internal:12301", "..."],
    "labels": { "job": "trajproxy", "node": "localhost" }
  },
  {
    "targets": ["192.168.1.100:12300", "192.168.1.100:12301", "..."],
    "labels": { "job": "trajproxy", "node": "192.168.1.100" }
  }
]
```

- `node` 标签用于 Grafana 按节点过滤
- `localhost` / `127.0.0.1` 自动替换为 `host.docker.internal`

### 热重载配置

修改 `prometheus.yml` 或 `alert_rules.yml` 后，无需重启容器：

```bash
make reload
# 等价于：curl -X POST http://localhost:19090/-/reload
```

### 查看抓取目标状态

```bash
# 通过 API
curl http://localhost:19090/api/v1/targets | python3 -c "
import sys, json
data = json.load(sys.stdin)
for t in data['data']['activeTargets']:
    print(f\"{t['labels']['instance']:30s} {t['health']:8s} {t.get('lastError', '')}\")
"
```

### 数据保留策略

`.env` 中配置：

```bash
PROMETHEUS_RETENTION=30d         # 时间保留：30 天
PROMETHEUS_RETENTION_SIZE=50GB   # 空间保留：50GB
```

取先到者。需重启 Prometheus 容器生效。

---

## Grafana 配置与使用

### 登录

- URL: `http://localhost:3000`
- 默认账号: `admin` / `trajproxy`
- 支持匿名访问（Viewer 角色）

### 预置 Dashboard

启动后自动加载 `TrajProxy Overview` Dashboard（5 Row ~30 面板）：

| Row | 内容 | 典型用途 |
|-----|------|---------|
| Global Overview | QPS、成功率、错误率、活跃请求、并发利用率 | 宏观健康度 |
| Traffic Overview | 请求速率时序、响应码分布热力图、延迟趋势 | 负载分析 |
| Inference Requests | 推理延迟、错误率、重试率、Token 用量、模型维度 | 推理服务诊断 |
| Trajectory Requests | 轨迹存储操作量、存储错误 | 存储层监控 |
| System Resources | CPU、内存 RSS、文件描述符、DB 连接池 | 资源水位 |

**模板变量**：

- `$datasource`: Prometheus 数据源
- `$node`: 按节点过滤（对应 `targets.json` 中的 `node` 标签）
- `$model`: 按模型过滤

### Dashboard 片段化构建

Dashboard 采用片段化管理，修改面板后需重新组装：

```bash
# 1. 编辑片段源文件
vim dockers/observability/configs/grafana/dashboard-src/row-2-inference.json

# 2. 重新组装
cd dockers/observability
make dashboard

# 3. 重启 Grafana 容器加载新版本
docker compose restart grafana
```

**片段文件说明**：

| 文件 | 内容 |
|------|------|
| `dashboard.json` | Dashboard 元数据（uid、title、模板变量） |
| `row-0-overview.json` | Row 0: 全局概览面板 |
| `row-1-traffic.json` | Row 1: 流量面板 |
| `row-2-inference.json` | Row 2: 推理面板（最大） |
| `row-3-trajectory.json` | Row 3: 轨迹面板 |
| `row-4-system.json` | Row 4: 系统资源面板 |

**重要**：片段文件在 `dashboard-src/` 目录下，此目录**不在** Grafana 的 provision 扫描路径内。
如果将片段文件和构建产物放在同一被扫描目录，会导致同 uid Dashboard 重复加载冲突。

### 在 Grafana UI 中修改面板

Grafana 已启用 `allowUiUpdates: true`，可以直接在 UI 中修改面板并保存。
但 UI 修改不会同步回 `dashboard-src/` 片段文件。

**建议工作流**：
1. 先在 UI 中调试面板直到满意
2. 导出 JSON，手动合并到对应的 `row-*.json` 片段文件
3. 执行 `make dashboard` + 重启 Grafana
4. 验证面板正常后提交代码

---

## 告警配置

### 告警规则

配置文件：`dockers/observability/configs/prometheus/alert_rules.yml`

7 条规则，按紧急程度分级：

| 规则 | 等级 | 触发条件 | 典型处置 |
|------|------|---------|---------|
| `InferErrors` | **critical** | 推理错误 > 0.5/s 持续 3m | 检查推理服务状态 |
| `WorkerDown` | **critical** | Worker 不可达 1m | 检查 Worker 进程、网络 |
| `HighErrorRate` | warning | 错误率 > 5% 持续 5m | 检查 Dashboard 错误面板 |
| `HighLatency` | warning | P95 > 60s 持续 5m | 检查推理延迟、DB 连接池 |
| `InferRetriesHigh` | warning | 重试 > 1/s 持续 5m | 检查推理服务负载 |
| `DBPoolSaturation` | warning | 连接池 > 85% 持续 5m | 增大 pool.max_size 或排查慢查询 |
| `WorkerHighMemory` | warning | RSS > 2GB 持续 10m | 检查内存泄漏、重启 Worker |

### 告警路由

配置文件：`dockers/observability/configs/alertmanager/alertmanager.yml`

```
Firing 告警
    ├── group_by [alertname, severity]
    ├── group_wait: 30s（聚合窗口，同类告警合并）
    ├── group_interval: 5m（同组通知间隔）
    ├── route:
    │     severity=critical → "critical" receiver, repeat 1h
    │     severity=warning  → "default" receiver,  repeat 4h
    └── inhibit: critical 抑制同 alertname 的 warning
```

### 启用通知渠道

编辑 `alertmanager.yml`，取消对应 receiver 的注释并填入配置：

**钉钉 Webhook**：

```yaml
receivers:
  - name: "default"
    webhook_configs:
      - url: "https://oapi.dingtalk.com/robot/send?access_token=YOUR_TOKEN"
        send_resolved: true

  - name: "critical"
    webhook_configs:
      - url: "https://oapi.dingtalk.com/robot/send?access_token=YOUR_TOKEN"
        send_resolved: true
```

**邮件通知**：

```yaml
receivers:
  - name: "critical"
    email_configs:
      - to: "oncall@example.com"
        from: "alertmanager@example.com"
        smarthost: "smtp.example.com:587"
        auth_username: "alertmanager@example.com"
        auth_password: "your_password"
        send_resolved: true
```

修改后执行 `make reload` 或重启 AlertManager 容器。

### 手动测试告警

在 Prometheus UI（http://localhost:19090）的 Alert 页面查看所有规则状态。

也可通过 AlertManager API 手动创建静默（测试时抑制通知）：

```
http://localhost:9093 → Silences 标签页 → New Silence
```

---

## 常用运维操作

### Makefile 命令速查

```bash
cd dockers/observability

# 服务管理
make start              # 一键部署（环境检查 + 拉镜像 + 启动 + 健康检查）
make restart            # 重启
make stop               # 停止（数据保留）

# 节点管理
make add IP=<IP>        # 持久追加节点
make remove IP=<IP>     # 持久移除节点
make sync               # 仅重新生成 targets.json
make targets            # 查看当前监控节点

# 配置管理
make reload             # 热重载 Prometheus 配置（无需重启）
make dashboard          # 从片段重新组装 Grafana Dashboard

# 调试
make status             # 查看服务状态（含 sync）
make logs               # 查看组件日志（tail -f）
make up                 # 原始 docker compose up -d
make down               # 原始 docker compose down
```

### 脚本命令

```bash
bash scripts/start_docker_observability.sh <command> [options]

# 子命令
start [IP1 IP2 ...]                        # 一键部署
stop                                       # 停止
restart [IP1 IP2 ...]                      # 重启
add-node  <IP> [PORT_START] [PORT_COUNT]  # 持久追加节点
remove-node <IP>                           # 持久移除节点
sync                                       # 仅重新生成 targets.json
```

### 日志查看

```bash
# 所有组件
cd dockers/observability
docker compose logs -f

# 指定组件
docker compose logs -f prometheus
docker compose logs -f grafana
docker compose logs -f alertmanager
```

### Prometheus 运维查询

在 Grafana Explore 或 Prometheus UI 中常用查询：

```promql
# 当前各 Worker 是否在线
up{job="trajproxy"}

# 各节点 QPS
sum by (node) (rate(trajproxy_requests_total[5m]))

# 全局成功率
sum(rate(trajproxy_requests_total{outcome="success"}[5m]))
  / sum(rate(trajproxy_requests_total[5m])) * 100

# P95 延迟
histogram_quantile(0.95,
  sum by (le) (rate(trajproxy_request_duration_seconds_bucket[5m])))

# 推理错误 top 模型
topk(5, sum by (model) (rate(trajproxy_infer_errors_total[5m])))

# DB 连接池使用率
trajproxy_db_pool_usage{state="used"} / trajproxy_db_pool_usage{state="peak"}

# Worker 内存（RSS）
process_resident_memory_bytes{job="trajproxy"}

# 1h 请求总量
sum(increase(trajproxy_requests_total[1h]))
```

---

## 环境变量完整参考

配置文件：`dockers/observability/.env`

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PROMETHEUS_PORT` | 19090 | Prometheus Web 端口 |
| `GRAFANA_PORT` | 3000 | Grafana Web 端口 |
| `ALERTMANAGER_PORT` | 9093 | AlertManager Web 端口 |
| `VIEWER_PORT` | 8081 | 轨迹查看器端口 |
| `GRAFANA_ADMIN_USER` | admin | Grafana 管理员用户名 |
| `GRAFANA_ADMIN_PASSWORD` | trajproxy | Grafana 管理员密码 |
| `WORKER_PORT_START` | 12300 | Worker 起始端口 |
| `WORKER_PORT_COUNT` | 10 | Worker 端口数量 |
| `MONITOR_NODES` | localhost | 监控节点 IP（空格分隔） |
| `PROMETHEUS_RETENTION` | 30d | Prometheus 数据保留时间 |
| `PROMETHEUS_RETENTION_SIZE` | 50GB | Prometheus 数据保留空间 |
| `PROMETHEUS_TARGET_HOST` | host.docker.internal | Docker 内访问宿主机的别名 |

---

## 故障排查

### 服务无法启动

```bash
# 检查 Docker 和 Compose 版本
docker --version && docker compose version

# 检查 Docker 守护进程
docker info

# 检查端口占用
lsof -i :19090
lsof -i :3000
lsof -i :9093
```

### Prometheus 抓取不到目标

```bash
# 1. 检查 targets.json 内容
cat dockers/observability/configs/prometheus/targets.json

# 2. 检查 Prometheus 目标状态（API）
curl http://localhost:19090/api/v1/targets

# 3. 常见原因：
#    - targets.json 为空或格式错误 → make sync 重新生成
#    - Worker 未暴露 /metrics 端点 → curl http://localhost:12300/metrics
#    - host.docker.internal 不可达 → Linux 需在 docker-compose.yml 中加 extra_hosts
```

> **Linux 注意**：`host.docker.internal` 默认仅在 Docker Desktop (macOS/Windows) 中可用。
> Linux 环境需确保 docker-compose.yml 中有 `extra_hosts: ["host.docker.internal:host-gateway"]`，
> 或在 Docker daemon 配置中添加 `"host-gateway"` 支持。

### Grafana 看不到数据

```bash
# 1. 检查 Prometheus 数据源
# Grafana → Configuration → Data Sources → 确认 Prometheus 连接正常

# 2. 检查 Prometheus 自身是否有数据
curl http://localhost:19090/api/v1/query?query=up

# 3. 检查 Dashboard 是否加载
# Grafana → Dashboards → Browse → 确认 TrajProxy 文件夹下有 dashboard
```

### 告警未触发

```bash
# 1. 检查告警规则加载状态
curl http://localhost:19090/api/v1/rules | python3 -m json.tool

# 2. 检查 AlertManager 连接
curl http://localhost:9093/api/v2/status

# 3. 常见原因：
#    - prometheus.yml 中 alerting 地址错误
#    - alert_rules.yml 语法错误 → cat 后检查 YAML 格式
#    - 数据量不足（需累积足够数据才触发阈值）
```

### 容器资源不足

```bash
# 查看容器资源使用
docker stats observability-prometheus observability-grafana observability-alertmanager

# 如果 Prometheus 内存超过 2GB 限制被 OOM kill：
# 1. 减少 MONITOR_NODES 节点数
# 2. 增加 scrape_interval（在 prometheus.yml 中）
# 3. 修改 docker-compose.yml 中 prometheus.deploy.resources.limits.memory
```

### 重建 Grafana Dashboard

```bash
# 如果 Dashboard 显示异常或需要恢复默认
cd dockers/observability

# 1. 重新组装
make dashboard

# 2. 重启 Grafana
docker compose restart grafana

# 3. 等待 30s provisioning 重新加载

# 4. 完全重建（删除 Grafana 数据库重建）
make stop
rm -rf data/grafana/grafana.db
make start
```

---

## 数据目录与持久化

```
dockers/observability/data/
├── prometheus/          # TSDB 时序数据库
│   ├── wal/             # Write-Ahead Log
│   └── 01XXX/           # 压缩后的数据块
├── grafana/
│   └── grafana.db       # SQLite 数据库（用户、Dashboard 状态）
└── alertmanager/
    ├── nflog            # 通知日志
    └── silences         # 静默配置
```

- `make stop` 不会删除数据
- 完全重置：`make stop && rm -rf data/* && make start`
- 备份：`tar -czf observability-data-backup.tar.gz data/`
