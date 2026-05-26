# 数据库归档机制设计文档 v2

> **导航**: [文档中心](../README.md) | [数据库设计](database.md)

版本: v2.2 | 最后更新: 2026-05-26

---

## 1. 核心设计理念

### 1.1 问题背景

请求轨迹数据的典型特征:

| 数据类型 | 增长速度 | 访问频率 | 存储占比 |
|----------|----------|----------|----------|
| 统计元数据 | 慢 | 高 | ~5% |
| 详情大字段 | 快 | 低 | ~95% |

### 1.2 设计目标

```
                    ┌─────────┐
                    │ 独立进程 │ -- traj_archiver, 与核心业务零耦合
                    └────┬────┘
               ┌─────────┴─────────┐
               │  最小化 DB 压力   │ -- 小批量 DELETE, 无大 JOIN
               └─────────┬─────────┘
          ┌──────────────┴──────────────┐
          │ 统计能力不受影响（元数据不动）│
          └──────────────┴──────────────┘
     ┌───────────────────┴───────────────────┐
     │  Ray Worker 并发架构，session 粒度分发 │
     │  协调器查询 → Worker 读/写/上传 → 清理 │
     └───────────────────────────────────────┘
```

---

## 2. 整体架构

### 2.1 独立进程架构

归档进程 `traj_archiver` 与核心业务 `traj_proxy` 完全独立，共享同一 Docker 镜像，通过启动命令区分角色:

```
核心业务容器 * N (traj-proxy)       归档容器 * 1 (traj-archiver)
  -- 仅处理推理请求                   -- 仅处理数据归档
  -- 不包含归档代码                   -- 独立配置、独立调度
  -- 零依赖 traj_archiver             -- 零依赖 traj_proxy
```

### 2.2 协调器-Worker 架构

```
协调器 (asyncio 主进程)                     Workers (Ray Actor 独立进程)
  │                                          │
  ├── 阶段1: 查询过期 run_id                  │
  ├── 阶段2: 逐 run 查询 session 列表         │
  │                                          │
  ├── 将 session 动态分发给空闲 Worker ─────────→│ 读 DB → 写文件 → 上传
  │   (完成一个就补一个，避免大 session 排队)    │ 返回 {"records", "file"}
  │                                          │
  ├── 阶段3: 收集 Worker 结果                 │
  ├── 阶段4: run 级 DELETE + UPDATE          │
  ├── 阶段5: 清理空分区                       │
```

**关键角色**:
- **协调器**: 查询过期 run → 查询 session 列表 → 动态分发 → 收集结果 → 清理 DB，只做轻量操作
- **Worker**: Ray Actor 独立进程，每次 `archive()` 新建 DB 连接，独立完成 读/写/上传 全流程

### 2.3 表职责

| 表名 | 职责 | 生命周期 | 关键字段 |
|------|------|----------|----------|
| request_metadata | 统计信息 + 分组键 | 永久保留 | run_id, session_id, archive_location |
| request_details_active | 详情大字段 | 按月分区，过期 DELETE | unique_id (FK), created_at |

### 2.4 archive_location 状态

```
写入时: archive_location = NULL (详情在 DB)
归档后: archive_location = "/data/archives/run-001/"          (本地)
         或 "s3://bucket/prefix/run-001/"                     (S3)
         或 "csb://bucket/prefix/run-001/"                    (CSB)
```

---

## 3. 分区管理

月分区保持不动（entrypoint.sh 不变），归档进程维护分区存在性:

- 容器启动时创建当月、下月、默认分区
- 每次归档前确保分区存在（`ensure_current_partition`）
- 归档后顺手 DETACH+DROP 变空的月分区

分区命名: `request_details_active_YYYY_MM`

---

## 4. 归档流程

### 4.1 归档判定: 按 run_id 粒度

一个 run 是一组相关请求的集合。只有 run 内全部记录的 MAX(created_at) 小于阈值，整个 run 才被归档:

```
run "app-001" 有 3 个 session, 15 条记录:
  sess-A: 全部 8 天前
  sess-B: 大部分 8 天前, 但 req5 只有 5 天前  -- threshold = 7 天
  -- run 整体不归档, 等 req5 也过期
```

判定 SQL:
```sql
SELECT m.run_id
FROM request_details_active d
JOIN request_metadata m ON d.unique_id = m.unique_id
WHERE m.run_id IS NOT NULL AND m.run_id != ''
GROUP BY m.run_id
HAVING MAX(d.created_at) < $threshold
```

