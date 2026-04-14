# TrajProxy 文档中心

本目录包含 TrajProxy 项目的完整文档，按以下结构组织：

---

## 目录结构

```
docs/
├── README.md                    # 文档索引（本文件）
│
├── design/                      # 架构设计文档
│   ├── architecture.md          # 系统架构设计（Pipeline 模式）
│   ├── database.md              # 数据库设计（元数据+详情分离）
│   ├── archive_mechanism.md     # 数据库归档机制设计
│   ├── identifier_design.md     # ID 设计规范（run_id, session_id）
│   ├── parser.md                # Parser 模块设计
│   ├── jinja-process.md         # Jinja 模板转换工作流
│   └── tito.md                  # TITO 前缀匹配方案（含方案对比）
│
├── develop/                     # 开发与运维文档
│   ├── api_nginx.md             # Nginx 入口 API（端口 12345）
│   ├── api_proxy.md             # TrajProxy API（端口 12300+）
│   ├── api_reference.md         # API 文档索引
│   ├── development.md           # 开发环境搭建
│   ├── deployment.md            # 部署指南
│   ├── configuration.md         # 配置说明
│   ├── store.md                 # Store 模块文档
│   ├── trajectory_viewer.md     # 轨迹查看器使用文档
│   ├── release.md               # 变更日志
│   ├── core_cases.md            # 核心用例测试
│   └── compare_vllm.md          # 与 vLLM 接口对比
│
└── experience/                  # 经验总结（项目历程）
    ├── README.md                # 经验索引
    └── 20260410_exp_session_id_unified_semantics.md
```

---

## 快速导航

### 新手入门

1. [开发环境搭建](develop/development.md) - 本地开发环境配置
2. [部署指南](develop/deployment.md) - 本地/Docker 部署方式
3. [配置说明](develop/configuration.md) - 配置文件详解
4. [API 参考](develop/api_reference.md) - API 文档索引
5. [轨迹查看器](develop/trajectory_viewer.md) - 对话轨迹可视化工具

### API 文档

- [Nginx 入口 API](develop/api_nginx.md) - 生产环境统一入口 (端口 12345)
- [TrajProxy API](develop/api_proxy.md) - 直接访问 Worker (端口 12300+)

### 架构设计

1. [系统架构](design/architecture.md) - Pipeline 架构、核心组件、处理流程
2. [数据库设计](design/database.md) - 元数据+详情分离架构、归档机制
3. [归档机制](design/archive_mechanism.md) - 数据库归档设计文档（分区管理、调度执行、注意事项）
4. [ID 设计规范](design/identifier_design.md) - run_id、session_id、model 语义定义
5. [Parser 模块](design/parser.md) - 工具调用和推理内容解析
6. [TITO 模式](design/tito.md) - Token-in-Token-out 前缀匹配（含方案对比）

---

## 文档分类说明

| 类型 | 位置 | 说明 |
|------|------|------|
| 架构/模块设计 | `design/` | 系统架构、模块设计、技术方案 |
| 开发/运维文档 | `develop/` | 开发指南、API 参考、配置说明、测试用例 |
| 经验沉淀 | `experience/` | 问题解决、踩坑记录、架构演进 |
