# Store 模块

> 数据存储层。封装所有持久化访问：PostgreSQL 数据库 + 外部对象存储（Blob）。
> 业务层（Pipeline / Processor）**禁止**绕过本层直接写 SQL 或访问存储。
> 对应全局规则「Store 隔离：数据库操作必须通过 store/ 层 Repository 类」。

---

## 一、功能边界

**对外提供：**
- PostgreSQL 连接池管理（`DatabaseManager`）
- 各业务表的 Repository（CRUD，参数化 SQL）
- `route_experts` 大字段卸载链路（Decorator + 引用表 Repository + Blob 存储）
- 模型配置实时同步（LISTEN/NOTIFY + 定期兜底）
- 数据模型 dataclass（`ModelConfig` / `RequestRecord`）

**不负责：**
- 表 / 索引 DDL —— 已迁移至 `scripts/init_db.py`（集群部署前一次性执行，权威 DDL 源）
- 业务编排 —— 由 `Pipeline` / `workers/worker.py` 负责
- 归档清理调度 —— 由 `traj_archiver` / worker 负责
- TTL 清理执行 —— `R3RefRepository` 仅返回过期候选，不执行删除

---

## 二、目录结构与组件分类

**注意：`store/` 含三类职责不同的组件，勿按文件名一概而论。**
判断某文件是否"直接访问 DB"的硬标准：是否在 `__init__` 注入 `AsyncConnectionPool` 并自行执行 raw SQL。

### A. 真 Repository（注入 `AsyncConnectionPool`，直接 raw SQL + `dict_row`）

| 文件 | 类 | 目标表 | 职责 |
|---|---|---|---|
| `request_repository.py` | `RequestRepository` | `request_metadata`、`request_details_active` | 请求轨迹 CRUD；方案二架构（元数据长期保留 + 详情表近期大字段）；支持 compact 精简模式（`COMPACT_SKIP_FIELDS`） |
| `model_repository.py` | `ModelRepository` | `model_registry` | 模型配置 CRUD（`register` 用 UPSERT） |
| `r3_ref_repository.py` | `R3RefRepository` | `r3_blob_refs` | `route_experts` 引用表 CRUD；状态机 `uploading → ready`；多租户授权键 `session_id` |

**共同约定：**
- `__init__` 注入 `pool`（由 `DatabaseManager.pool` 传入）
- `async with self.pool.connection()` + `async with conn.transaction()`
- 全部 SQL 使用参数化占位符 `%s`，**禁止字符串拼接**
- 异常统一抛 `DatabaseError`（`traj_proxy.exceptions`）

### B. Decorator（透明增强 Repository，**不直接访问 DB**）

| 文件 | 类 | 说明 |
|---|---|---|
| `decorators/offloading_repository.py` | `OffloadingRepository` | 包裹 `RequestRepository`，把 `route_experts` 字段卸载到 Blob 存储 |

**命名说明（重要）：** `OffloadingRepository` 命名含 "Repository" 是因其**对外实现 Repository 接口**（duck-type 透明替换，对下游 `Pipeline`/`Processor` 无感）。但其本质是 **Decorator / Coordinator**：
- **不持有** `AsyncConnectionPool`，**不写 SQL**，全文无直接 DB 访问；
- 通过委托 `inner`（`RequestRepository`）+ `ref_repo`（`R3RefRepository`）间接完成所有 DB 操作；
- 其余公共方法经 `__getattr__` 透明委托给 `inner`；
- 关闭卸载特性时，worker 直接使用裸 `RequestRepository`，本类零参与。

读代码时请勿按 A 类 Repository 的约定（注入 pool + raw SQL）去预期它。

### C. 对象存储（非 DB）

| 文件 | 类 / 函数 | 说明 |
|---|---|---|
| `blob_storage.py` | `BlobStorage`（ABC）、`CSBBlobStorage`、`LocalDiskBlobStorage`、`create_blob_storage`、`StreamHandle` | `route_experts` blob 的上传 / 流式读取 / 删除 / 存在性检查 |

- **不碰 DB**，不负责 TTL 清理、引用表 CRUD、marker 构造（后者由 `OffloadingRepository` 负责）；
- `open_stream` 为 async，所有 I/O 启动在返回前 await 完成，失败同步抛 `BlobStorageError`（→ HTTP 502）；
- `StreamHandle.iter_bytes` 只 yield 字节块，不再启动 I/O；`close` 幂等。

### D. 基础设施与同步

| 文件 | 类 | 职责 |
|---|---|---|
| `database_manager.py` | `DatabaseManager` | `AsyncConnectionPool` 管理 + 连接池监控指标上报；DDL 已外迁 |
| `model_synchronizer.py` | `ModelSynchronizer` | DB → 内存模型配置同步（LISTEN/NOTIFY 实时 + 定期兜底），回调 `ProcessorManager`；通过 `ModelRepository` 间接访问 DB，不直接写 SQL |
| `notification_listener.py` | `NotificationListener` | PG LISTEN 专用独占连接（**不用连接池**，因 LISTEN 需持久连接）；频道 `model_registry_changes` |
| `models.py` | `ModelConfig`、`RequestRecord` | dataclass，分别映射 `model_registry` / 请求轨迹逻辑记录 |

---

## 三、对外接口（概览，详见各文件 docstring）

