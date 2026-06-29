# TrajProxy 可观测性系统设计文档

> **状态**: 已实施（v0.3.2）
> **最后更新**: 2026-06-12
> **依赖**: `prometheus-client>=0.20.0`、Prometheus v2.53.0、Grafana 11.1.0、AlertManager v0.27.0

---

## 一、概述

TrajProxy 可观测性系统由两层组成：

1. **应用层**（内置于 TrajProxy）：EventBus 事件总线 + Prometheus 指标采集 + 结构化日志 + 健康检查
2. **基础设施层**（独立 Docker Compose 部署）：Prometheus + Grafana + AlertManager + Trajectory Viewer

两层通过 Prometheus Pull 模式解耦——TrajProxy 暴露 `/metrics` 端点，Prometheus 定时拉取。

**设计原则**：

- **解耦**：观测逻辑通过 EventBus + 装饰器 + 中间件注入，业务代码不感知
- **可禁用**：`observability.disable()` 一行关闭全部观测，业务功能不受影响
- **基数安全**：`model`/`run_id` 标签有严格白名单防护，禁止 `request_id`/`session_id` 作为标签
- **标准兼容**：遵循 Prometheus / JSON 日志等业界标准

---

## 二、部署架构

### 2.1 整体拓扑

```
┌─────────────────────────────────────────────────────────────────────┐
│                        宿主机 (Host)                                 │
│                                                                      │
│  ┌───────────────────────────────────────────────┐                  │
│  │              TrajProxy Workers                  │                  │
│  │  Worker 0 (12300)  ─┬─ /metrics               │                  │
│  │  Worker 1 (12301)  ─┤  /health                │                  │
│  │  Worker 2 (12302)  ─┤  stdout (JSON logs)      │                  │
│  │  ...               ─┘                          │                  │
│  │  Worker N (1230N)                              │                  │
│  └───────────────┬────────────────────────────────┘                  │
│                  │ /metrics (pull, 15s)                              │
│                  │                                                   │
│  ┌───────────────┴──────────────────────────────────────────────┐   │
│  │          Docker Compose (observability-net)                    │   │
│  │                                                                │   │
│  │  ┌──────────────┐    ┌──────────────┐    ┌───────────────┐   │   │
│  │  │  Prometheus   │───>│   Grafana     │    │ AlertManager  │   │   │
│  │  │  :19090       │    │   :3000       │    │ :9093         │   │   │
│  │  │  (2GB, 1CPU)  │    │ (512MB,.5CPU)│    │ (128MB,.2CPU)│   │   │
│  │  │               │    │              │    │               │   │   │
│  │  │  targets.json │    │  provisioning │    │  alert_rules  │   │   │
│  │  │  alert_rules  │    │  dashboards   │    │  receivers    │   │   │
│  │  └──────────────┘    └──────────────┘    └───────────────┘   │   │
│  │         │                                      │              │   │
│  │         │ host.docker.internal                 │              │   │
│  │         v                                      v              │   │
│  │  ┌──────────────┐                        钉钉 / 邮件         │   │
│  │  │  Viewer       │                                             │   │
│  │  │  :8081 (nginx)│                                             │   │
│  │  │  (64MB,.1CPU)│                                             │   │
│  │  └──────────────┘                                             │   │
│  └────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘

数据持久化（bind mount）：
  ./data/prometheus/   → Prometheus TSDB（30d/50GB）
  ./data/grafana/      → Grafana SQLite + 用户配置
  ./data/alertmanager/ → AlertManager 静默/通知日志
```

### 2.2 组件说明

| 组件 | 镜像 | 端口 | 资源限制 | 职责 |
|------|------|------|----------|------|
| **Prometheus** | `prom/prometheus:v2.53.0` | 19090 | 2GB/1CPU | 指标抓取与存储、告警规则评估 |
| **Grafana** | `grafana/grafana:11.1.0` | 3000 | 512MB/0.5CPU | 指标可视化、Dashboard 展示 |
| **AlertManager** | `prom/alertmanager:v0.27.0` | 9093 | 128MB/0.2CPU | 告警分组/路由/抑制/通知 |
| **Trajectory Viewer** | `nginx:alpine` | 8081 | 64MB/0.1CPU | 轨迹回放 HTML 页面（静态） |

### 2.3 网络模型