**不归档边界**: run_id 为 NULL 或空字符串的记录不参与归档，永久保留在数据库中。

### 4.2 导出: Ray Worker 并发架构

采用 **协调器 + Ray Actor Worker** 模式，session 粒度并发处理:

```
协调器 (__main__.py + archiver.py)          Workers (session_worker.py × N)
  │                                          │
  ├── 1. 查询过期 run_id                      │
  ├── 2. 逐 run 查询 session 列表             │
  │                                          │
  ├── 初始填满: 每个 Worker 领一个 session ────→│ psycopg.connect() (新建连接)
  │                                          │── DECLARE CURSOR 流式读取
  │                                          │── 按 batch FETCH 500 行
  │                                          │── 写 .jsonl.gz 文件
  │                                          │── storage.upload() → 返回结果
  │                                          │
  ├── ray.wait() 等待第一个完成               │
  │                                          │
  ├── 该 Worker 空闲 → 分配下一个 session ─────→│ 同上，处理下一个 session
  │   (动态分发，避免大 session 排队)           │
  │                                          │
  ├── 全部 session 完成                       │
  ├── 3. 收集统计 (records, files)            │
  ├── 4. run 级 DELETE + UPDATE              │
  └── 5. 清理空分区                           │
```

**关键设计**:
- **协调器-Worker 分离**: 协调器只做查询和 cleanup（轻量），Worker 独立进程完成 读/写/上传 全流程
- **动态分发**: Worker 完成一个 session 后立即领下一个，避免大 session 占用 Worker 导致小 session 排队
- **独立 DB 连接**: Worker 每次 `archive()` 新建 `psycopg.connect()`，不共享连接池，互不干扰
- **storage 常驻复用**: Worker `__init__` 时创建 storage 实例，Actor 生命周期内复用
- **服务端游标**: DECLARE CURSOR 避免一次性加载全部数据到内存
- **NULL session 处理**: `session_id IS NULL` 使用独立 SQL 路径
- **心跳超时**: `ray.wait` 每 120 秒检查一次，防止 Worker 卡死
- **快速失败**: Worker 失败时 `ray.cancel` 清空剩余任务，中止该 run

### 4.3 清理: DELETE + UPDATE 同事务

```python
# 按 run_id 整体清理，同一事务内:
async with conn.transaction():
    DELETE FROM request_details_active
        WHERE unique_id IN (
            SELECT unique_id FROM request_metadata WHERE run_id = $run_id
        )
    UPDATE request_metadata
        SET archive_location = $loc, archived_at = NOW()
        WHERE run_id = $run_id AND archive_location IS NULL
```

### 4.4 空分区清理

归档结束后检查月分区是否为空, 空则 DETACH + DROP (零 VACUUM)。

---

## 5. 保护机制

### 归档范围保护
- 只 DELETE 详情表，元数据表永久保留
- run_id 为 NULL 或空字符串的记录不归档
- archive_location 标记归档位置

### 时间保护
- 只有 run 内全部记录的 MAX(created_at) 小于阈值才归档
- 避免部分归档导致 session 数据割裂

### 事务保护
- DELETE + UPDATE 在同事务内，原子性保证
- 崩溃恢复: 文件先上传，未提交的事务回滚后重试

### 磁盘保护
- 每个 Worker 同一时刻只处理一个 session 文件，磁盘占用 ≤ num_workers × 单个 session 文件大小
- 上传成功后立即删除本地临时文件

---

## 6. 调度器

轮询模式，固定间隔:

```yaml
archive:
  poll_interval: 3600   # 轮询间隔 (秒, 默认 1 小时)
  retention_days: 7     # 留存天数
  num_workers: 1        # Ray Worker 进程数, 默认 1 (顺序)
```

主循环: `execute() -> sleep(poll_interval) -> execute()`
失败: 等 5 分钟重试
状态查询: `get_status()` 返回运行状态、Worker 数量和统计

---

## 7. 存储

### 7.1 存储后端

三模式，配置自动选择:

| 配置 | 后端 | archive_location 格式 |
|------|------|----------------------|
| 无 s3 配置 | 本地文件 | `{run_id}/{session_id}.jsonl.gz` |
| s3.bucket 有值 (无 app_token) | S3/MinIO | `s3://{bucket}/{prefix}{run_id}/{session_id}.jsonl.gz` |
| s3.app_token 有值 | CSB 网关 | `csb://{bucket}/{prefix}{run_id}/{session_id}.jsonl.gz` |

