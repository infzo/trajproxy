# 部署指南

> **导航**: [文档中心](../README.md) | [开发环境](development.md) | [配置说明](configuration.md)

TrajProxy 支持三种部署方式：本地开发模式、Docker Compose 容器化部署和 All-in-One 单容器部署。

---

## 前置要求

### 通用要求

- LLM 推理服务（如 vLLM、Ollama 等）已运行
- PostgreSQL 数据库

### 本地开发模式

- Python 3.11+
- pip 依赖：`pip install -r requirements.txt`

### Docker 容器模式

- Docker >= 20.10
- Docker Compose >= 2.0
- 至少 8GB 可用内存

---

## 方式一：本地开发部署

适用于开发调试场景，直接在本地运行 Python 进程。

### 1. 配置

编辑 `dockers/compose/configs/config.yaml`：

```yaml
proxy_workers:
  count: 2
  base_port: 12300
  models:
    - model_name: qwen3.5-2b
      url: http://localhost:1234  # 本地推理服务
      api_key: sk-1234
      tokenizer_path: Qwen/Qwen3.5-2B
      token_in_token_out: true

ray:
  num_cpus: 4
  working_dir: "."      # 本地路径
  pythonpath: "."

database:
  url: "postgresql://llmproxy:dbpassword9090@localhost:5432/traj_proxy"
```

### 2. 准备数据库

```bash
# 启动 PostgreSQL（如使用 Docker）
docker run -d --name traj_proxy_db \
    -e POSTGRES_DB=traj_proxy \
    -e POSTGRES_USER=llmproxy \
    -e POSTGRES_PASSWORD=dbpassword9090 \
    -p 5432:5432 postgres:16

# 初始化数据库
export DATABASE_URL="postgresql://llmproxy:dbpassword9090@localhost:5432/traj_proxy"
python scripts/init_db.py
```

### 3. 启动服务

```bash
# 设置本地环境变量并启动
export RAY_WORKING_DIR="."
export RAY_PYTHONPATH="."
python -m traj_proxy.app
```

### 4. 验证

```bash
# 健康检查
curl http://localhost:12300/health

# 查看模型列表
curl http://localhost:12300/models
```

---

## 方式二：Docker 容器化部署

适用于生产环境，使用 Docker Compose 启动完整的容器组。

### 1. 配置

编辑 `dockers/compose/configs/config.yaml`：

```yaml
proxy_workers:
  count: 2
  base_port: 12300
  models:
    - model_name: qwen3.5-2b
      url: http://host.docker.internal:1234  # 宿主机推理服务
      api_key: sk-1234
      tokenizer_path: Qwen/Qwen3.5-2B
      token_in_token_out: true

ray:
  num_cpus: 4
  working_dir: "/app"
  pythonpath: "/app"

database:
  url: "postgresql://llmproxy:dbpassword9090@db:5432/traj_proxy"
```

### 2. 启动服务

```bash
# 启动所有服务（默认）
./scripts/start_docker_compose.sh

# 或使用明确的 start 参数
./scripts/start_docker_compose.sh start

# 或手动启动
cd dockers/compose && docker-compose up -d --build
```

### 3. 容器组说明

启动后包含以下容器：

| 容器名 | 端口 | 说明 |
|--------|------|------|
| nginx | 12345 | 统一入口网关 |
| litellm | 4000 | API 网关 |
| db | 5432 | PostgreSQL 数据库 |
| traj_proxy | 12300-12320 | ProxyWorkers |