```
observability-net (bridge)          Host Network
┌────────────────────────┐    ┌──────────────────────┐
│ prometheus             │    │ TrajProxy Workers    │
│   ↓ scrape via         │    │  12300-1230N         │
│   host.docker.internal │───>│  /metrics endpoint    │
│                        │    │                      │
│ grafana                │    │ LiteLLM              │
│   ↓ query via          │    │  :4000 /metrics      │
│   observability-net    │    │                      │
│   → prometheus:19090   │    └──────────────────────┘
│                        │
│ alertmanager           │
│   ← fired from         │
│   prometheus:19090     │
└────────────────────────┘
```

- **Prometheus → Workers**：通过 `host.docker.internal`（Docker Desktop）或 `--add-host` 访问宿主机
- **Grafana → Prometheus**：通过 `observability-net` 内部 DNS（`prometheus:19090`）
- **Prometheus → AlertManager**：通过 `observability-net` 内部 DNS（`alertmanager:9093`）

### 2.4 配置结构

```
dockers/observability/
├── .env                            # 环境变量（端口、节点、Grafana 凭证）
├── .env.example                    # 环境变量模板（提交仓库）
├── Makefile                        # 快捷操作入口
├── docker-compose.yml              # 4 服务定义
├── configs/
│   ├── prometheus/
│   │   ├── prometheus.yml          # 抓取配置（scrape_configs）
│   │   ├── alert_rules.yml         # 7 条告警规则
│   │   └── targets.json            # 动态生成（file_sd）
│   ├── grafana/
│   │   ├── provisioning/
│   │   │   ├── datasources/
│   │   │   │   └── prometheus.yml  # 自动注册 Prometheus 数据源
│   │   │   └── dashboards/
│   │   │       └── dashboards.yml  # Dashboard 文件扫描配置
│   │   ├── dashboards/
│   │   │   └── trajproxy-overview.json  # 组装后的完整 Dashboard
│   │   └── dashboard-src/          # 片段源文件（不在 Grafana 扫描路径内）
│   │       ├── build_dashboard.py  # 片段组装脚本
│   │       ├── dashboard.json      # Dashboard 元数据壳
│   │       └── row-{0..4}-*.json   # 5 个 Row 面板定义
│   └── alertmanager/
│       └── alertmanager.yml        # 路由与接收器配置
└── data/                           # 运行时数据（bind mount）
    ├── prometheus/
    ├── grafana/
    └── alertmanager/
```

**关键约束**：`dashboard-src/` 是构建输入目录，**不可**放在 Grafana provisioning 扫描路径下。
Grafana 会递归扫描被挂载目录的所有子目录，如果源文件和构建产物同处一个被扫描目录，
同 `uid` 的 dashboard 会导致重复加载冲突。

---

## 三、核心数据流

### 3.1 指标采集流程

```
请求到达 → HTTP Middleware
    │
    ├── 设置 request_id (ContextVar)
    ├── 记录 start_time
    │
    ▼
Routes 层 (Layer 1.5)
    ├── semaphore.acquire() → emit(SEMAPHORE_ACQUIRED) / emit(CONCURRENCY_REJECTED)
    │
    ▼
Processor (Layer 2)
    ├── emit(REQUEST_STARTED) → ACTIVE_REQUESTS.inc()
    ├── Pipeline 处理请求
    │     └── Pipeline 装饰器 (Layer 5)
    │           └── 轨迹存储失败 → emit(TRAJECTORY_STORE_ERROR)
    ├── emit(REQUEST_COMPLETED, context, exception)
    │     └── MetricsCollector 订阅 → 确定 outcome → 更新 Counter/Histogram/Gauge
    │     └── RequestSummary 订阅 → 输出结构化摘要日志
    │
    ▼
InferClient (Layer 3, @observe_inference)
    └── emit(INFERENCE_COMPLETED) → INFER_DURATION / ERRORS / RETRIES

ProcessorManager (Layer 4, @observe_model_lifecycle)
    └── emit(MODEL_LIFECYCLE) → MODEL_LIFECYCLE counter
```

**Prometheus 每 15s 拉取各 Worker 的 `/metrics` 端点**，获取所有 Counter/Histogram/Gauge 的当前值。

### 3.2 告警流程

```
Prometheus (每 15s 评估 alert_rules.yml)
    │
    ├── 规则触发 → Firing
    │     │
    │     ▼
    │   AlertManager
    │     ├── group_by [alertname, severity]
    │     ├── group_wait: 30s（聚合窗口）
    │     ├── route:
    │     │     critical → "critical" receiver (1h repeat)
    │     │     warning  → "default" receiver  (4h repeat)
    │     └── inhibit: 同一 alertname 的 critical 抑制 warning
    │
    ▼
  通知渠道：钉钉 Webhook / 邮件（按需启用）
```

