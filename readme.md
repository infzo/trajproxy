# TrajProxy - LLM 代理服务

TrajProxy 是一个强大的 LLM 代理系统，提供统一的 OpenAI 兼容 API，支持 Token-in-Token-out 模式、多 Worker 并发处理、会话管理和监控等功能。

## 项目概述

TrajProxy采用基于Ray的分布式Worker架构，支持高并发的LLM推理请求处理和对话历史管理。

- **ProxyCore Worker**: 处理LLM推理请求，支持Token-in-Token-out模式和前缀匹配缓存
- **TranscriptProvider Worker**: 提供对话历史查询服务

## 部署视图

```mermaid
graph TB
    subgraph "客户端"
        Client[客户端应用]
    end

    subgraph "Docker容器"
        direction TB
        subgraph "litellm容器"
            LiteLLM[LiteLLM<br/>API网关<br/>端口:4000]
        end

        subgraph "traj_proxy容器"
            WM[WorkerManager<br/>主进程]
            subgraph "Ray Workers"
                PW0[ProxyWorker-0<br/>12300]
                PW1[ProxyWorker-1<br/>12301]
            end
        end
    end

    subgraph "外部服务"
        DB[(PostgreSQL<br/>端口:5432)]
        Infer[Infer服务<br/>LLM推理]
    end

    subgraph "监控"
        Prometheus[Prometheus<br/>端口:9090]
    end

    Client -->|OpenAI兼容API<br/>Anthropic API| LiteLLM
    LiteLLM -->|转发请求| PW0
    LiteLLM -->|转发请求| PW1

    PW0 -->|推理请求| Infer
    PW1 -->|推理请求| Infer
    PW0 -->|存储/查询轨迹| DB
    PW1 -->|存储/查询轨迹| DB

    PW0 -->|指标| Prometheus
    PW1 -->|指标| Prometheus

    WM -.->|管理| PW0
    WM -.->|管理| PW1

    style Client fill:#e1f5ff
    style LiteLLM fill:#c8e6c9
    style WM fill:#fff4e1
    style PW0 fill:#ffcc80
    style PW1 fill:#ffcc80
    style DB fill:#d1ecf1
    style Infer fill:#f8d7da
    style Prometheus fill:#e8f5e9
```

## 请求处理流程

```mermaid
flowchart LR
    Start[客户端请求] --> API{请求类型}

    API -->|OpenAI/Anthropic| LiteLLM[LiteLLM网关]
    API -->|查询历史| TP[TranscriptProvider]

    LiteLLM -->|路由到ProxyCore| PC[ProxyCore Worker]

    subgraph "ProxyCore处理流程"
        direction TB
        PC --> PB[构建Prompt]
        PB --> MODE{处理模式}

        MODE -->|Text模式| PC_Text[发送文本到Infer]
        MODE -->|Token模式| PC_Token[前缀匹配编码<br/>发送Token到Infer]

        PC_Text --> Infer[Infer服务]
        PC_Token --> Infer

        Infer --> Response[接收响应]
        Response --> Build[构建OpenAI响应]
        Build --> Save[存储轨迹到DB]
    end

    TP --> Query[查询历史记录]
    Query --> DB[(PostgreSQL)]
    DB --> TP

    Save --> Return[返回响应]
    TP --> Return

    Return --> End[客户端]

    style Start fill:#e1f5ff
    style LiteLLM fill:#c8e6c9
    style PC fill:#ffcc80
    style TP fill:#ffe0b2
    style Infer fill:#f8d7da
    style DB fill:#d1ecf1
    style End fill:#e1f5ff
```

## 架构组件

| 组件 | 端口 | 说明 |
|------|------|------|
| LiteLLM | 4000 | API 网关，提供统一的 OpenAI 兼容接口 |
| ProxyWorker | 12300-12310 | 统一的代理服务，集成了 LLM 推理和轨迹查询功能 |
| PostgreSQL | 5432 | 数据库存储 |
| Prometheus | 9090 | 监控和指标收集 |

---

## 快速开始

### 前置要求

#### 通用要求
- LLM 推理服务（如 Ollama、vLLM 等）运行在 `http://localhost:1234`
- PostgreSQL 数据库（可本地运行或容器运行）

#### 本地开发模式
- Python 3.11+
- pip 依赖安装：`pip install -r traj_proxy/requirements.txt`

#### Docker 容器模式
- Docker >= 20.10
- Docker Compose >= 2.0
- 至少 8GB 可用内存

