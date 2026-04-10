# TrajProxy 文档中心

本目录包含 TrajProxy 项目的完整文档，按以下结构组织：

---

## 目录结构

```
docs/
├── README.md                    # 文档索引（本文件）
│
├── design/                      # 架构设计文档
│   ├── architecture.md          # 系统架构设计
│   ├── database.md              # 数据库设计
│   ├── identifier_design.md     # ID 设计规范
│   ├── next_db.md               # 数据库优化方案
│   ├── parser.md                # Parser 模块设计
│   ├── tito-v1.md               # TITO 前缀匹配方案
│   ├── tito-v2.md               # TITO 模式集成方案
│   └── jinja-process.md         # Jinja 模板转换工作流
│
├── develop/                     # 开发与运维文档
│   ├── development.md           # 开发环境搭建
│   ├── deployment.md            # 部署指南
│   ├── configuration.md         # 配置说明
│   ├── api_reference.md         # API 参考手册
│   ├── store.md                 # Store 模块文档
│   ├── release.md               # 变更日志
│   ├── core_cases.md            # 核心用例测试
│   └── compare_vllm.md          # 与 vLLM 接口对比
│
└── experience/                  # 经验总结（项目历程）
    └── README.md                # 经验索引
```

---

## 快速导航

### 新手入门

1. [开发环境搭建](develop/development.md) - 本地开发环境配置
2. [部署指南](develop/deployment.md) - 本地/Docker 部署方式
3. [配置说明](develop/configuration.md) - 配置文件详解
4. [API 参考](develop/api_reference.md) - OpenAI 兼容接口

### 架构设计

1. [系统架构](design/architecture.md) - Pipeline 架构、核心组件
2. [数据库设计](design/database.md) - 表结构、存储策略
3. [Parser 模块](design/parser.md) - 工具调用和推理内容解析
4. [TITO 模式](design/tito-v1.md) - Token-in-Token-out 前缀匹配

### 核心概念

- [ID 设计规范](design/identifier_design.md) - run_id、session_id、model 语义定义
- [Jinja 模板转换](design/jinja-process.md)

### 项目历程

- [变更日志](develop/release.md) - 版本变更记录
- [经验总结](experience/README.md) - 问题解决历程

---

## 文档分类说明

| 类型 | 位置 | 说明 |
|------|------|------|
| 架构/模块设计 | `design/` | 系统架构、模块设计、技术方案 |
| 开发/运维文档 | `develop/` | 开发指南、API 参考、配置说明、测试用例 |
| 经验沉淀 | `experience/` | 问题解决、踩坑记录、架构演进 |