### 3.3 日志流程

```
TrajProxy Worker stdout
    │
    ├── LOG_FORMAT=text  → 人类可读文本日志
    └── LOG_FORMAT=json  → JSON 结构化日志（可被 Loki/Vector 消费）
          │
          字段: {timestamp, level, module, worker_id, message,
                 request_id?, summary?, exception?}
```

---

## 四、应用层架构

### 4.1 EventBus 事件总线

进程内同步事件总线，< 50 行实现。业务代码通过 `emit()` 发射事件，观测模块通过 `subscribe()` 消费。

```python
# 核心 API
event_bus.emit(event_name, **kwargs)       # 触发事件（失败隔离）
event_bus.subscribe(event_name, handler)   # 注册订阅者
event_bus.enable() / disable() / reset()   # 全局控制
```

**多进程约束**：EventBus 状态为进程级，`setup()` 必须在每个 Worker 进程中单独调用。

### 4.2 事件类型（8 个）

| 事件 | 触发层 | kwargs |
|------|--------|--------|
| `request.started` | Processor | `model, is_stream, max_concurrent` |
| `request.completed` | Processor | `context, exception` |
| `inference.completed` | InferClient 装饰器 | `model, duration_ms, retry_count, error, error_type` |
| `model.lifecycle` | ProcessorManager 装饰器 | `action, model, run_id, model_type` |
| `concurrency.rejected` | Routes | `model, run_id, wait_duration_ms` |
| `semaphore.acquired` | Routes | `wait_duration_ms, model` |
| `trajectory.store_error` | Pipeline | `model, error_type, error_message` |
| `stream.client_disconnect` | StreamingResponse | `model, chunk_count, duration_ms` |

### 4.3 Outcome 判定（纯函数）

`outcome.py` 从 ProcessContext 状态 + 异常类型推导请求结果，**业务代码完全不感知**。

| outcome | 触发条件 |
|---------|---------|
| `success` | 非流式正常完成 / 流式完整返回 |
| `stream_partial` | 流式中途断连（有 chunk 但未 finish） |
| `error_client` | 异常 status_code 4xx |
| `error_server` | TrajProxy 内部异常 5xx |
| `error_infer` | InferServiceError（502/连接失败） |
| `error_rate_limit` | 并发限流 429（由 `concurrency.rejected` 事件独立记录） |
| `timeout` | InferTimeoutError（504） |

> **注意**：`error_rate_limit` 仅在 Routes 层（`EVENT_CONCURRENCY_REJECTED`）触发，
> 不经过 Processor 层。这是因为 `HTTPException(429)` 在 `routes.py` 的 semaphore 超时时直接抛出，
> 不会进入 Processor 的 emit 路径。

### 4.4 五层拦截架构

```
Layer 1: Middleware       → HTTP 级：request_id, 耗时, 状态码
Layer 1.5: Routes         → 429 限流 / 信号量等待（不过 Processor）
Layer 2: Processor        → 请求级：ProcessContext 全状态
Layer 3: @observe_inference → 推理级：调用耗时, 错误, 重试
Layer 4: @observe_model_lifecycle → 模型注册/注销
Layer 5: Pipeline         → 轨迹存储失败 / 流式断连

所有层通过 EventBus → 订阅者 统一消费
```

### 4.5 模块文件结构

```
traj_proxy/observability/
├── __init__.py              # setup() / teardown() / disable()
├── event_bus.py             # 轻量事件总线
├── events.py                # 事件类型常量
├── outcome.py               # Outcome 纯函数判定
├── label_guards.py          # 基数防护（model/run_id 白名单）
├── decorators.py            # @observe_inference, @observe_model_lifecycle
├── metrics_collector.py     # Prometheus 指标定义 + EventBus 订阅者
├── request_summary.py       # 结构化摘要日志订阅者
├── request_context.py       # ContextVar request_id 传播
├── json_formatter.py        # JSON 日志格式化器
└── health_checker.py        # 增强健康检查（深度/浅度）
```

---

## 五、指标体系

### 5.1 指标清单

#### A. 请求核心（4 个）

| 指标名 | 类型 | Label | 用途 |
|--------|------|-------|------|
| `trajproxy_requests_total` | Counter | `method, model, stream, outcome, run_id` | 请求计数 |
| `trajproxy_request_duration_seconds` | Histogram | `model, stream, run_id` | 端到端延迟 |
| `trajproxy_active_requests` | Gauge | — | 当前活跃请求 |
| `trajproxy_concurrency_utilization` | Gauge | — | 并发使用率 |

