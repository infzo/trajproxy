# 配置详解

> **导航**: [文档中心](../README.md) | [部署指南](deployment.md) | [API 概览](../api/overview.md)

TrajProxy 使用 YAML 格式的配置文件，默认位于 `dockers/compose/configs/config.yaml`（Docker Compose 部署）或 `dockers/allinone/configs/config.yaml`（All-in-One 部署）。

---

## 配置文件位置

配置文件按以下顺序查找：

1. **环境变量指定**: `TRAJ_PROXY_CONFIG` 环境变量
2. **默认路径**: `dockers/compose/configs/config.yaml`

```bash
# 使用环境变量指定配置文件
export TRAJ_PROXY_CONFIG=/path/to/custom-config.yaml
```

---

## 完整配置示例

```yaml
# 基础路径配置
models_dir: /app/models               # models 目录路径，用于 tokenizer 加载
custom_parsers_dir: /app/custom_parsers  # 自定义 parser 目录路径

# ProxyWorker 配置
proxy_workers:
  count: 2                    # Worker 数量
  base_port: 12300            # 起始端口（12300, 12301）
  max_concurrent_requests: 4096  # 单 Worker 最大并发请求数（超限返回 429）
  semaphore_acquire_timeout: 5.0  # 信号量获取超时（秒），超时返回 429
  models:                     # 预置模型配置
    - model_name: qwen3.5-2b
      url: http://host.docker.internal:8000/v1
      api_key: sk-1234
    - model_name: qwen3.5-2b
      run_id: app-001
      url: http://host.docker.internal:8000/v1
      api_key: sk-1234
      tokenizer_path: Qwen/Qwen3.5-2B
      token_in_token_out: true
      tool_parser: "qwen3_coder"
      reasoning_parser: "qwen3"

# Ray 配置
ray:
  num_cpus: 4                 # CPU 核心数
  working_dir: "/app"         # 默认容器内路径，可通过环境变量 RAY_WORKING_DIR 覆盖
  pythonpath: "/app"          # 默认容器内 PYTHONPATH，可通过环境变量 RAY_PYTHONPATH 覆盖

# 数据库配置
database:
  url: "postgresql://llmproxy:dbpassword9090@db:5432/traj_proxy"
  pool:
    min_size: 2               # 最小连接数
    max_size: 4               # 最大连接数
    timeout: 60               # 连接超时时间（秒）

# ProcessorManager 配置
processor_manager:
  sync_fallback_interval: 300   # 兜底全量同步间隔（秒）
  sync_max_retries: 3           # 同步失败最大重试次数
  sync_retry_delay: 5           # 同步重试初始延迟（秒）
  listen_reconnect_delay: 5     # LISTEN 连接重连初始延迟（秒）
  listen_max_reconnect_delay: 60  # LISTEN 连接重连最大延迟（秒）
  processor_cache_max_size: 32    # LRU 缓存最大 Processor 数（per worker）
  processor_idle_timeout: 300     # 空闲超时（秒），超时未访问的 Processor 自动淘汰

# InferClient 配置
infer_client:
  connect_timeout: 10       # 连接超时（秒）
  read_timeout: 600         # 读取超时（秒）
  max_connections: 0        # HTTP 连接池上限（0 = 不限制，每个 InferClient）
  max_retries: 2            # 请求失败重试次数（针对 502/503/504 错误）
```