> 可观测性栈（Prometheus + Grafana + AlertManager + Viewer）已独立为单独的 Docker Compose 部署，
> 详见 [方式五：可观测性栈独立部署](#方式五可观测性栈独立部署)。

### 4. 验证

```bash
# 检查服务状态
cd dockers && docker-compose ps

# 检查 Nginx 健康状态
curl http://localhost:12345/health

# 检查 LiteLLM 健康状态
curl http://localhost:4000/health/liveliness

# 检查 TrajProxy 健康状态
curl http://localhost:12300/health
```

---

## 方式三：All-in-One 单容器部署

适用于不支持 Docker Compose 的环境（如某些内网服务器、单机部署），将 nginx + LiteLLM + PostgreSQL + TrajProxy + 归档进程打包为一个镜像，通过 supervisord 管理所有服务。

### 1. 构建镜像

```bash
cd dockers/allinone
./scripts/build_image.sh

# 或手动构建
docker build -t traj_proxy_allinone:latest -f dockers/allinone/Dockerfile .
```

### 2. 配置

编辑 `dockers/allinone/configs/config.yaml`：

```yaml
proxy_workers:
  count: 2
  base_port: 12300
  max_concurrent_requests: 4096
  models:
    - model_name: qwen3.5-2b
      url: http://host.docker.internal:8000/v1
      api_key: sk-1234

database:
  url: "postgresql://llmproxy:dbpassword9090@127.0.0.1:5432/traj_proxy"
  pool:
    min_size: 50
    max_size: 100
    timeout: 60
```

归档进程配置编辑 `dockers/allinone/configs/archiver.yaml`，详见下方归档进程章节。

### 3. 启动

```bash
# 基本启动
docker run -d --name trajproxy-allinone \
    -p 12345:12345 \
    --shm-size=2g \
    traj_proxy_allinone:latest

# 自定义数据库凭证
docker run -d --name trajproxy-allinone \
    -p 12345:12345 \
    --shm-size=2g \
    -e POSTGRES_USER=myuser \
    -e POSTGRES_PASSWORD=mysecretpassword \
    -e POSTGRES_DB=litellm \
    -e TRAJ_PROXY_DB=traj_proxy \
    traj_proxy_allinone:latest

# 数据持久化（推荐生产环境）
docker run -d --name trajproxy-allinone \
    -p 12345:12345 \
    --shm-size=2g \
    -v trajproxy_postgres:/data/postgres \
    -v trajproxy_archives:/data/archives \
    -v trajproxy_logs:/app/logs \
    traj_proxy_allinone:latest
```

> **重要**：必须设置 `--shm-size=2g`（或更大），Ray 需要共享内存。默认 64MB 会导致性能严重下降。

### 4. 服务架构

All-in-One 容器内由 supervisord 管理 5 个服务：

| 服务 | 说明 | 通信方式 |
|------|------|----------|
| PostgreSQL | 数据库 | 容器内 127.0.0.1:5432 |
| LiteLLM | API 网关 | 容器内 127.0.0.1:4000 |
| TrajProxy | 主应用 | 容器内 12300+ 端口 |
| 归档进程 | 定时归档 | 容器内独立进程 |
| Nginx | 反向代理 | 对外暴露 12345 |

所有内部服务通过 localhost 通信，对外仅暴露 Nginx 端口 12345。

### 5. 环境变量

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `POSTGRES_USER` | llmproxy | PostgreSQL 用户名 |
| `POSTGRES_PASSWORD` | dbpassword9090 | PostgreSQL 密码 |
| `POSTGRES_DB` | litellm | LiteLLM 数据库名 |
| `TRAJ_PROXY_DB` | traj_proxy | TrajProxy 数据库名 |
| `DATABASE_URL` | 自动生成 | 数据库连接 URL（自动从上述变量拼接） |
| `LITELLM_MASTER_KEY` | sk-1234 | LiteLLM 管理密钥 |
| `LITELLM_SALT_KEY` | sk-1234 | LiteLLM 加密盐值 |

### 6. 验证

```bash
# 检查 Nginx 健康状态
curl http://localhost:12345/health

# 查看所有服务日志
docker logs -f trajproxy-allinone

# 进入容器查看各服务状态
docker exec trajproxy-allinone supervisorctl status
```

---

## 方式四：归档进程独立部署

归档进程与核心业务独立部署，1 个数据库实例对应 1 个归档容器。

### 1. 配置

编辑 `configs/archiver.yaml`:
```yaml
database:
  url: "postgresql://llmproxy:dbpassword9090@db:5432/traj_proxy"
  pool:
    min_size: 2
    max_size: 10
    timeout: 30
    max_idle: 900

archive:
  retention_days: 30
  poll_interval: 3600
  num_workers: 1                 # Ray Worker 进程数，默认 1（顺序）
  compress: true                 # 是否 gzip 压缩，false 则直接上传 jsonl
  storage_path: "/data/archives"
  local_temp_path: "/tmp/archives"
  # S3 模式 (可选):
  # s3:
  #   bucket: "my-bucket"
  #   prefix: "archives/"
  #   endpoint_url: null
```

### 2. 启动

```bash
# 本地存储模式
./scripts/start_docker_archiver.sh

# MinIO S3 测试模式
./scripts/start_docker_archiver.sh --test

# 停止
./scripts/stop_docker_archiver.sh [--test|--all]
```

### 3. 容器

| 容器 | 说明 |
|------|------|
| traj-archiver | 归档进程 (生产) |
| traj-archiver-minio | MinIO S3 服务 (仅测试) |

### 4. 验证

```bash
# 查看归档日志
docker logs -f traj-archiver

# 检查归档状态 (日志关键字)
docker logs traj-archiver | grep "归档任务完成"
docker logs traj-archiver | grep "runs_processed"
```

---

## 常用运维命令

### 本地开发模式

```bash
# 停止服务
Ctrl+C

# 查看日志（输出到终端）
python -m traj_proxy.app 2>&1 | tee logs/traj_proxy.log
```

### Docker 容器模式

```bash
# 使用启动脚本管理服务（推荐）
./scripts/start_docker_compose.sh start    # 启动服务
./scripts/start_docker_compose.sh stop     # 停止服务
./scripts/start_docker_compose.sh restart  # 重启服务

# 查看服务状态
cd dockers/compose && docker-compose ps

# 查看所有日志
cd dockers/compose && docker-compose logs -f

# 查看特定服务日志
cd dockers/compose && docker-compose logs -f traj_proxy
cd dockers/compose && docker-compose logs -f litellm
cd dockers/compose && docker-compose logs -f nginx
cd dockers/compose && docker-compose logs -f db

# 重启服务
cd dockers/compose && docker-compose restart

# 停止服务
cd dockers/compose && docker-compose down

# 停止并清理数据（谨慎使用）
cd dockers/compose && docker-compose down -v

# 进入容器
cd dockers/compose && docker-compose exec traj_proxy /bin/bash

# 进入数据库
cd dockers/compose && docker-compose exec db psql -U llmproxy -d traj_proxy
```

---

## 生产环境优化

### 资源配置

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

### Worker 数量调优

根据服务器配置调整 `config.yaml`：

```yaml
proxy_workers:
  count: 4  # 根据 CPU 核心数调整

ray:
  num_cpus: 8  # 实际 CPU 核心数
```

### 数据库连接池

根据并发量调整连接池大小：

```yaml
database:
  pool:
    min_size: 20   # 最小连接数
    max_size: 100  # 最大连接数
    timeout: 30    # 连接超时（秒）
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
lsof -i :12301

# 检查依赖安装
pip list | grep -E "fastapi|uvicorn|ray|psycopg"
```

#### 数据库连接失败

```bash
# 检查 PostgreSQL 是否运行
psql -h localhost -U llmproxy -d traj_proxy -c "SELECT 1"

# 检查数据库连接配置
cat dockers/compose/configs/config.yaml | grep -A5 database
```

### Docker 容器模式

#### 服务无法启动

```bash
# 检查端口占用
lsof -i :12345
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
cd dockers/compose && docker-compose ps db

# 查看数据库日志
cd dockers/compose && docker-compose logs db
```

#### Worker 无响应

```bash
# 检查 Worker 进程
cd dockers/compose && docker-compose exec traj_proxy ps aux | grep worker

# 检查 Worker 配置
cd dockers/compose && docker-compose exec traj_proxy cat /app/configs/config.yaml
```

---

## 方式五：可观测性栈独立部署

可观测性栈（Prometheus + Grafana + AlertManager + Trajectory Viewer）独立于业务服务部署，
通过 `host.docker.internal` 网络采集宿主机上 TrajProxy Workers 的 `/metrics` 指标。

> 详细运维操作见 [可观测性运维指南](observability.md)

### 1. 配置

```bash
cd dockers/observability
cp .env.example .env
```

编辑 `.env`，关键配置：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PROMETHEUS_PORT` | 19090 | Prometheus 端口（Docker Desktop 占用 9090）|
| `GRAFANA_PORT` | 3000 | Grafana 端口 |
| `WORKER_PORT_START` | 12300 | Worker 起始端口 |
| `WORKER_PORT_COUNT` | 10 | Worker 端口数量 |
| `MONITOR_NODES` | localhost | 监控节点 IP（空格分隔）|

### 2. 启动

```bash
cd dockers/observability
make start

# 或指定临时节点
make start IP="192.168.1.100 10.0.0.5"
```

### 3. 容器组说明

| 容器名 | 端口 | 说明 |
|--------|------|------|
| observability-prometheus | 19090 | 指标采集与告警评估 |
| observability-grafana | 3000 | 指标可视化 Dashboard |
| observability-alertmanager | 9093 | 告警分组与通知路由 |
| observability-viewer | 8081 | 轨迹回放页面 |

### 4. 验证

```bash
make status

# Prometheus 健康
curl http://localhost:19090/-/healthy

# Grafana Dashboard
# 浏览器访问 http://localhost:3000（admin / trajproxy）
```

### 5. 管理监控节点

```bash
make add IP=192.168.1.100      # 持久追加节点
make remove IP=192.168.1.100   # 持久移除节点
make sync                       # 重新生成 targets.json
make reload                     # 热重载 Prometheus 配置
```

---

## 配置差异对照

| 配置项 | 本地开发 | Docker Compose | All-in-One |
|--------|----------|----------------|------------|
| `url` (推理服务) | `http://localhost:1234` | `http://host.docker.internal:1234` | `http://host.docker.internal:1234` |
| `database.url` | `...localhost:5432...` | `...db:5432...` | `...127.0.0.1:5432...` |
| `ray.working_dir` | `.` | `/app` | `/app` |
| `ray.pythonpath` | `.` | `/app` | `/app` |
| `max_concurrent_requests` | 4096 | 4096 | 4096 |
| `pool.min_size` | 2 | 10 | 50 |
| `pool.max_size` | 4 | 30 | 100 |
