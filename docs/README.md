# TrajProxy 文档中心

本目录包含 TrajProxy 项目的完整文档，按职责分为五个区域：

```
docs/
├── README.md                    # 文档索引（本文件）
├── release.md                   # 版本变更日志
│
├── assets/                      # 静态资源
│
├── design/                      # 架构设计（Why & What）
│   ├── architecture.md          # 系统总体架构（Pipeline 模式）
│   ├── observability.md         # 可观测性系统设计（EventBus + Prometheus + Grafana）
│   │
│   ├── data/                    # 数据层设计
│   │   ├── schema.md            # 数据库 Schema（元数据+详情分离）
│   │   ├── archive.md           # 数据归档机制（分区管理）
│   │   └── optimization.md      # 库表优化演进方案
│   │
│   ├── modules/                 # 核心模块设计
│   │   ├── parser.md            # Parser 模块（工具调用与推理解析）
│   │   ├── store.md             # Store 持久化层模块
│   │   └── identifiers.md       # ID 语义规范（run_id, session_id）
│   │
│   └── features/                # 特性/子系统方案
│       ├── tito.md              # TITO 前缀匹配（含方案对比）
│       └── jinja-template.md    # Jinja 多轮一致性转换
│
├── api/                         # 接口文档
│   ├── overview.md              # API 总览与选型
│   ├── nginx.md                 # Nginx 入口 API（端口 12345）
│   ├── proxy.md                 # TrajProxy 原生 API（端口 12300+）
│   └── vs-vllm.md               # 与 vLLM 接口行为对比
│
├── guide/                       # 操作指南（How）
│   ├── quickstart.md            # 快速上手（常用命令与核心用例）
│   ├── deployment.md            # 部署指南（本地 / Docker / 可观测性栈）
│   ├── observability.md         # 可观测性运维指南（Prometheus + Grafana）
│   ├── configuration.md         # 配置文件详解
│   └── development.md           # 开发环境搭建
│
└── tools/                       # 辅助工具
    ├── trajectory-viewer.md     # 轨迹查看器使用文档
    └── consistency-report.md    # Jinja 一致性验证报告规范
```

---

## 分类原则

| 类型 | 目录 | 定位 | 回答的问题 |
|------|------|------|----------|
| 架构设计 | `design/` | 系统架构、模块设计、特性方案 | 为什么这么设计？组件边界是什么？ |
| 接口文档 | `api/` | HTTP API 规范与行为说明 | 接口怎么调？入参出参是什么？ |
| 操作指南 | `guide/` | 部署、配置、开发实操步骤 | 如何把它跑起来？ |
| 工具 | `tools/` | 可视化/调试等辅助工具 | 有哪些现成工具可用？ |
| 变更 | `release.md` | 版本发布历史 | 最近改了什么？ |

---

## 快速导航

### 我是新手，从哪开始？

1. [快速上手](guide/quickstart.md) — 5 分钟跑通注册模型 → 发请求 → 查轨迹的完整流程
2. [部署指南](guide/deployment.md) — 本地 / Docker / 可观测性栈部署方式
3. [可观测性运维](guide/observability.md) — Prometheus + Grafana 监控部署与运维
4. [开发环境搭建](guide/development.md) — 本地环境配置、依赖安装、测试运行
5. [配置说明](guide/configuration.md) — 配置文件字段详解
6. [API 总览](api/overview.md) — 选择 Nginx 入口 和 直连 Proxy
7. [轨迹查看器](tools/trajectory-viewer.md) — 对话轨迹可视化

### 我想知道最近更新了什么

- [变更日志](release.md) — 版本发布历史与变更详情

### 我想了解架构设计

- [总体架构](design/architecture.md) — Pipeline 模式、Processor、核心组件
- [可观测性设计](design/observability.md) — EventBus 架构、Prometheus 指标、Grafana Dashboard
- [ID 语义规范](design/modules/identifiers.md) — `run_id` / `session_id` 语义与提取规则
- [数据库设计](design/data/schema.md) — 元数据+详情分离架构
- [Parser 模块](design/modules/parser.md) — 工具调用和推理内容解析
- [TITO 模式](design/features/tito.md) — Token-in-Token-out 前缀匹配缓存

### 我想调用 API

- [API 总览](api/overview.md) — 入口选择（Nginx 12345 vs Proxy 12300+）
- [Nginx 入口 API](api/nginx.md) — 生产环境统一入口，路径参数解析
- [TrajProxy 原生 API](api/proxy.md) — 直连 Worker，模型管理、轨迹查询、补全
- [与 vLLM 对比](api/vs-vllm.md) — Chat Completions 接口行为差异

### 我想扩展能力

- [自定义 Parser 按需发现](design/modules/parser.md#十二自定义-parser-按需发现机制) — 从自定义目录扩展解析能力
- [Store 模块](design/modules/store.md) — 持久化层接口与扩展