#### B. 并发控制（2 个）

| 指标名 | 类型 | Label |
|--------|------|-------|
| `trajproxy_semaphore_wait_seconds` | Histogram | `model` |
| `trajproxy_semaphore_rejected_total` | Counter | `model` |

#### C. 流式（2 个核心 + 1 个可选）

| 指标名 | 类型 | Label |
|--------|------|-------|
| `trajproxy_ttft_seconds` | Histogram | `model, run_id` |
| `trajproxy_stream_completion_total` | Counter | `model, run_id, result` |
| `trajproxy_stream_client_disconnect_total` | Counter | `model` |

#### D. 阶段（1 个）

| 指标名 | 类型 | Label |
|--------|------|-------|
| `trajproxy_phase_duration_seconds` | Histogram | `model, phase` |

`phase` 枚举：`transform` / `encode` / `cache_db_query` / `cache_prefix_match` / `inference` / `decode`

#### E. Token（1 个）

| 指标名 | 类型 | Label |
|--------|------|-------|
| `trajproxy_tokens_total` | Counter | `model, run_id, type` |

#### F. 下游依赖（5 个）

| 指标名 | 类型 | Label |
|--------|------|-------|
| `trajproxy_infer_duration_seconds` | Histogram | `model` |
| `trajproxy_infer_errors_total` | Counter | `model, error_type` |
| `trajproxy_infer_retries_total` | Counter | `model, outcome` |
| `trajproxy_db_pool_usage` | Gauge | `state` |
| `trajproxy_trajectory_store_errors_total` | Counter | `model, error_type` |

#### G. 进程级（ProcessCollector 自动暴露，4 个）

| 指标名 | 来源 |
|--------|------|
| `process_cpu_seconds_total` | prometheus-client 内置 |
| `process_resident_memory_bytes` | prometheus-client 内置 |
| `process_open_fds` | prometheus-client 内置 |
| `process_start_time_seconds` | prometheus-client 内置 |

#### 合计

| 类别 | Counter | Histogram | Gauge | 小计 |
|------|---------|-----------|-------|------|
| 核心 | 6 | 4 | 3 | 13 |
| 进程级 | 1 | 0 | 3 | 4 |
| **合计** | **7** | **4** | **6** | **17** |

### 5.2 基数防护

| label | 允许值 | 上限策略 |
|-------|--------|---------|
| `model` | ProcessorManager 已注册 model_name | 未知归 `"unknown"` |
| `run_id` | 已注册 run_id | 超 100 归 `"other"` |
| `method` | `POST`, `GET`, `DELETE`, `OTHER` | ≥ 4 |
| `outcome` | 7 种归因枚举 | 固定 |
| `phase` | 6 个处理阶段 | 固定 |

**禁止**：`request_id`、`session_id`、原始 `error_message` 作为 label。

### 5.3 内存影响

按 50 模型 × 10 run_id 规模，约 14,000 时间序列，每 Worker 增量 < 10MB。进程重启清零。

---

## 六、告警规则

共 7 条规则，分为 **critical** 和 **warning** 两个等级：

| # | 规则名 | 严重度 | 表达式摘要 | 持续时间 |
|---|--------|--------|-----------|---------|
| 1 | `HighErrorRate` | warning | 错误率 > 5% | 5m |
| 2 | `HighLatency` | warning | P95 延迟 > 60s | 5m |
| 3 | `InferErrors` | **critical** | 推理错误率 > 0.5/s | 3m |
| 4 | `InferRetriesHigh` | warning | 重试频率 > 1/s | 5m |
| 5 | `DBPoolSaturation` | warning | 连接池使用率 > 85% | 5m |
| 6 | `WorkerDown` | **critical** | `up{job="trajproxy"} == 0` | 1m |
| 7 | `WorkerHighMemory` | warning | RSS > 2GB | 10m |

**路由策略**：
- `critical` → `critical` receiver，每 1h 重复
- `warning` → `default` receiver，每 4h 重复
- 同一 alertname 的 critical 抑制 warning

配置文件：`dockers/observability/configs/prometheus/alert_rules.yml`

---

## 七、Grafana Dashboard

### 7.1 Dashboard 架构

预置 Dashboard（UID: `trajproxy-overview`）按 5 个 Row 布局：