### 1. 配置环境

编辑 `configs/config.yaml`，配置 LLM 推理服务地址和数据库连接：

```yaml
# ProxyWorker 配置
proxy_workers:
  count: 2
  base_port: 12300
  models:
    - model_name: qwen3.5-2b
      url: http://localhost:1234  # LLM 推理服务地址
      api_key: sk-1234
      tokenizer_path: Qwen/Qwen3.5-2B  # HuggingFace 模型名称或本地路径
      token_in_token_out: true  # 启用 Token-in-Token-out 模式，支持前缀匹配缓存

# Ray配置
ray:
  num_cpus: 4
  working_dir: "/app"  # 容器内路径，可通过环境变量 RAY_WORKING_DIR 覆盖
  pythonpath: "/app"   # 容器内 PYTHONPATH，可通过环境变量 RAY_PYTHONPATH 覆盖

# 数据库配置
database:
  url: "postgresql://llmproxy:dbpassword9090@localhost:5432/traj_proxy"
```

### 2. 启动服务

提供两种部署方式，根据需求选择：

#### 方式一：本地开发启动

适用于开发调试场景，直接在本地运行 Python 进程，连接外部数据库。

```bash
cd traj_proxy

# 一键启动
./start_local.sh
```

**说明**：
- `start_local.sh` 会自动设置本地环境变量 `RAY_WORKING_DIR="."` 和 `RAY_PYTHONPATH="."`
- 依赖本地已安装的 PostgreSQL 数据库
- TrajProxy 运行在宿主机上，端口直接监听

#### 方式二：Docker 容器化部署

适用于生产环境，使用 Docker Compose 启动完整的容器组（包括 litellm、postgresdb、traj_proxy、prometheus）。

```bash
cd traj_proxy

# 一键启动所有容器
./start_docker.sh
```

**说明**：
- `start_docker.sh` 会自动执行 `docker-compose up -d`
- 启动完整的容器组：litellm（网关）、db（数据库）、traj_proxy（代理）、prometheus（监控）
- 数据库 URL 配置为 `postgresql://llmproxy:dbpassword9090@db:5432/litellm`（容器内网络）

### 3. 验证服务

```bash
# 检查 LiteLLM 健康状态（仅方式二）
curl http://localhost:4000/health/liveliness

# 检查 TrajProxy 健康状态（通用）
curl http://localhost:12300/health

# 检查可用模型
curl http://localhost:12300/proxy/v1/models
```

---

## 部署教程

### 方式一：本地开发部署

适用于开发调试场景，直接在本地运行。

#### 配置说明

编辑 `configs/config.yaml`：

```yaml
proxy_workers:
  count: 2
  base_port: 12300
  models:
    - model_name: qwen3.5-2b
      url: http://localhost:1234  # 本地运行的推理服务
      api_key: sk-1234
      tokenizer_path: Qwen/Qwen3.5-2B  # HuggingFace 模型名称或本地路径
      token_in_token_out: true  # 启用前缀匹配缓存

ray:
  num_cpus: 4
  working_dir: "."      # 本地路径
  pythonpath: "."       # 本地 PYTHONPATH

# 数据库配置
database:
  url: "postgresql://llmproxy:dbpassword9090@localhost:5432/traj_proxy"
```

**关键点**：
- `url: http://localhost:1234` - 连接本地运行的 LLM 推理服务
- `database.url: ...localhost...` - 连接本地运行的 PostgreSQL
- `working_dir: "."` 和 `pythonpath: "."` - 使用当前目录

#### 启动步骤

```bash
# 进入项目目录
cd traj_proxy

# 确保本地数据库已运行
# docker run -d --name litellm_db -e POSTGRES_DB=litellm -e POSTGRES_USER=llmproxy -e POSTGRES_PASSWORD=dbpassword9090 -p 5432:5432 postgres:16

# 一键启动
./start_local.sh
```

#### 常用命令

```bash
# 停止服务
Ctrl+C  # 或者 kill 进程

# 查看日志
# 日志会直接输出到终端
```

---

### 方式二：Docker 容器化部署

适用于生产环境，使用 Docker Compose 启动完整的容器组。

#### 配置说明

编辑 `configs/config.yaml`：