> **注意**：上述示例取自 `configs/config.yaml`（项目根配置）。Docker 部署配置（`dockers/compose/` 和 `dockers/allinone/`）中 `infer_client.connect_timeout` 为 60、`max_connections` 为 1000。其他部署模式差异详见 [部署模式配置差异](#部署模式配置差异)。

---

## 配置项详解

### 基础路径配置

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `models_dir` | string | /app/models | models 目录路径，用于 tokenizer 加载 |
| `custom_parsers_dir` | string | /app/custom_parsers | 自定义 parser 目录路径 |

**本地开发配置：**

```yaml
models_dir: ./models
custom_parsers_dir: ./custom_parsers
```

**Docker 部署配置：**

```yaml
models_dir: /app/models
custom_parsers_dir: /app/custom_parsers
```

**custom_parsers_dir 目录结构：**

```
custom_parsers/
  tool_parsers/           # 自定义工具解析器目录
    my_tool_parser.py     # 文件名即为 parser 名称
  reasoning_parsers/      # 自定义推理解析器目录
    my_reasoning_parser.py
```

详见 [parser.md](../design/modules/parser.md#十二自定义-parser-按需发现机制)。

---

### proxy_workers

Worker 进程配置。

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `count` | int | 2 | Worker 实例数量 |
| `base_port` | int | 12300 | 起始端口号，后续 Worker 递增 |
| `max_concurrent_requests` | int | 4096 | 单 Worker 最大并发请求数，超限返回 429（代码 fallback: 128） |
| `semaphore_acquire_timeout` | float | 5.0 | 信号量获取超时（秒），超时返回 429（代码 fallback: 5.0） |
| `models` | list | [] | 预置模型配置列表 |

> **注意**：`max_concurrent_requests` 所有部署模式统一为 4096。代码 fallback 为 128。

#### models 配置

每个模型的配置项：

| 配置项 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| `model_name` | string | 是 | - | 模型名称，请求时的标识 |
| `run_id` | string | 否 | - | 运行标识，区分同一模型的不同部署实例 |
| `url` | string | 是 | - | 推理服务 URL |
| `api_key` | string | 是 | - | API 密钥 |
| `tokenizer_path` | string | 否 | - | Tokenizer 路径（本地路径或 HuggingFace 名称） |
| `token_in_token_out` | bool | 否 | false | 是否启用 Token-in-Token-out 模式 |
| `tool_parser` | string | 否 | "" | 工具解析器名称 |
| `reasoning_parser` | string | 否 | "" | 推理解析器名称 |

**模式选择：**

- `token_in_token_out: true` - 需要 `tokenizer_path`，支持前缀匹配缓存
- `token_in_token_out: false` - 直接转发模式，无需 tokenizer

---

### ray

Ray 分布式框架配置。

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `num_cpus` | int | 4 | 分配的 CPU 核心数 |
| `working_dir` | string | "/app" | Ray 工作目录 |
| `pythonpath` | string | "/app" | Python 模块搜索路径 |

**环境变量覆盖：**

| 环境变量 | 对应配置 |
|----------|----------|
| `RAY_WORKING_DIR` | `ray.working_dir` |
| `RAY_PYTHONPATH` | `ray.pythonpath` |

---

### database

数据库连接配置。

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `url` | string | - | PostgreSQL 连接 URL |
| `pool.min_size` | int | 2 | 连接池最小连接数（代码 fallback: 2） |
| `pool.max_size` | int | 4 | 连接池最大连接数（代码 fallback: 20） |
| `pool.timeout` | int | 60 | 连接超时时间（秒）（代码 fallback: 30） |

> **注意**：表中默认值为 `configs/config.yaml` 的 shipped 值。代码 fallback 值仅在配置文件未设置时生效。Docker Compose 部署的 `dockers/compose/configs/config.yaml` 中 pool 值为 `min_size:10, max_size:30, timeout:60`；All-in-One 部署为 `min_size:50, max_size:100, timeout:60`。

**URL 格式：**

```
postgresql://用户名:密码@主机:端口/数据库名
```

**本地开发：**

```yaml
database:
  url: "postgresql://llmproxy:dbpassword9090@localhost:5432/traj_proxy"
```

**Docker 部署：**

```yaml
database:
  url: "postgresql://llmproxy:dbpassword9090@db:5432/traj_proxy"
```

---

### archive

归档配置，控制过期详情数据的自动归档行为。

> **注意**：`configs/config.yaml`（项目根配置）不包含 `archive` 段。归档功能有两种配置方式：
> - **Docker Compose / All-in-One 部署**：在 `dockers/compose/configs/config.yaml` 或 `dockers/allinone/configs/config.yaml` 中包含 `archive` 段，但此段仅作为参考，实际归档进程使用独立的 `configs/archiver.yaml`。
> - **独立归档进程部署**：使用专门的 `configs/archiver.yaml`，这是归档进程的实际配置来源。
>
> 详情见 [数据库设计](../design/data/schema.md) 中的归档机制，以及 [部署指南](deployment.md) 中的归档进程部署。

**Docker Compose 部署的 config.yaml 中的 archive 段（仅供参考）：**

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `enabled` | bool | false | 是否启用自动归档（Docker Compose 中默认 true） |
| `retention_days` | int | 30 | 活跃详情保留天数 |
| `storage_path` | string | "/data/archives" | JSONL+GZIP 归档文件目录 |
| `batch_size` | int | 1000 | 每批处理的记录数 |
| `schedule` | string | "0 2 * * *" | cron 表达式，控制归档执行时间 |
| `timezone` | string | "Asia/Shanghai" | 时区配置 |

**独立归档进程配置（configs/archiver.yaml）详见 [部署指南](deployment.md#方式三归档进程独立部署)。**

**自动归档说明：**

当 `enabled: true` 时，应用启动后自动创建归档调度器：
- 根据 cron 表达式定时执行归档任务
- 应用内调度，不依赖系统 cron
- 归档过期分区（分区上界 < retention_days 阈值）
- 导出 JSONL+GZIP 文件后 DETACH + DROP 分区

**cron 表达式示例：**

| 表达式 | 说明 |
|--------|------|
| `0 2 * * *` | 每天凌晨 2 点 |
| `0 */6 * * *` | 每 6 小时 |
| `0 3 * * 0` | 每周日凌晨 3 点 |

---

### processor_manager

ProcessorManager 同步配置，控制模型配置的跨 Worker 同步。

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `sync_fallback_interval` | int | 300 | 兜底全量同步间隔（秒） |
| `sync_max_retries` | int | 3 | 同步失败最大重试次数 |
| `sync_retry_delay` | int | 5 | 同步重试初始延迟（秒），指数退避 |
| `listen_reconnect_delay` | float | 5.0 | LISTEN 连接重连初始延迟（秒） |
| `listen_max_reconnect_delay` | float | 60.0 | LISTEN 连接重连最大延迟（秒） |
| `processor_cache_max_size` | int | 32 | LRU 缓存最大 Processor 数（per worker）（代码 fallback: 32） |
| `processor_idle_timeout` | int | 300 | 空闲超时（秒），超时未访问的 Processor 自动淘汰，0 = 禁用（代码 fallback: 300） |

**同步机制说明：**

1. **LISTEN/NOTIFY（主通道）**：实时监听数据库通知，延迟毫秒级
2. **兜底轮询（备用）**：定期全量同步，防止通知丢失

---

### infer_client

InferClient 超时配置，控制 TrajProxy 到推理服务的 HTTP 请求超时。

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `connect_timeout` | int | 10 | 连接超时（秒）（代码 fallback: 60） |
| `read_timeout` | int | 600 | 读取超时（秒） |
| `max_connections` | int | 0 | HTTP 连接池上限（0 = 不限制）（代码 fallback: 1000） |
| `max_retries` | int | 2 | 请求失败重试次数（针对 502/503/504 错误） |

**端到端超时链路：**

```
Client → Nginx → LiteLLM → TrajProxy → Infer
         │         │          │          │
         └─ 600s ──┴── 600s ──┴── 600s ──┘
```

---

## 环境变量汇总

| 环境变量 | 说明 |
|----------|------|
| `TRAJ_PROXY_CONFIG` | 配置文件路径 |
| `RAY_WORKING_DIR` | Ray 工作目录 |
| `RAY_PYTHONPATH` | Python 模块路径 |
| `DATABASE_URL` | 数据库连接 URL（仅用于 Docker Compose 环境传递给 LiteLLM 和归档进程；`traj_proxy` 不读取此变量，始终使用 `database.url` 配置项） |
| `ARCHIVER_CONFIG` | 归档进程配置文件路径（仅归档进程使用） |

---

## 本地开发 vs Docker 部署

### 本地开发配置

```yaml
proxy_workers:
  models:
    - model_name: qwen3.5-2b
      url: http://localhost:8000  # 本地推理服务

ray:
  working_dir: "."
  pythonpath: "."

database:
  url: "postgresql://llmproxy:dbpassword9090@localhost:5432/traj_proxy"
```

### Docker 部署配置

```yaml
proxy_workers:
  models:
    - model_name: qwen3.5-2b
      url: http://host.docker.internal:8000  # 宿主机推理服务

ray:
  working_dir: "/app"
  pythonpath: "/app"

database:
  url: "postgresql://llmproxy:dbpassword9090@db:5432/traj_proxy"
```

---

## 配置加载代码

配置通过 `traj_proxy/utils/config.py` 加载：

```python
from traj_proxy.utils.config import load_config, get_config

# 加载配置（带缓存）
config = load_config()

# 获取特定配置
from traj_proxy.utils.config import (
    get_proxy_workers_config,
    get_database_config,
    get_database_pool_config,
    get_processor_manager_config,
    get_infer_client_config,
    get_models_dir,
    get_custom_parsers_dir,
    get_max_concurrent_requests,
    get_semaphore_acquire_timeout,
    get_processor_cache_max_size,
    get_processor_idle_timeout,
)

proxy_config = get_proxy_workers_config()
db_config = get_database_config()
pool_config = get_database_pool_config()
infer_config = get_infer_client_config()
```

**归档配置**独立于主配置，通过 `traj_archiver/config.py` 加载：

```python
from traj_archiver.config import get_archive_config, get_database_pool_config

archive_config = get_archive_config()
archiver_pool_config = get_database_pool_config()
```

---

## 部署模式配置差异

不同部署模式下，部分配置项的推荐值存在差异：

| 配置项 | 本地开发 (`configs/`) | Docker Compose (`dockers/compose/`) | All-in-One (`dockers/allinone/`) |
|--------|----------------------|-------------------------------------|----------------------------------|
| `proxy_workers.max_concurrent_requests` | 4096 | 4096 | 4096 |
| `database.url` | `@db:5432` | `@db:5432` | `@127.0.0.1:5432` |
| `database.pool.min_size` | 2 | 10 | 50 |
| `database.pool.max_size` | 4 | 30 | 100 |
| `infer_client.connect_timeout` | 10 | 60 | 60 |
| `infer_client.max_connections` | 0（不限） | 1000 | 1000 |
| `infer_client.max_retries` | 2 | _(未配置)_ | _(未配置)_ |
| `archive.enabled` | _(未配置)_ | true | false |
| `processor_manager.processor_idle_timeout` | 300 | _(未配置，默认 300)_ | _(未配置，默认 300)_ |

> **说明**：All-in-One 模式因所有服务共享单机资源，连接池配置更激进以减少连接建立开销；Docker Compose 模式下各服务独立容器，连接池适中。本地开发以低资源占用为主。
