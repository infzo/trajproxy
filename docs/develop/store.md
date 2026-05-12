# Store 模块文档

> **导航**: [文档中心](../README.md) | [数据库设计](../design/database.md)

## 概述

`traj_proxy/store/` 是数据持久化层，负责 PostgreSQL 连接管理和数据访问。为上层提供模型配置管理和请求轨迹存储能力。

**依赖**: PostgreSQL、psycopg (async)、psycopg_pool

**相关文档**: 数据库 Schema 详见 [database.md](database.md)

---

## 一、模块结构

```
traj_proxy/store/
├── __init__.py                 # 模块导出
├── models.py                   # 数据模型定义 (dataclass)
├── database_manager.py         # 连接池管理
├── request_repository.py       # 请求轨迹 CRUD（双表事务）
├── model_repository.py         # 模型配置 CRUD
├── model_synchronizer.py       # 跨 Worker 模型同步（LISTEN/NOTIFY + 兜底轮询）
└── notification_listener.py    # LISTEN/NOTIFY 实时同步
```

---

## 二、架构概览

### 2.1 组件关系

```
ProxyWorker
  │
  ├── DatabaseManager (连接池)
  │       │
  │       ├── RequestRepository ─────► request_metadata + request_details_active
  │       │       ▲
  │       │       │ insert() / get_by_session() / get_all_by_session() / list_sessions()
  │       │       │
  │       │  TrajectoryProvider (薄封装)
  │       │       ▲
  │       │       │ get_trajectory() / list_trajectories() / get_trajectories()
  │       │       │
  │       │  Processor (Pipeline 内部调用)
  │       │
  │       ├── ModelRepository ──────► model_registry 表
  │       │       ▲
  │       │       │ register() / unregister() / get_all() / get_by_key()
  │       │       │
  │       │  ModelSynchronizer
  │       │
  │       └── NotificationListener ──► LISTEN 专用连接 (非连接池)
                ▲
                │ on_notification callback
                │
           ModelSynchronizer
```

### 2.2 组件职责

| 组件 | 职责 |
|------|------|
| `DatabaseManager` | 管理 AsyncConnectionPool 生命周期 |
| `RequestRepository` | 请求轨迹的双表事务写入和 JOIN 查询 |
| `ModelRepository` | 模型配置的 UPSERT / DELETE / 查询，含 NOTIFY |
| `ModelSynchronizer` | 跨 Worker 模型同步（封装 LISTEN/NOTIFY + 兜底轮询） |
| `NotificationListener` | 监听 `model_registry_changes` 通道，支持自动重连 |
| `ModelConfig` | 模型配置数据模型 (dataclass) |
| `RequestRecord` | 请求轨迹数据模型 (dataclass) |

### 2.3 连接管理

- **连接池**: `DatabaseManager` 持有 `AsyncConnectionPool`，所有 Repository 共享
- **专用连接**: `NotificationListener` 持有独立的 `AsyncConnection`（LISTEN 需要独占连接，autocommit 模式）
- **依赖注入**: Repository 通过构造函数接收连接池引用

---

## 三、数据模型

### 3.1 ModelConfig

```python
@dataclass
class ModelConfig:
    url: str                           # 推理服务 URL
    api_key: str                       # API 密钥
    tokenizer_path: Optional[str]      # Tokenizer 路径
    run_id: str = ""                   # 运行 ID，空字符串 = 全局模型
    model_name: str = ""               # 模型名称
    token_in_token_out: bool = False   # 是否启用 Token 模式
    tool_parser: str = ""              # 工具解析器名称
    reasoning_parser: str = ""         # 推理解析器名称
    updated_at: Optional[datetime]     # 最后更新时间
```

**复合主键**: `(run_id, model_name)`

### 3.2 RequestRecord

```python
@dataclass
class RequestRecord:
    unique_id: str                     # 全局唯一标识
    request_id: str                    # 请求 ID
    session_id: str                    # 会话 ID
    model: str                         # 模型名称
    messages: List[Any]                # 消息列表
    # ... 完整字段见 database.md
    archive_location: Optional[str]    # NULL=活跃, 非空=已归档
    archived_at: Optional[datetime]    # 归档时间
```