```yaml
proxy_workers:
  count: 2
  base_port: 12300
  models:
    - model_name: qwen3.5-2b
      url: http://host.docker.internal:1234  # 宿主机推理服务
      api_key: sk-1234
      tokenizer_path: Qwen/Qwen3.5-2B  # HuggingFace 模型名称或本地路径
      token_in_token_out: true  # 启用前缀匹配缓存

ray:
  num_cpus: 4
  working_dir: "/app"  # 容器内路径
  pythonpath: "/app"   # 容器内 PYTHONPATH

# 数据库配置
database:
  url: "postgresql://llmproxy:dbpassword9090@db:5432/traj_proxy"
```

**关键点**：
- `url: http://host.docker.internal:1234` - 从容器访问宿主机服务
- `database.url: ...db:5432...` - 连接容器内的数据库服务
- `working_dir: "/app/traj_proxy"` - 容器内工作目录

#### 启动步骤

```bash
# 进入项目目录
cd traj_proxy

# 一键启动所有容器
./start_docker.sh

# 或直接使用 docker-compose
docker-compose up -d --build
```

#### 容器组说明

启动后包含以下容器：

| 容器名 | 端口 | 说明 |
|---------|-------|------|
| litellm | 4000 | API 网关 |
| db | 5432 | PostgreSQL 数据库 |
| traj_proxy | 12300-12310 | ProxyWorkers（统一服务） |
| prometheus | 9090 | 监控服务 |

#### 常用命令

```bash
# 查看服务状态
docker-compose ps

# 查看日志
docker-compose logs -f

# 停止服务
./start_docker.sh  # 或 docker-compose down

# 重启服务
docker-compose restart

# 进入容器
docker-compose exec traj_proxy /bin/bash

# 进入数据库
docker-compose exec db psql -U llmproxy -d litellm
```

---

### 生产环境优化

#### 资源配置

修改 `docker-compose.yml` 添加资源限制：

```yaml
traj_proxy:
  deploy:
    resources:
      limits:
        cpus: '4'
        memory: 8G
      reservations:
        cpus: '2'
        memory: 4G
```

#### Worker 数量调优

根据服务器配置调整 `config.yaml`：

```yaml
proxy_workers:
  count: 4  # 根据 CPU 核心数调整

ray:
  num_cpus: 8  # 实际 CPU 核心数
```

### 配置说明

#### config.yaml

Worker 配置文件位于 `configs/config.yaml`，主要配置项：

```yaml
# ProxyWorker 配置
proxy_workers:
  count: 2                    # Worker数量
  base_port: 12300            # 起始端口（12300, 12301）
  models:                     # 预置模型配置
    - model_name: qwen3.5-2b
      url: http://host.docker.internal:1234  # LLM 推理服务地址
      api_key: sk-1234
      tokenizer_path: Qwen/Qwen3.5-2B  # HuggingFace 模型名称或本地路径
      token_in_token_out: true  # 启用 Token-in-Token-out 模式，支持前缀匹配缓存

# Ray 配置
ray:
  num_cpus: 4                 # CPU核心数
  working_dir: "/app"         # 默认容器内路径，可通过环境变量 RAY_WORKING_DIR 覆盖
  pythonpath: "/app"          # 默认容器内 PYTHONPATH，可通过环境变量 RAY_PYTHONPATH 覆盖

# 数据库配置
database:
  url: "postgresql://llmproxy:dbpassword9090@db:5432/traj_proxy"
```

**关键说明**：
- `host.docker.internal` 用于从 Docker 容器访问宿主机服务
- 本地部署时，使用 `url: http://localhost:1234` 和 `database.url: ...localhost...`
- 容器部署时，使用 `url: http://host.docker.internal:1234` 和 `database.url: ...db:5432...`
- `working_dir` 和 `pythonpath` 支持通过环境变量 `RAY_WORKING_DIR` 和 `RAY_PYTHONPATH` 覆盖

#### litellm.yaml

LiteLLM 配置文件，定义模型路由规则：

```yaml
model_list:
  # 通用匹配：所有模型请求都路由到 traj_proxy
  - model_name: "*"
    litellm_params:
      model: "openai/*"
      api_base: http://traj_proxy:12300/proxy/v1  # 内部 ProxyCore 服务
      api_key: "sk-1234"
  - model_name: "*"
    litellm_params:
      model: "openai/*"
      api_base: http://traj_proxy:12301/proxy/v1  # 第二个 ProxyCore 实例
      api_key: "sk-1234"

litellm_settings:
  drop_params: true       # 自动删除模型不支持的参数
  disable_request_checking: true

general_settings:
  master_key: "sk-1234"  # API 认证密钥
  forward_client_headers_to_llm_api: true  # 转发 x- 开头的请求头
```

