# Grafana Dashboard 使用指南

> 本文档详细说明 TrajProxy 可观测性系统中 Grafana Dashboard 的面板结构、各图表含义及日常运维中的使用方法。

---

## 目录

- [概述](#概述)
- [Dashboard 构建机制](#dashboard-构建机制)
- [全局模板变量](#全局模板变量)
- [Dashboard 1: TrajProxy Overview](#dashboard-1-trajproxy-overview)
  - [Row 0 — 全局总览](#row-0--全局总览)
  - [Row 1 — 流量概览](#row-1--流量概览)
  - [Row 2 — 推理请求](#row-2--推理请求)
  - [Row 2b — TITO 缓存监控](#row-2b--tito-缓存监控)
  - [Row 3 — 轨迹请求](#row-3--轨迹请求)
  - [Row 4 — 系统资源](#row-4--系统资源)
- [Dashboard 2: TrajProxy Model Mix](#dashboard-2-trajproxy-model-mix)
  - [概览行](#概览行)
  - [模型使用统计行](#模型使用统计行)
  - [Token 统计行](#token-统计行)
  - [系统状态行](#系统状态行)
- [告警规则](#告警规则)
- [常见查询模式](#常见查询模式)

---

## 概述

TrajProxy 配备了 **两个 Grafana Dashboard**，从不同视角覆盖服务的运行状态：

| Dashboard | UID | 定位 | 适用场景 |
|-----------|-----|------|----------|
| **TrajProxy Overview** | `trajproxy-overview` | 全景运维监控 | 日常巡检、故障定位、容量规划 |
| **TrajProxy Model Mix** | `trajproxy-model-mix` | 模型使用分析 | 模型流量分配、Token 消耗统计、Pipeline 性能对比 |

**数据流**: 应用代码（EventBus 事件） → Prometheus 指标采集 → Grafana 可视化

**指标前缀约定**:
- `trajproxy_*` — TrajProxy Overview Dashboard 使用的核心指标
- `traj_proxy_*` — Model Mix Dashboard 使用的历史指标（早期命名，仅部分指标仍在使用）
- `process_*` / `up` — Prometheus 自动暴露的进程级指标

---

## Dashboard 构建机制

Overview Dashboard 采用**分片构建**模式，源文件拆分维护以降低大 JSON 的编辑成本：

```
dockers/observability/configs/grafana/
├── dashboard-src/                    # 源文件目录（不参与 provisioning 扫描）
│   ├── dashboard.json                # Shell: 定义元数据、模板变量（panels 为空数组）
│   ├── row-0-overview.json           # Row 0 面板片段
│   ├── row-1-traffic.json            # Row 1 面板片段
│   ├── row-2-inference.json          # Row 2 面板片段
│   ├── row-2b-cache.json             # Row 2b 面板片段
│   ├── row-3-trajectory.json         # Row 3 面板片段
│   ├── row-4-system.json             # Row 4 面板片段
│   └── build_dashboard.py            # 组装脚本
└── dashboards/                       # Grafana provisioning 扫描目录（仅放构建产物）
    └── trajproxy-overview.json       # 最终组装产物
```

**构建命令**:
```bash
make dashboard
# 或
python3 dockers/observability/configs/grafana/dashboard-src/build_dashboard.py
```

脚本按文件名排序（`row-0-*` 在前，`row-4-*` 在后）合并所有面板片段到 `dashboard.json` 的 `panels` 数组中，输出到 Grafana provisioning 目录。

> **重要**: Grafana provisioning 会**递归扫描** `dashboards/` 目录下的所有子目录。严禁在此目录下放置源文件、片段或构建中间产物，否则可能导致同 UID dashboard 重复加载、互相覆盖。

---

## 全局模板变量

Overview Dashboard 顶部提供 6 个下拉筛选器，影响所有面板的数据展示范围：

| 变量 | 标签 | 类型 | 说明 |
|------|------|------|------|
| `$datasource` | 数据源 | 单选 | 选择 Prometheus 数据源实例 |
| `$node` | 节点 | 多选（含 All） | 按节点名称过滤，`All` 时匹配所有节点 |
| `$model` | 模型 | 多选（含 All） | 按推理模型名过滤 |
| `$run_id` | Run ID | 多选（含 All） | 按作业运行标识过滤 |
| `$instance` | 实例 | 多选（含 All） | 按 Prometheus scrape target（`host:port`）过滤 |
| `$quantile` | 分位数 | 单选 | 耗时面板使用的分位数：**P50** / **P95**（默认）/ **P99** |

**使用技巧**:
- 排查单个模型问题时，在 `$model` 下拉框选择目标模型，所有推理相关面板自动聚焦
- 排查特定任务的错误分布时，在 `$run_id` 选择具体标识，配合错误面板定位问题
- 调整 `$quantile` 观察不同分位下的延迟分布：P50 反映中位体验，P95 反映尾部延迟，P99 反映极端情况

---

## Dashboard 1: TrajProxy Overview

这是 TrajProxy 的**主 Dashboard**，按功能域组织为 5 个 Row，共 **33 个面板**。从上到下逐层展开：全局概览 → 流量分类 → 推理详情 → 轨迹查询 → 系统资源。

---

### Row 0 — 全局总览

> 一眼看全：请求流量构成、总量趋势、耗时表现、模型生命周期。

#### 面板 1: 全站请求类型占比 (1h)

| 属性 | 值 |
|------|-----|
| 类型 | 环形饼图 (Pie Chart) |
| 时间窗口 | 最近 1 小时 |
| 指标 | `trajproxy_api_requests_total` |
| 分组维度 | `route`（请求类型） |

**含义**: 展示各 API 端点类型在过去 1 小时的请求占比。`route` 标签由 `api_metrics_middleware` 自动分类，常见值包括：

- `chat_completions` — 推理请求
- `model_register` / `model_list` / `model_delete` — 模型管理 API
- `trajectory_list` / `trajectory_detail` — 轨迹查询 API
- `health` — 健康检查

**用途**: 快速识别流量构成变化。如果 `trajectory_*` 占比突然升高，可能有用户在批量查询历史数据。

---

#### 面板 2: 全站请求数 (5min) - 按请求类型

| 属性 | 值 |
|------|-----|
| 类型 | 时序折线图 (Time Series) |
| 时间窗口 | 每 5 分钟增量 |
| 指标 | `trajproxy_api_requests_total` |
| 分组维度 | `route` |

**含义**: 各 API 类型每 5 分钟的请求数量趋势。底部图例显示均值 (mean) 和峰值 (max)。

**用途**: 观察流量随时间的变化趋势，发现流量尖峰或异常下跌。不同 `route` 使用独立线条，便于区分推理流量 vs 管理流量。

---

#### 面板 3: 全站请求数 (5min) - 按节点

| 属性 | 值 |
|------|-----|
| 类型 | 时序折线图 |
| 时间窗口 | 每 5 分钟增量 |
| 指标 | `trajproxy_api_requests_total` |
| 分组维度 | `node` |

**含义**: 各节点承担的请求量趋势。在多节点部署场景下，可用于观察负载均衡是否均匀。

**用途**: 某个节点流量明显偏高时，需要检查 Nginx  upstream 权重配置或节点健康状态。

---

#### 面板 4: 全站请求耗时 - 按请求类型

| 属性 | 值 |
|------|-----|
| 类型 | 时序折线图 |
| 时间窗口 | 每 5 分钟增量 |
| 指标 | `trajproxy_api_request_duration_seconds_bucket` |
| 分组维度 | `route` |
| 受 `$quantile` 控制 | 是 |

**含义**: 各请求类型端到端耗时的分位数趋势。标题中动态显示当前选择的分位数（如 `P95`）。

**用途**: 对比不同 API 的响应速度。如果 `trajectory_detail` 耗时持续走高，可能是大轨迹数据查询引发性能问题。

---

#### 面板 5: 模型生命周期事件

| 属性 | 值 |
|------|-----|
| 类型 | 柱状图 (Bars) |
| 时间窗口 | 每 5 分钟增量 |
| 指标 | `trajproxy_model_lifecycle_total` |
| 分组维度 | `action` × `type` |

**含义**: 模型注册 (`register`) 和注销 (`unregister`) 事件计数。每次有新的模型 Processor 被加载或淘汰时触发。

**用途**: 频繁出现 `register/unregister` 可能意味着 Processor LRU 缓存容量不足（`processor_cache_max_size`），导致模型反复加载/淘汰。

---

### Row 1 — 流量概览

> 分三类概览：推理请求、模型管理、轨迹查询，每类展示请求量 + 成功率 + 平均耗时。

三个面板结构一致，采用 **Stat 面板**（数值 + 迷你趋势图），使用颜色阈值直观标识健康状态。

#### 面板 6: 推理请求概览

| 子指标 | 窗口 | 阈值 |
|--------|------|------|
| 请求数 (5min) | 5 分钟增量 | — |
| 成功率 (1h) | 1 小时滚动 | < 90% 红色 / 90~99% 黄色 / ≥ 99% 绿色 |
| 平均耗时 (1h) | 1 小时滚动 | < 10s 绿色 / 10~30s 黄色 / > 30s 红色 |

**含义**: 推理服务的综合健康看板。成功率基于 `outcome="success"` 占总请求的比例。

**用途**: **首选巡检面板**。成功率变红或耗时变黄即需深入排查 Row 2 推理详情。

---

#### 面板 7: 模型请求概览

| 子指标 | 窗口 | 耗时阈值 |
|--------|------|----------|
| 请求数 (5min) | 5 分钟增量 | — |
| 成功率 (1h) | 1 小时滚动 | 同上 |
| 平均耗时 (1h) | 1 小时滚动 | < 1s 绿色 / 1~5s 黄色 / > 5s 红色 |

**含义**: 模型管理 API（注册/删除/列表）的执行状况。

**用途**: 模型注册操作通常瞬间完成，耗时超过 1s 需关注 DB 查询性能或 Tokenizer 加载瓶颈。

---

#### 面板 8: 轨迹请求概览

| 子指标 | 窗口 | 耗时阈值 |
|--------|------|----------|
| 请求数 (5min) | 5 分钟增量 | — |
| 成功率 (1h) | 1 小时滚动 | 同上 |
| 平均耗时 (1h) | 1 小时滚动 | < 1s 绿色 / 1~5s 黄色 / > 5s 红色 |

**含义**: 轨迹查询 API（列表 + 详情）的执行状况。

**用途**: 轨迹查询涉及大量 DB 数据和 JSON 序列化，耗时阈值相对宽松。如果持续偏高，参考 Row 3 排查数据库性能。

---

### Row 2 — 推理请求

> 深入推理细节：流量分布、结果分类、重试监控、耗时分析、流式/模式占比、失败详情。

本行按逻辑分为**两排**：
- 上排（左→右）：请求量分布 → 结果分类 → 重试 → Pipeline 耗时 → 流式/模式占比
- 下排（左→右）：端到端耗时 → 推理服务耗时 → TTFT → 序列长度 → 缓存命中率
- 第三排：失败分析（按节点 / 按 run_id / 错误细分）

#### 面板 9: 推理请求数 (5min) - 按运行标识

| 属性 | 值 |
|------|-----|
| 类型 | 时序折线图 |
| 指标 | `trajproxy_requests_total` |
| 分组维度 | `run_id` |

**含义**: 每个 `run_id`（作业标识）的推理请求量趋势。多色线条叠加展示不同作业的流量分布。

**用途**: 识别哪个作业产生了最多流量，辅助容量规划和问题归因。

---

#### 面板 10: 推理请求数 (5min) - 按结果

| 属性 | 值 |
|------|-----|
| 类型 | 时序折线图 |
| 指标 | `trajproxy_requests_total` |
| 分组维度 | `outcome` |

**含义**: 推理请求按结果分类的数量趋势。7 种 Outcome 各有中文标签和颜色：

| Outcome | 中文标签 | 颜色 | 说明 |
|---------|---------|------|------|
| `success` | 请求成功 (Success) | 绿色 | 请求完成并成功 |
| `stream_partial` | 流式传输部分完成 (Stream Partial) | 浅绿 | 流式传输未完整结束（如客户端断连） |
| `error_client` | 客户端-请求不合法 (4xx Client Error) | 橙色 | 客户端发送了无效请求 |
| `error_server` | 内部-服务端错误 (5xx Server Error) | 深红 | Proxy 内部异常 |
| `error_rate_limit` | 推理-请求被限流 (429 Too Many Requests) | 紫色 | 请求被并发信号量拒绝 |
| `error_infer` | 推理-服务异常（连接 / HTTP 错误） | 蓝色 | 下游推理服务返回异常 |
| `timeout` | 推理-服务超时 (Timeout) | 红色 | 推理请求超时 |

**用途**: 最关键的错误诊断面板之一。大面积出现非 `success` 结果时，根据颜色快速判断错误类型：
- 橙色条 = 客户端问题（检查请求格式）
- 蓝色/红色条 = 下游推理问题（检查推理服务状态）
- 紫色条 = 自身限流（检查并发配置）

---

#### 面板 11: 推理内部重试次数 (5min) - 按节点

| 属性 | 值 |
|------|-----|
| 类型 | 堆叠柱状图 (Stacked Bars) |
| 指标 | `trajproxy_infer_retries_total` |
| 分组维度 | `node` |

**含义**: 下游推理服务返回 502/503/504 触发的自动重试计数。由 InferClient 的指数退避重试机制产生。

**用途**: 重试频繁说明推理服务不稳定或瞬时过载。配合 AlertManager 的 `InferRetriesHigh` 告警使用。

---

#### 面板 12: 推理 Pipeline 各阶段耗时

| 属性 | 值 |
|------|-----|
| 类型 | 时序折线图 |
| 指标 | `trajproxy_phase_duration_seconds_bucket` |
| 固定分位 | P95 |
| 分组维度 | `phase` |

**含义**: Pipeline 各阶段（`convert` / `inference` / `store` 等）P95 耗时的分解视图。用于识别瓶颈所在阶段。

**用途**:
- `inference` 阶段耗时高 → 推理服务响应慢
- `convert` 阶段耗时高 → Token 转换或 Jinja2 模板渲染性能问题
- `store` 阶段耗时高 → 数据库写入性能问题

---

#### 面板 13: 推理流式与非流式占比 (1h)

| 属性 | 值 |
|------|-----|
| 类型 | 环形饼图 |
| 指标 | `trajproxy_requests_total` |
| 分组维度 | `stream` |

**含义**: 最近 1 小时内流式请求 vs 非流式请求的占比。

**用途**: 流量模式评估。流式请求占比高时 TTFT 指标更值得关注。

---

#### 面板 14: DIRECT vs TITO 模式占比 (1h)

| 属性 | 值 |
|------|-----|
| 类型 | 环形饼图 |
| 指标 | `trajproxy_requests_total` |
| 分组维度 | `pipeline_mode` |

**含义**: 请求经过 DirectPipeline（直接转发）vs TokenPipeline（TITO 模式，含 Token 编解码）的占比。

**用途**: 评估两种 Pipeline 的使用情况。TITO 模式涉及额外 Token 转换开销，比例高时需关注 Row 2b 缓存命中率。

---

#### 面板 15: 推理请求端到端耗时 - 按运行标识

| 属性 | 值 |
|------|-----|
| 类型 | 时序折线图 |
| 指标 | `trajproxy_request_duration_seconds_bucket` |
| 分组维度 | `run_id` |
| 受 `$quantile` 控制 | 是 |

**含义**: 从请求到达到响应返回的完整端到端耗时（含全 Pipeline 各阶段）。

**用途**: 反映用户真实体验。按 `run_id` 拆分可在多租户场景下对比各服务的延迟差异。

---

#### 面板 16: 推理服务调用耗时 - 按运行标识

| 属性 | 值 |
|------|-----|
| 类型 | 时序折线图 |
| 指标 | `trajproxy_phase_duration_seconds_bucket` (phase=inference) |
| 分组维度 | `run_id` |
| 受 `$quantile` 控制 | 是 |

**含义**: 单次调用下游推理服务的 HTTP 延迟。仅统计 `phase=inference`，不含 Proxy 自身的转换和存储耗时。

**用途**: 与面板 15 对比可判断延迟瓶颈在 Proxy 层还是推理服务层。如果两者差距大，说明 Proxy 内部处理（convert/store）占用了较多时间。

---

#### 面板 17: 推理首 Token 耗时 (TTFT) - 按运行标识

| 属性 | 值 |
|------|-----|
| 类型 | 时序折线图 |
| 指标 | `trajproxy_ttft_seconds_bucket` |
| 分组维度 | `run_id` |
| 受 `$quantile` 控制 | 是 |

**含义**: Time To First Token — 流式请求中从发出请求到收到第一个 Token 的耗时。仅流式请求有数据。

**用途**: 衡量用户对推理「响应感」的体验。TTFT 高通常意味着推理服务 prefill 阶段耗时长（可能受 prompt 长度影响）。

---

#### 面板 18: 推理序列长度（输入+输出） - 按运行标识

| 属性 | 值 |
|------|-----|
| 类型 | 时序折线图 |
| 指标 | `trajproxy_sequence_length_tokens_bucket` |
| 分组维度 | `run_id` |
| 受 `$quantile` 控制 | 是 |

**含义**: 每请求的总 Token 数（prompt + completion），即序列长度。

**用途**: 评估请求规模和推理服务负载。序列越长，推理耗时和内存消耗越大。

---

#### 面板 19-21: 失败分析三联面板

| 面板 | 标题 | 分组维度 |
|------|------|----------|
| 19 | 推理请求失败数量 - 按节点 (5min) | `node` |
| 20 | 推理请求失败数量 - 按运行标识 (5min) | `run_id` |
| 21 | 推理请求错误细分（请选择 Run ID） | `outcome` |

**面板 19-20 结构**（双 Y 轴）:
- **左轴**: 请求级失败总数（`outcome` 为 `error_infer|timeout|stream_partial|error_server` 的请求）
- **右轴**: 轨迹写入 DB 失败数（`trajproxy_trajectory_store_errors_total`）

**面板 21 结构**（堆叠柱状图）:
- 按 `outcome` 拆分所有错误类型，使用中文 displayName（参照面板 10 的颜色/标签表）
- 需在顶部 `$run_id` 下拉框选择具体标识，选 All 时为所有标识聚合

**用途**: 
1. 先看面板 19 判断哪个节点失败多
2. 再看面板 20 定位到具体 `run_id`
3. 最后面板 21 查看该 `run_id` 的错误类型分布，确认是超时还是服务异常

---

### Row 2b — TITO 缓存监控

> 单独一个面板，监控 TITO 模式的前缀缓存效果。

#### 面板 22: TITO模式缓存命中率 - 按运行标识（1h）

| 属性 | 值 |
|------|-----|
| 类型 | 时序折线图 |
| 指标 | `trajproxy_cache_lookups_total` |
| 分组维度 | `run_id` |
| 时间窗口 | 1 小时滚动 |

**含义**: 前缀缓存**近 1 小时**的命中率百分比 = `hits / (hits + misses) × 100`。使用 `increase(...[1h])` 计算滚动窗口增量，**不受进程重启影响**。仅 TITO 模式有数据（DirectPipeline 不使用缓存）。

**用途**: 衡量 TITO 模式的前缀缓存效果。命中率低于 50% 说明多轮对话间共享前缀较少，或缓存配置需要调整。命中率持续为 0 说明每次请求前缀完全不同或 TITO 模式未被使用。

---

### Row 3 — 轨迹请求

> 轨迹查询 API 的详细监控：请求量、耗时、数据规模、失败分析。

#### 面板 23: 轨迹详情请求数 (5min) - 按运行标识

| 属性 | 值 |
|------|-----|
| 类型 | 时序柱状图 (Bars) |
| 指标 | `trajproxy_api_requests_total` (route=trajectory_detail) |
| 分组维度 | `run_id` |

**含义**: 轨迹详情 API 的请求量分布。用于观察不同 `run_id` 的轨迹查询活跃度。

---

#### 面板 24: 轨迹详情请求端到端耗时 - 按运行标识

| 属性 | 值 |
|------|-----|
| 类型 | 时序折线图 |
| 指标 | `trajproxy_api_request_duration_seconds_bucket` (route=trajectory_detail) |
| 受 `$quantile` 控制 | 是 |

**含义**: 轨迹详情查询的端到端耗时（含 DB 查询 + JSON 序列化 + 网络传输）。

**用途**: 耗时异常高时，结合面板 27（对话数量）和面板 28（响应大小）判断是否因数据量过大引起。

---

#### 面板 25: 轨迹平均对话数量 - 按运行标识

| 属性 | 值 |
|------|-----|
| 类型 | 时序折线图 |
| 指标 | `trajproxy_trajectory_record_count` |

**含义**: 每次轨迹查询返回的 dialog records 条数平均值。

**用途**: 记录数越多，查询和序列化的成本越高。可用于评估是否需要启用 `fields` 过滤参数减少响应体积。

---

#### 面板 26: 轨迹请求平均大小 - 按运行标识

| 属性 | 值 |
|------|-----|
| 类型 | 时序折线图 |
| 指标 | `trajproxy_trajectory_response_size_bytes` |
| 单位 | bytes（自动转换 KB/MB/GB） |

**含义**: 轨迹查询响应的平均字节数。

**用途**: 当响应体过大（> 10MB）时，可能导致序列化和传输超时。配合 API 层的 `fields` 过滤参数可降低响应体积。

---

#### 面板 27-29: 轨迹失败分析三联面板

| 面板 | 标题 | 分组维度 |
|------|------|----------|
| 27 | 轨迹请求失败数量 - 按节点 (5min) | `node` |
| 28 | 轨迹请求失败数量 - 按运行标识 (5min) | `run_id` |
| 29 | 轨迹请求错误细分（请选择 Run ID） | `error_category` |

**面板 27-28**（双 Y 轴）与推理失败面板结构一致。

**面板 29** 的错误分类（堆叠柱状图）：

| 类别 | 中文标签 | 颜色 | 说明 |
|------|---------|------|------|
| `database` | DB-数据库异常 (Database Error) | 红色 | PostgreSQL 连接或查询异常 |
| `timeout` | DB-查询超时 (Query Timeout) | 橙色 | DB 查询超过 30s 分段超时 |
| `serialize_timeout` | 内部-序列化超时 (Serialize Timeout) | 黄色 | JSON 序列化超过 30s 分段超时 |
| `rate_limit` | 内部-请求被限流 (429 Too Many Requests) | 紫色 | 信号量拒绝 |
| `other` | 内部-其他未知错误 (Other) | 灰色 | 未分类的其他错误 |

---

### Row 4 — 系统资源

> 进程级别的资源消耗监控，从基础设施层观察服务健康度。

#### 面板 30: Worker 存活状态

| 属性 | 值 |
|------|-----|
| 类型 | Stat（UP/DOWN） |
| 指标 | `up{job="trajproxy"}` |

**含义**: 各 Worker 实例的存活状态。`up=1` 显示绿色 **UP**，`up=0` 显示红色 **DOWN**。

**用途**: **最直接的存活指示器**。任一实例变 DOWN 即触发 AlertManager 的 `WorkerDown` 告警（severity: critical）。

---

#### 面板 31: Worker 内存 (RSS)

| 属性 | 值 |
|------|-----|
| 类型 | 时序折线图 |
| 指标 | `process_resident_memory_bytes{job="trajproxy"}` |
| 阈值线 | 2GB（红色） |

**含义**: 各 Worker 进程的常驻内存 (RSS)。超过 2GB 红色阈值线时预警。

**用途**: 内存持续增长可能意味着 Processor LRU 缓存未正常淘汰，或存在内存泄漏。触发 `WorkerHighMemory` 告警。

---

#### 面板 32: CPU 使用率

| 属性 | 值 |
|------|-----|
| 类型 | 时序折线图 |
| 指标 | `process_cpu_seconds_total{job="trajproxy"}`（5min rate × 100） |

**含义**: 各 Worker 进程的 CPU 使用率百分比。

**用途**: CPU 长期高位需排查是否存在同步阻塞操作或 Tokenizer 重复加载。

---

#### 面板 33: 打开文件描述符

| 属性 | 值 |
|------|-----|
| 类型 | 时序折线图 |
| 指标 | `process_open_fds{job="trajproxy"}` |

**含义**: 各 Worker 进程当前打开的文件描述符数量（包括 TCP 连接）。

**用途**: FD 数量持续增长可能意味着连接泄漏（HTTP client 或 DB connection pool）。接近系统限制（通常 65536）时会导致请求失败。

---

#### 面板 34: DB 连接池使用率 - 按节点标识

| 属性 | 值 |
|------|-----|
| 类型 | 时序折线图 |
| 指标 | `trajproxy_db_pool_usage{state="used"}` |
| 分组维度 | `node` |

**含义**: 各节点的数据库连接池实际使用连接数。

**用途**: 如果使用数持续接近连接池上限，可能触发 `DBPoolSaturation` 告警，导致请求排队和延迟增加。

---

## Dashboard 2: TrajProxy Model Mix

> 聚焦模型维度的流量和 Token 分析，用于评估各模型的使用效率。

**模板变量**: `$model`（模型名，多选）、`$route_type`（路由类型，多选）。

### 概览行

4 个 Stat 面板横排展示核心 SLI：

| 面板 | 含义 | 单位 | 阈值 |
|------|------|------|------|
| **QPM** | 每分钟请求数 | reqpm | < 100 绿 / 100~200 黄 / > 200 红 |
| **活跃模型数** | 当前有请求流量的模型数量 | 个 | — |
| **错误率** | 失败请求 / 总请求 | percentunit | < 1% 绿 / 1~5% 黄 / > 5% 红 |
| **请求延迟 P95** | P95 端到端耗时 | 秒 | < 5s 绿 / 5~15s 黄 / > 15s 红 |

**用途**: 模型维度的健康快览。QPM 反映整体负载，错误率和延迟 P95 反映服务质量。

---

### 模型使用统计行

5 个面板深入分析模型使用模式：

| 面板 | 类型 | 含义 |
|------|------|------|
| **Token 转换比率** | 双向折线图 | 各模型的 input/output Token 转换占比（居中零轴，正向为 in，反向为 out） |
| **Token 流量镜像** | 双向折线图 | 从下游推理服务镜像的 input/output Token 速率 |
| **推理 Pipeline 各阶段耗时** | 折线图 | P50 下各阶段（total/reason/prefill/decode/convert/overhead）耗时对比。overhead 为虚线，等于总耗时减去各阶段之和 |
| **推理流式与非流式占比(1h)** | 百分比堆叠面积图 | 流式 vs 非流式的实时占比变化 |
| **错误分布** | 堆叠柱状图 | 按 `error_type` 分类的错误数量时间分布 |

---

### Token 统计行

| 面板 | 类型 | 含义 |
|------|------|------|
| **输入 Token 速率** | 折线图 | 各模型每秒消耗的 input Token 数 (tps) |
| **输出 Token 速率** | 折线图 | 各模型每秒消耗的 output Token 数 (tps) |
| **Token 市场份额** | 环形饼图 | 各模型在选定时间范围内的 Token 总消耗占比 |

**用途**: 评估各模型的使用成本和负载贡献。Token 市场份额饼图直观展示哪个模型消耗最多资源。

---

### 系统状态行

| 面板 | 含义 |
|------|------|
| **内存使用** | 进程 RSS 内存（`process_resident_memory_bytes`） |
| **CPU 使用** | 进程 CPU 使用率（`rate(process_cpu_seconds_total)`） |
| **Goroutines** | Go 协程数（注：此面板仅对 Go 语言实现的组件有效） |
| **请求 QPS (按模型)** | 每秒请求数按模型堆叠 |

---

## 告警规则

AlertManager 定义了 7 条告警规则，覆盖从服务存活到性能退化的关键场景：

| # | 告警名 | 严重度 | 触发条件 | 持续时间 |
|---|--------|--------|----------|----------|
| 1 | **HighErrorRate** | warning | 节点级请求错误率 > 5% | 5 分钟 |
| 2 | **HighLatency** | warning | 节点级 P95 延迟 > 60 秒 | 5 分钟 |
| 3 | **InferErrors** | critical | 模型级推理错误速率 > 0.5/s | 3 分钟 |
| 4 | **InferRetriesHigh** | warning | 模型级重试速率 > 1/s | 5 分钟 |
| 5 | **DBPoolSaturation** | warning | DB 连接池使用率 > 85% | 5 分钟 |
| 6 | **WorkerDown** | critical | Worker 实例不可达 (`up == 0`) | 1 分钟 |
| 7 | **WorkerHighMemory** | warning | Worker 内存 > 2GB | 10 分钟 |

**严重程度说明**:
- `critical` — 立即响应：服务不可用或核心功能严重受损
- `warning` — 关注处理：服务质量下降或资源瓶颈

---

## 常见查询模式

### 诊断推理失败

1. 查看 **Row 1 > 推理请求概览**，确认成功率是否下降
2. 查看 **Row 2 > 推理请求数 - 按结果**，观察哪种 `outcome` 上升
3. 在 `$run_id` 选择目标标识，查看 **推理请求错误细分** 面板
4. 查看 **推理 Pipeline 各阶段耗时**，定位瓶颈阶段
5. 如果是 `error_infer`，查看 **推理内部重试次数** 面板

### 诊断轨迹查询慢

1. 查看 **Row 1 > 轨迹请求概览**，确认平均耗时是否超标
2. 查看 **Row 3 > 轨迹请求平均大小**，判断是否因数据量过大
3. 查看 **轨迹平均对话数量**，确认 records 规模
4. 查看 **轨迹请求错误细分**，排查是否有 `serialize_timeout` 或 `timeout`

### 评估系统容量

1. 查看 **Row 0 > 全站请求数 - 按节点**，确认负载均衡
2. 查看 **Row 4 > CPU 使用率** 和 **Worker 内存**，判断资源水位
3. 查看 **DB 连接池使用率**，评估连接池是否接近饱和
4. 查看 **打开文件描述符**，确认是否接近系统限制

### 评估 TITO 缓存效果

1. 查看 **Row 2 > DIRECT vs TITO 模式占比**，确认 TITO 流量比例
2. 查看 **Row 2b > 缓存命中率**，评估缓存有效性
3. 命中率低时，检查多轮对话是否共享足够的前缀 Token