| Row | 名称 | 面板数 | 核心视图 |
|-----|------|--------|---------|
| 0 | Global Overview | stat + piechart | QPS、成功率、错误率、活跃请求 |
| 1 | Traffic Overview | timeseries + heatmap | 请求速率、响应码分布、延迟趋势 |
| 2 | Inference Requests | timeseries + stat | 推理延迟、错误率、重试、Token 用量 |
| 3 | Trajectory Requests | timeseries | 轨迹存储操作、存储错误 |
| 4 | System Resources | timeseries + stat | CPU、内存 RSS、文件描述符、DB 连接池 |

Dashboard 模板变量：`$datasource`、`$node`、`$model`。自动刷新间隔 30s。

### 7.2 片段化构建系统

Dashboard 采用源文件片段 + 构建脚本的方式管理，避免维护单个大型 JSON：

```
dashboard-src/           ← 构建输入（不在 Grafana 扫描路径下）
├── dashboard.json       ← 元数据壳（uid、title、变量定义）
├── row-0-overview.json  ← Row 0 面板
├── row-1-traffic.json   ← Row 1 面板
├── row-2-inference.json ← Row 2 面板（最大）
├── row-3-trajectory.json← Row 3 面板
├── row-4-system.json    ← Row 4 面板
└── build_dashboard.py   ← 组装脚本

dashboards/              ← 构建输出（Grafana provision 扫描此目录）
└── trajproxy-overview.json
```

构建命令：`make dashboard`（在 `dockers/observability/` 下执行）

**约束**：修改片段文件后必须执行 `make dashboard`，并重启 Grafana 容器以加载新版本。

### 7.3 Grafana Provisioning

**数据源**（自动注册）：

```yaml
# provisioning/datasources/prometheus.yml
datasources:
  - name: Prometheus
    type: prometheus
    url: http://prometheus:19090
    isDefault: true
    editable: false
```

**Dashboard**（自动扫描）：

```yaml
# provisioning/dashboards/dashboards.yml
providers:
  - name: trajproxy
    type: file
    folder: TrajProxy
    updateIntervalSeconds: 30
    allowUiUpdates: true
    options:
      path: /var/lib/grafana/dashboards
```

---

## 八、应用层配置

### 8.1 config.yaml

```yaml
observability:
  enabled: true              # 全局开关
  log_format: text           # text | json
  metrics_enabled: true      # Prometheus /metrics 端点
  health_deep_check: false   # 深度健康检查（检查推理服务）
```

### 8.2 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OBSERVABILITY_ENABLED` | `true` | `false` 时 setup() 直接跳过 |
| `LOG_FORMAT` | `text` | `json` 启用结构化日志 |

### 8.3 健康检查端点

| 端点 | 说明 |
|------|------|
| `GET /health` | 浅度检查（进程存活） |
| `GET /health?deep=1` | 深度检查（DB 连接 + 各推理服务 by base_url 去重） |
| `GET /metrics` | Prometheus 指标抓取 |

---

## 九、边界与局限

### 9.1 不做的事

| 方案 | 原因 |
|------|------|
| OpenTelemetry | 单服务代理链路短，Prometheus + 摘要日志足够 |
| ELK/Loki 集成代码 | JSON 日志即可被任意聚合系统消费 |
| 实时告警 | 由 AlertManager / Grafana 端处理 |
| 跨 Worker 指标聚合 | 各 Worker 独立暴露，Prometheus 服务端聚合 |
| 请求体/响应体日志 | 安全规则禁止 |

### 9.2 定位能力边界

**能定位**：推理服务故障、DB 连接池瓶颈、Pipeline 阶段耗时异常、并发限流、推理重试频繁、特定 run_id 错误率

**不能直接定位**：具体 SQL 慢查询、推理服务内部排队、Tokenizer CPU 热点、单请求调用链追踪

缓解方式：通过 `request_id` grep 摘要日志，或使用 py-spy/pyroscope 等工具。

---

## 十、关键数字汇总

| 维度 | 数值 |
|------|------|
| Prometheus 核心指标 | 17 个（含 ProcessCollector） |
| 告警规则 | 7 条（2 critical + 5 warning） |
| Outcome 归因类型 | 7 种 |
| 事件类型 | 8 种 |
| 观测模块文件 | 11 个 |
| 业务代码侵入 | ~41 行 / 7 文件 |
| Pipeline 层修改 | +4 行（轨迹存储 emit） |
| 每 Worker 内存增量 | < 10MB（~14000 序列） |
| 新增依赖 | 1 个（prometheus-client） |
| Grafana 面板 | 5 Row ~30 面板 |
| Dashboard 源文件片段 | 5 个 row-*.json + 1 个壳 |