---

## 使用案例

### 案例 1：通过 LiteLLM 调用（推荐）

使用 OpenAI 兼容的聊天接口：

```bash
curl --location 'http://localhost:4000/v1/chat/completions' \
  --header 'Content-Type: application/json' \
  -H "Authorization: Bearer sk-1234" \
  -H "x-session-id: app_001#sample_001#task_001" \
  --data '{
    "model": "qwen3.5-2b",
    "messages": [
      {
        "role": "user",
        "content": "你好，请介绍一下自己"
      }
    ]
  }'
```

### 案例 2：Anthropic Messages API

通过 LiteLLM 兼容 Anthropic 的消息接口：

```bash
curl --location 'http://localhost:4000/v1/messages' \
  --header 'x-api-key: sk-1234' \
  --header 'anthropic-version: 2023-06-01' \
  --header 'Content-Type: application/json' \
  --data '{
    "model": "qwen3.5-2b",
    "max_tokens": 1024,
    "messages": [
      {
        "role": "user",
        "content": "what llm are you"
      }
    ]
  }'
```

### 案例 3：直接调用 TrajProxy Core

绕过 LiteLLM 直接访问代理服务：

```bash
curl --location 'http://localhost:12300/proxy/v1/chat/completions' \
  --header 'Content-Type: application/json' \
  -H "x-session-id: app_001#sample_001#task_001" \
  --data '{
    "model": "qwen3.5-2b",
    "messages": [
      {
        "role": "user",
        "content": "what llm are you"
      }
    ]
  }'
```

### 案例 4：Python 客户端示例

```python
import openai

# 配置客户端（通过 LiteLLM）
client = openai.OpenAI(
    base_url="http://localhost:4000/v1",
    api_key="sk-1234"
)

# 发送请求
response = client.chat.completions.create(
    model="qwen3.5-2b",
    messages=[
        {"role": "user", "content": "你好！"}
    ]
)

print(response.choices[0].message.content)
```

### 案例 5：查询对话历史

```bash
curl "http://localhost:12300/transcript/trajectory?session_id=app_001;sample_001;task_001"
```

---

## API端点

### LiteLLM 网关（端口 4000）
- `POST /v1/chat/completions` - OpenAI 兼容聊天补全
- `POST /v1/messages` - Anthropic 兼容消息接口
- `GET /v1/models` - 列出可用模型
- `GET /health/liveliness` - 健康检查

### TrajProxy Core（端口 12300-12310）
- `POST /proxy/v1/chat/completions` - 聊天补全
- `GET /proxy/v1/models` - 列出已注册模型
- `POST /proxy/models/register` - 注册新模型
- `DELETE /proxy/models/{model_name}` - 删除模型
- `GET /health` - 健康检查

### TranscriptProvider（端口 12310-12315）
- `GET /transcript/trajectory` - 查询对话轨迹记录
- `GET /transcript/health` - 健康检查

---

## 监控与日志

### Prometheus 监控（仅 Docker 容器模式）

访问 Prometheus UI：
```
http://localhost:9090
```

### 查看请求记录

连接到 PostgreSQL 数据库：

**本地开发模式：**
```bash
psql -h localhost -U llmproxy -d litellm

# 查询请求记录
SELECT * FROM request_records ORDER BY start_time DESC LIMIT 10;

# 查询特定会话的记录
SELECT * FROM request_records WHERE session_id = 'app_001;sample_001;task_001';
```

**Docker 容器模式：**
```bash
# 进入数据库
docker-compose exec db psql -U llmproxy -d litellm

# 查询请求记录
SELECT * FROM request_records ORDER BY start_time DESC LIMIT 10;

# 查询特定会话的记录
SELECT * FROM request_records WHERE session_id = 'app_001;sample_001;task_001';
```

### 查看日志

**本地开发模式：**
```bash
# 日志直接输出到终端，通过重定向保存
./start_local.sh 2>&1 | tee logs/traj_proxy.log
```

**Docker 容器模式：**
```bash
# 查看所有服务日志
docker-compose logs -f

# 查看特定服务日志
docker-compose logs -f traj_proxy
docker-compose logs -f litellm
```

---

## 常用命令

### 本地开发模式