- `DatabaseManager`：`initialize()` / `pool` / 连接池监控 / `close()`
- `RequestRepository`：`insert()` / `get_record_detail()` / `get_by_session()` / `list_records()` / `get_prefix_candidates()`
- `ModelRepository`：`register()` / 查询 / 列举 / 删除
- `R3RefRepository`：`insert_ref()` / `get_for_fetch()` / `mark_ready()` / `mark_consumed()`（预留）/ `fetch_expired_for_cleanup()`（预留）
- `BlobStorage`：`put()` / `open_stream()` / `delete()` / `exists()` / `aclose()`；`StreamHandle`：`iter_bytes()` / `close()`
- `OffloadingRepository`：`insert()` / `aclose()`（其余经 `__getattr__` 委托 `inner`）
- `ModelSynchronizer`：`start()` / `stop()`
- `NotificationListener`：`start()` / `stop()`

`store/__init__.py` 当前仅导出：`DatabaseManager`、`RequestRepository`、`ModelRepository`、`NotificationListener`、`R3RefRepository`、`ModelConfig`、`RequestRecord`。
`BlobStorage` 经工厂 `create_blob_storage` 注入，未在 `store/__init__.py` 的 `__all__` 中；`OffloadingRepository` 经 `store/decorators` 子包导出（`from traj_proxy.store.decorators import OffloadingRepository`），由 worker 装配注入。

---

## 四、依赖关系

**内部依赖（store 内）：**
```
OffloadingRepository ──► RequestRepository (inner)
                    └──► R3RefRepository (ref_repo)
                    └──► BlobStorage (blob)
ModelSynchronizer ──► ModelRepository
                  └──► NotificationListener (LISTEN 连接)
ModelRepository   ──► models.ModelConfig
                  └──► notification_listener.CHANNEL
```

**外部依赖：**
- `psycopg` / `psycopg_pool`（PG 异步驱动 + 连接池）
- `httpx`（`CSBBlobStorage` 的 HTTP 客户端）
- `traj_proxy.exceptions`（`DatabaseError` / `ProxyCoreError` / `BlobStorageError`）
- `traj_proxy.utils.logger`（统一日志，**禁止 print**）
- `traj_proxy.observability.metrics_collector`（连接池监控指标）
- `traj_proxy.utils.config`（同步重试 / 兜底间隔等配置）
- 设计上与 `traj_archiver` 解耦，`CSBBlobStorage` 三步认证对齐 `traj_archiver/csb_storage.py` 但为独立实现

**被依赖方：** `workers/worker.py`（装配）、`proxy_core/`（经 worker 注入的 Repository 接口使用）、`serve/`（轨迹查询 Provider 调 Repository）。

---

## 五、装配方式（`workers/worker.py:400-454`）

1. `DatabaseManager(db_url, pool_config)` → `initialize()` → 取 `pool`；
2. `RequestRepository(pool, storage_mode)` 作为基础 inner；
3. 若 `route_experts_offload.enabled`：
   - `create_blob_storage(r3_config)` → `BlobStorage` 实例；
   - `R3RefRepository(pool)`；
   - `OffloadingRepository(inner=..., blob=..., ref_repo=..., backend=..., marker_config=..., ttl_hours=..., blob_key_prefix=...)` 包裹 inner；
   - 结果类型 `Union[RequestRepository, OffloadingRepository]`，对下游透明；
4. worker 持有 `self.offloading_repo` 引用，`shutdown` 时先于 inner 调 `aclose()`（取消并等待后台上传 task，防孤儿化）。

---

## 六、架构约束（强制，对齐 `.opencode/instructions/global-rules.md`）

1. **Store 隔离**：所有 DB 操作必须经本层 Repository 类，业务层禁止直接写 SQL。
2. **三类组件不得混淆**：
   - 真 Repository（A 类）= 注入 pool + raw SQL；
   - `OffloadingRepository`（B 类）= Decorator，**不直接访问 DB**，只编排委托；
   - `BlobStorage`（C 类）= 对象存储，非 DB。
   新增存储组件时先判定归属，命名应反映职责（避免非 Repository 冠以 Repository 之名）。
3. **参数化 SQL**：禁止字符串拼接用户输入到 SQL，防注入。
4. **多租户授权**：`R3RefRepository.get_for_fetch` 按 `(session_id, request_id)` 复合查询，防跨租户读取。
5. **日志脱敏**：禁止记录完整用户请求内容与模型响应原文。
6. **幂等性**：`insert_ref` 用 `ON CONFLICT DO NOTHING`；`StreamHandle.close` 幂等。
7. **失败安全**：`OffloadingRepository.insert` 保证 `insert_ref` 失败 → 不 strip → 退化原行为（PG 存大字段，无数据丢失）。

---

## 七、数据库表概览（DDL 详见 `scripts/init_db.py`）

| 表 | 管理者 | 用途 |
|---|---|---|
| `request_metadata` | `RequestRepository` | 请求轨迹元数据（长期保留，统计信息） |
| `request_details_active` | `RequestRepository` | 请求轨迹详情（近期大字段，归档后迁移） |
| `model_registry` | `ModelRepository` | 模型配置注册表 |
| `r3_blob_refs` | `R3RefRepository` | `route_experts` 卸载引用表（状态机 + TTL） |

> 表/索引的创建与变更一律在 `scripts/init_db.py` 中维护，禁止在运行时代码里执行 DDL。