### 7.2 文件结构

```
{run_id}/
  {session_id}.jsonl.gz    -- 该 session 的完整请求轨迹
  {session_id}.jsonl.gz
  ...
```

### 7.3 数据格式

JSONL + GZIP，每行一个请求记录:
```json
{"unique_id": "...", "messages": [...], "created_at": "2026-05-15T10:30:00", ...}
```

---

## 8. 部署

### 8.1 独立 compose 文件

```bash
# 本地存储模式
./scripts/start_docker_archiver.sh

# MinIO S3 测试模式
./scripts/start_docker_archiver.sh --test

# 停止
./scripts/stop_docker_archiver.sh [--test|--all]
```

### 8.2 配置

独立配置文件 `configs/archiver.yaml`:
```yaml
database:
  url: "postgresql://..."
archive:
  retention_days: 30
  poll_interval: 3600
  num_workers: 1              # Ray Worker 进程数，默认 1（顺序）
  compress: true              # 是否 gzip 压缩
  storage_path: "/data/archives"
  local_temp_path: "/tmp/archives"
  s3:  # 可选，不设则本地模式
    bucket: "my-bucket"
    prefix: "archives/"
    endpoint_url: null
```

### 8.3 容器组

| 容器 | 说明 |
|------|------|
| traj-archiver | 归档进程 (生产) |
| traj-archiver-test | 归档进程 + MinIO (测试) |

---

## 9. 关键文件

| 文件 | 职责 |
|------|------|
| `traj_archiver/__main__.py` | 进程入口，Ray 初始化 + Worker 创建 + 调度器启动 |
| `traj_archiver/archiver.py` | 协调器 (run 判定、动态分发 Worker、DELETE+UPDATE) |
| `traj_archiver/session_worker.py` | Ray Actor Worker (读 DB → 写文件 → 上传) |
| `traj_archiver/scheduler.py` | 轮询调度器 |
| `traj_archiver/storage.py` | 存储工厂 (本地/S3/CSB 自动选择) |
| `traj_archiver/s3_storage.py` | S3 存储实现 (boto3) |
| `traj_archiver/csb_storage.py` | CSB 网关存储实现 (原生 REST API) |
| `traj_archiver/config.py` | 独立配置加载 |
| `configs/archiver.yaml` | 归档配置文件 |
| `dockers/archiver/` | 独立 compose 部署 |
| `scripts/start_docker_archiver.sh` | 启停脚本 |
| `tests/e2e/layers/archive/` | E2E 测试 |

## 10. 版本演进

| 维度 | v1 (原设计) | v2.0 (独立进程) | v2.2 (当前: Ray Worker) |
|------|-----------|----------|----------|
| 架构 | 嵌入 traj_proxy | 独立 traj_archiver 进程 | 独立进程 + Ray Actor Workers |
| 调度 | cron (croniter) | 轮询 (poll_interval) | 轮询 (poll_interval) |
| 存储 | 仅本地 | 本地 + S3 + CSB 三模式 | 本地 + S3 + CSB 三模式 |
| 归档粒度 | 月分区 (DETACH+DROP) | run_id (DELETE+UPDATE) | run_id (DELETE+UPDATE) |
| 文件格式 | YYYY_MM.jsonl.gz | {run_id}/{session_id}.jsonl.gz | {run_id}/{session_id}.jsonl.gz |
| 导出模式 | 全量写完再上传 | 生产者-消费者 pipeline | Ray Worker 并发，动态分发 |
| 并发控制 | 无 | asyncio Queue (upload_concurrency) | Ray Actor (num_workers) |
| 磁盘保护 | 无 (可能打爆磁盘) | 有界队列 | Worker 同一时刻只处理一个 session |
| DB 连接 | 共享连接池 | 共享连接池 | 协调器用连接池，Worker 每次新建 |
| 配置 | config.yaml archive 段 | 独立 archiver.yaml | 独立 archiver.yaml |
| CLI | scripts/archive_records.py | python -m traj_archiver | python -m traj_archiver |
| 留存期 | 30 天固定 | 7/10/30 天灵活配置 | 7/10/30 天灵活配置 |