```bash
# 启动服务
./start_local.sh

# 停止服务
Ctrl+C

# 查看日志
# 日志直接输出到终端
```

### Docker 容器模式

```bash
# 启动所有容器
./start_docker.sh

# 停止所有容器
docker-compose down

# 停止所有容器并清理数据（谨慎使用）
docker-compose down -v

# 重启服务
docker-compose restart

# 查看服务状态
docker-compose ps

# 查看所有服务日志
docker-compose logs -f

# 查看特定服务日志
docker-compose logs -f traj_proxy
docker-compose logs -f litellm
docker-compose logs -f db

# 进入容器
docker-compose exec traj_proxy /bin/bash

# 进入数据库
docker-compose exec db psql -U llmproxy -d litellm
```

---

## 故障排查

### 本地开发模式

#### 服务无法启动

```bash
# 检查 Python 环境
python --version  # 需要 Python 3.11+

# 检查端口占用
lsof -i :12300
lsof -i :12310

# 检查依赖安装
pip list | grep -E "fastapi|uvicorn|ray|psycopg"
```

#### 数据库连接失败

```bash
# 检查 PostgreSQL 是否运行
psql -h localhost -U llmproxy -d litellm -c "SELECT 1"

# 检查数据库连接配置
cat traj_proxy/config.yaml | grep database
```

### Docker 容器模式

#### 服务无法启动

```bash
# 检查端口占用
lsof -i :4000
lsof -i :12300

# 检查 Docker 磁盘空间
docker system df

# 清理未使用的资源
docker system prune
```

#### 数据库连接失败

```bash
# 检查数据库状态
docker-compose ps db

# 查看数据库日志
docker-compose logs db
```

#### Worker 无响应

```bash
# 检查 Worker 进程
docker-compose exec traj_proxy ps aux | grep worker

# 检查 Worker 配置
docker-compose exec traj_proxy cat /app/traj_proxy/config.yaml
```

---

## 技术栈

- **Python 3.8+**
- **Ray** - 分布式计算框架
- **FastAPI** - Web框架
- **Uvicorn** - ASGI服务器
- **PostgreSQL** - 数据库
- **Psycopg3** - PostgreSQL驱动

## 项目结构

```
traj_proxy/
├── traj_proxy/
│   ├── app.py                      # 主入口
│   ├── config.yaml                 # 配置文件
│   ├── proxy_core/                 # 推理核心模块
│   │   ├── processor.py           # 主处理器
│   │   ├── processor_manager.py   # 处理器管理器
│   │   ├── prompt_builder.py      # 消息转换器
│   │   ├── token_builder.py       # Token处理器
│   │   ├── infer_client.py        # 推理客户端
│   │   ├── context.py             # 上下文数据类
│   │   └── routes.py              # API路由
│   ├── transcript_provider/        # 对话历史模块
│   │   ├── provider.py            # 对话历史提供者
│   │   └── routes.py              # API路由
│   ├── store/                      # 存储模块
│   │   ├── database_manager.py    # 数据库管理器
│   │   ├── request_repository.py  # 请求记录仓库
│   │   └── model_repository.py    # 模型配置仓库
│   ├── workers/                    # Worker 模块
│   │   ├── worker.py              # 统一的 ProxyWorker 实现
│   │   ├── manager.py             # Worker 管理器
│   │   └── route_registrar.py     # 路由注册器
│   └── utils/                      # 工具模块
│       ├── config.py              # 配置管理
│       └── logger.py              # 日志系统
├── docker-compose.yml              # Docker编排文件
├── Dockerfile                      # 镜像构建文件
├── litellm.yaml                    # LiteLLM配置
├── prometheus.yml                  # Prometheus配置
├── start_local.sh                 # 本地开发启动脚本
├── start_docker.sh                # Docker Compose 启动脚本
└── dockers/                        # 导出的镜像文件
```

---

## 端口映射

| 外部端口 | 内部端口 | 服务 | 说明 |
|----------|----------|------|------|
| 4000 | 4000 | LiteLLM API | API 网关入口 |
| 5432 | 5432 | PostgreSQL | 数据库访问 |
| 12300-12310 | 12300-12310 | ProxyWorkers | 统一代理服务（多实例） |
| 9090 | 9090 | Prometheus | 监控面板 |

**使用建议**：
- 客户端请求统一通过 `4000` 端口（LiteLLM）访问
- 内部服务端口（12300+）用于直接调用和调试

---

## 许可证

MIT License