> **注意**: `RequestRecord` 目前仅作为数据结构定义，`RequestRepository.insert()` 直接接收 `ProcessContext` 对象。

---

## 四、API 参考

### 4.1 DatabaseManager

| 方法 | 签名 | 说明 |
|------|------|------|
| `__init__` | `(db_url: str, pool_config: Optional[dict])` | 初始化，pool_config 默认 `{min_size: 2, max_size: 20, timeout: 30}` |
| `initialize` | `async () -> None` | 创建并打开连接池 |
| `close` | `async () -> None` | 关闭连接池 |

### 4.2 RequestRepository

| 方法 | 签名 | 说明 |
|------|------|------|
| `insert` | `async (context: ProcessContext, tokenizer_path: Optional[str], run_id: Optional[str]) -> None` | 事务双写 metadata + details 表 |
| `get_by_session` | `async (session_id: str, limit: int = 100) -> List[Dict]` | JOIN 查询活跃记录，按 start_time 倒序 |
| `get_all_by_session` | `async (session_id: str) -> List[Dict]` | JOIN 查询活跃记录（无 limit） |
| `list_sessions` | `async (run_id: str) -> List[Dict]` | 按 run_id 分组查询 session 列表 |
| `get_metadata_by_session` | `async (session_id: str, limit: int = 10000) -> List[Dict]` | 轻量元数据查询（含活跃+已归档） |
| `get_statistics` | `async (model, start_time, end_time) -> Dict` | 聚合统计查询 |

**写入机制**：`insert()` 在同一事务中写入 `request_metadata` 和 `request_details_active`，任一失败则整体回滚。

**查询机制**：`get_by_session()` 通过 JOIN 查询两张表，只返回活跃（`archive_location IS NULL`）的完整记录。返回字段与旧版单表完全一致，业务代码零修改。

### 4.3 ModelRepository

| 方法 | 签名 | 说明 |
|------|------|------|
| `register` | `async (model_name, url, api_key, ...) -> ModelConfig` | UPSERT 注册模型 + NOTIFY |
| `unregister` | `async (model_name, run_id) -> bool` | 删除模型 + NOTIFY |
| `get_all` | `async () -> List[ModelConfig]` | 获取所有动态模型（表不存在时返回空列表） |
| `get_by_key` | `async (run_id, model_name) -> Optional[ModelConfig]` | 获取单个模型（增量同步用） |

### 4.4 NotificationListener

| 方法 | 签名 | 说明 |
|------|------|------|
| `__init__` | `(db_url, on_notification, reconnect_delay, max_reconnect_delay)` | 初始化，设置回调函数 |
| `start` | `async () -> None` | 启动后台 LISTEN 循环 |
| `stop` | `async () -> None` | 停止监听并关闭连接 |

**通道名**: `model_registry_changes`

**Payload 格式**:
```json
{
    "action": "register | unregister",
    "run_id": "run_001",
    "model_name": "qwen3.5-2b",
    "timestamp": 1712345678.9
}
```

### 4.5 ModelSynchronizer

| 方法 | 签名 | 说明 |
|------|------|------|
| `__init__` | `(model_registry, db_url, on_model_register, on_model_unregister, on_full_sync, ...)` | 初始化，设置回调函数和同步参数 |
| `start` | `async () -> None` | 启动同步（全量加载 + LISTEN + 兜底轮询） |
| `stop` | `async () -> None` | 停止所有同步任务 |

**初始化参数**:

| 参数 | 说明 |
|------|------|
| `model_registry` | `ModelRepository` 实例 |
| `db_url` | 数据库连接 URL（用于 LISTEN 专用连接） |
| `on_model_register` | 单个模型注册回调（`ProcessorManager.register_from_config`） |
| `on_model_unregister` | 单个模型删除回调（`ProcessorManager.unregister_by_key`） |
| `on_full_sync` | 全量同步回调（`ProcessorManager.full_sync`） |

