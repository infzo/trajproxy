# 数据库归档机制设计文档 v2

> **导航**: [文档中心](../README.md) | [数据库设计](database.md)

版本: v2.0 | 最后更新: 2026-05-25

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
     │   按 run_id 组织，JSONL+GZIP, S3/本地  │
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

### 2.2 表职责

| 表名 | 职责 | 生命周期 | 关键字段 |
|------|------|----------|----------|
| request_metadata | 统计信息 + 分组键 | 永久保留 | run_id, session_id, archive_location |
| request_details_active | 详情大字段 | 按月分区，过期 DELETE | unique_id (FK), created_at |

### 2.3 archive_location 状态

```
写入时: archive_location = NULL (详情在 DB)
归档后: archive_location = "my-run/sess-123.jsonl.gz" (本地)
         或 "s3://bucket/prefix/my-run/sess-123.jsonl.gz" (S3)
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
WHERE m.run_id IS NOT NULL
GROUP BY m.run_id
HAVING MAX(d.created_at) < $threshold
```

run_id = NULL 的记录按 session 单独判定（同样逻辑，HAVING MAX < threshold）。

### 4.2 导出: 按 session 分文件

```
每个过期 run:
  1. 查询该 run 的全部记录 (JOIN metadata 获取 session_id)
  2. 按 session_id 分组, 流式写入 .jsonl.gz
  3. 最多同时打开 50 个文件句柄 (LRU 淘汰)
  4. 每个 session 文件写完后立即上传到 storage
  5. 删除本地临时文件
```

### 4.3 清理: DELETE + UPDATE 同事务

```python
# 每 500 条 unique_id 一批, 同一事务内:
DELETE FROM request_details_active WHERE unique_id = ANY($1)
UPDATE request_metadata SET archive_location = $loc, archived_at = NOW()
    WHERE unique_id = $uid
```

### 4.4 空分区清理

归档结束后检查月分区是否为空, 空则 DETACH + DROP (零 VACUUM)。

## 5. 保护机制

### 时间保护
- 只有 run 内全部记录的 MAX(created_at) 小于阈值才归档
- 避免部分归档导致 session 数据割裂

### 范围保护
- 只 DELETE 详情表, 元数据表永久保留
- archive_location 标记归档位置

### 事务保护
- DELETE + UPDATE 在同事务内, 原子性保证
- 崩溃恢复: 文件先上传, 未提交的事务回滚后重试

---

## 6. 调度器

轮询模式, 固定间隔:

```yaml
archive:
  poll_interval: 3600   # 轮询间隔 (秒, 默认 1 小时)
  retention_days: 7     # 留存天数
```

主循环: `execute() -> sleep(poll_interval) -> execute()`  
失败: 等 5 分钟重试  
状态查询: `get_status()` 返回运行状态和统计

---

## 7. 存储

### 7.1 存储后端

双模式, 配置自动选择:

| 配置 | 后端 | archive_location 格式 |
|------|------|----------------------|
| s3 为空 | 本地文件 | `{run_id}/{session_id}.jsonl.gz` |
| s3.bucket 有值 | S3/MinIO | `s3://{bucket}/{prefix}{run_id}/{session_id}.jsonl.gz` |

### 7.2 文件结构

```
{run_id}/
  {session_id}.jsonl.gz    -- 该 session 的完整请求轨迹
  {session_id}.jsonl.gz
  ...
_unknown/
  {session_id}.jsonl.gz    -- run_id=NULL 的记录
```

### 7.3 数据格式

JSONL + GZIP, 每行一个请求记录:
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
  storage_path: "/data/archives"
  s3:  # 可选, 不设则本地模式
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
| `traj_archiver/__main__.py` | 进程入口 |
| `traj_archiver/archiver.py` | 归档执行器 (run 判定、流式导出、DELETE+UPDATE) |
| `traj_archiver/scheduler.py` | 轮询调度器 |
| `traj_archiver/storage.py` | 存储抽象 (本地/S3 自动选择) |
| `traj_archiver/s3_storage.py` | S3 存储实现 |
| `traj_archiver/config.py` | 独立配置加载 |
| `configs/archiver.yaml` | 归档配置文件 |
| `dockers/archiver/` | 独立 compose 部署 |
| `scripts/start_docker_archiver.sh` | 启停脚本 |
| `tests/e2e/layers/archive/` | E2E 测试 |

## 10. 与 v1 的差异

| 维度 | v1 (原设计) | v2 (当前) |
|------|-----------|----------|
| 架构 | 嵌入 traj_proxy | 独立 traj_archiver 进程 |
| 调度 | cron (croniter) | 轮询 (poll_interval) |
| 存储 | 仅本地 | 本地 + S3 双模式 |
| 归档粒度 | 月分区 (DETACH+DROP) | run_id (DELETE+UPDATE) |
| 文件格式 | YYYY_MM.jsonl.gz | {run_id}/{session_id}.jsonl.gz |
| 配置 | config.yaml archive 段 | 独立 archiver.yaml |
| CLI | scripts/archive_records.py | 已删除, python -m traj_archiver |
| 留存期 | 30 天固定 | 7/10/30 天灵活配置 |