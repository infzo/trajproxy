# TrajProxy - LLM 代理服务

TrajProxy 是一个 LLM 请求代理系统，提供 OpenAI 兼容 API、Token-in-Token-out 模式、动态模型管理和请求轨迹记录功能。

## 特性

- **OpenAI 兼容 API** - 无缝对接现有客户端
- **Token-in-Token-out 模式** - 支持前缀匹配缓存
- **动态模型管理** - 运行时注册/删除模型
- **请求轨迹记录** - 完整的对话历史存储
- **多 Worker 架构** - Ray 分布式处理，高并发支持

## 快速开始

### 前置要求

- Python 3.11+
- PostgreSQL 数据库
- LLM 推理服务（如 vLLM、Ollama）

### 本地开发

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 初始化数据库
export DATABASE_URL="postgresql://user:pass@host:5432/traj_proxy"
python scripts/init_db.py

# 3. 启动服务
./scripts/start_local.sh
```

### Docker 部署

```bash
# 一键启动所有容器
./scripts/start_docker.sh
```

### 验证服务

```bash
# 健康检查
curl http://localhost:12300/health

# 发送请求
curl -X POST http://localhost:12300/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "qwen3.5-2b", "messages": [{"role": "user", "content": "你好"}]}'
```

## 项目结构

```
TrajProxy/
├── configs/           # 配置文件
├── docs/              # 详细文档
├── traj_proxy/        # 主代码
│   ├── proxy_core/    # 推理核心
│   ├── store/         # 存储层
│   ├── workers/       # Worker 管理
│   └── utils/         # 工具模块
├── tests/             # 测试
├── scripts/           # 脚本
└── dockers/           # Docker 配置
```

## 文档

详细文档请参阅 [docs/](docs/) 目录：

| 文档 | 说明 |
|------|------|
| [架构设计](docs/architecture.md) | 系统架构、核心组件、处理流程 |
| [API 参考](docs/api_reference.md) | 完整的 API 接口文档 |
| [配置详解](docs/configuration.md) | 配置文件说明、环境变量 |
| [数据库设计](docs/database.md) | 表结构、索引、同步机制 |
| [部署指南](docs/deployment.md) | 本地开发、Docker 部署 |
| [开发指南](docs/development.md) | 开发环境、测试运行 |

## 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                      WorkerManager                           │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐          │
│  │ ProxyWorker │  │ ProxyWorker │  │ ProxyWorker │  ...     │
│  │   :12300    │  │   :12301    │  │   :12302    │          │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘          │
└─────────┼────────────────┼────────────────┼─────────────────┘
          │                │                │
          ▼                ▼                ▼
    ┌──────────┐     ┌──────────┐     ┌──────────┐
    │PostgreSQL│     │ Infer服务 │     │Prometheus│
    │   (DB)   │     │  (LLM)   │     │ (监控)   │
    └──────────┘     └──────────┘     └──────────┘
```

## 端口说明

| 端口 | 服务 | 说明 |
|------|------|------|
| 12345 | Nginx | 统一入口网关（Docker 部署） |
| 4000 | LiteLLM | API 网关（Docker 部署） |
| 12300+ | ProxyWorkers | TrajProxy 服务 |
| 5432 | PostgreSQL | 数据库 |

## 许可证

MIT License