---

## 五、配置参考

### 5.1 连接池

配置项 `database.pool`:

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `min_size` | 2 | 最小空闲连接数 |
| `max_size` | 20 | 最大连接数 |
| `timeout` | 30 | 连接获取超时（秒） |

### 5.2 归档配置

配置项 `archive`:

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | false | 是否启用自动归档 |
| `retention_days` | 30 | 活跃详情保留天数 |
| `storage_path` | "/data/archives" | JSONL+GZIP 归档文件目录 |
| `batch_size` | 1000 | 每批处理的记录数 |

### 5.3 同步参数

配置项 `processor_manager`:

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `sync_fallback_interval` | 300 | 兜底全量同步间隔（秒） |
| `sync_max_retries` | 3 | 同步失败最大重试次数 |
| `sync_retry_delay` | 5 | 重试初始延迟（秒），指数退避 |

### 5.4 LISTEN/NOTIFY

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `reconnect_delay` | 5 | LISTEN 重连初始延迟（秒） |
| `max_reconnect_delay` | 60 | LISTEN 重连最大延迟（秒） |

---

## 六、数据同步机制

### 6.1 双通道同步

```
         ┌────────── 主通道 ──────────┐
         │                            │
Worker A ──► UPSERT + NOTIFY ──► PostgreSQL ──► Worker B/C LISTEN
                                                       │
                                           ┌── 兜底通道 ──┐
                                           │              │
                                  Worker B/C 定期 get_all()
                                  对比内存 → 增删 Processor
```

- **主通道**: LISTEN/NOTIFY，延迟毫秒级
- **兜底通道**: 定期全量同步，防止 NOTIFY 丢失

### 6.2 同步流程

1. **注册**: `ModelRepository.register()` 执行 UPSERT + NOTIFY（同一事务内）
2. **监听**: `NotificationListener` 收到通知，解析 payload
3. **增量更新**: `ProcessorManager._handle_notification()` 调用 `get_by_key()` 获取最新配置，重建 Processor
4. **兜底**: `_periodic_sync()` 定期调用 `get_all()`，对比所有字段（url、api_key、tokenizer_path 等），有差异则重建

### 6.3 初始化顺序

```python
# worker.py: ProxyWorker.initialize()
db_manager = DatabaseManager(db_url, pool_config)
await db_manager.initialize()                    # 1. 打开连接池

request_repository = RequestRepository(pool)     # 2. 创建 RequestRepository
processor_manager = ProcessorManager(db_manager) # 3. 创建 ProcessorManager
trajectory_provider = TrajectoryProvider(repo)    # 4. 创建 TrajectoryProvider

model_synchronizer = ModelSynchronizer(...)       # 5. 创建 ModelSynchronizer
await model_synchronizer.start()                  # 6. 启动全量同步 + LISTEN + 兜底轮询
# 7. 注册预置模型（内存，不持久化）
```

---

## 七、集成边界

### 7.1 上游调用方

| 调用方 | 使用的组件 | 方法 |
|--------|-----------|------|
| `Processor` (Pipeline 内部) | `RequestRepository` | `insert()` |
| `ProcessorManager` | `ModelRepository` | `register()`, `unregister()`, `get_all()`, `get_by_key()` |
| `ModelSynchronizer` | `NotificationListener` | `start()`, `stop()` |
| `ModelSynchronizer` | `ModelRepository` | `get_all()`, `get_by_key()` |
| `TrajectoryProvider` | `RequestRepository` | `get_by_session()`, `get_all_by_session()`, `list_sessions()` |
| `routes.py` | `TrajectoryProvider` | `get_trajectory()`, `list_trajectories()`, `get_trajectories()` |

### 7.2 依赖方向

```
proxy_core (Processor, ProcessorManager)  ──►  store
transcript_provider                       ──►  store
workers                                   ──►  store (初始化)
```

store 层不依赖上层模块（`TYPE_CHECKING` 延迟导入 `ProcessContext` 是为类型检查服务的 workaround）。
