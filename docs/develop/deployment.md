# 部署指南

> **导航**: [文档中心](../README.md) | [开发环境](development.md) | [配置说明](configuration.md)

TrajProxy 支持两种部署方式：本地开发模式和 Docker 容器化部署。

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

编辑 `configs/config.yaml`：

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

编辑 `configs/config.yaml`：

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
# 一键启动所有容器
./scripts/docker-compose/start.sh

# 或手动启动
cd dockers && docker-compose up -d --build
```

### 3. 容器组说明

启动后包含以下容器：

| 容器名 | 端口 | 说明 |
|--------|------|------|
| nginx | 12345 | 统一入口网关 |
| litellm | 4000 | API 网关 |
| db | 5432 | PostgreSQL 数据库 |
| traj_proxy | 12300-12320 | ProxyWorkers |
| prometheus | 9090 | 监控服务 |

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
# 查看服务状态
cd dockers && docker-compose ps

# 查看所有日志
cd dockers && docker-compose logs -f

# 查看特定服务日志
cd dockers && docker-compose logs -f traj_proxy
cd dockers && docker-compose logs -f litellm
cd dockers && docker-compose logs -f nginx
cd dockers && docker-compose logs -f db

# 重启服务
cd dockers && docker-compose restart

# 停止服务
cd dockers && docker-compose down

# 停止并清理数据（谨慎使用）
cd dockers && docker-compose down -v

# 进入容器
cd dockers && docker-compose exec traj_proxy /bin/bash

# 进入数据库
cd dockers && docker-compose exec db psql -U llmproxy -d traj_proxy
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
cat configs/config.yaml | grep -A5 database
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
cd dockers && docker-compose ps db

# 查看数据库日志
cd dockers && docker-compose logs db
```

#### Worker 无响应

```bash
# 检查 Worker 进程
cd dockers && docker-compose exec traj_proxy ps aux | grep worker

# 检查 Worker 配置
cd dockers && docker-compose exec traj_proxy cat /app/configs/config.yaml
```

---

## 配置差异对照

| 配置项 | 本地开发 | Docker 部署 |
|--------|----------|-------------|
| `url` (推理服务) | `http://localhost:1234` | `http://host.docker.internal:1234` |
| `database.url` | `...localhost:5432...` | `...db:5432...` |
| `ray.working_dir` | `.` | `/app` |
| `ray.pythonpath` | `.` | `/app` |
