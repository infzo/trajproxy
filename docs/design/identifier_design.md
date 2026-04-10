# Run ID 和 Model Name 组合定义规则

> **导航**: [文档中心](../README.md) | [API 参考](../develop/api_reference.md)

本文档定义了 TrajProxy 中 `run_id`、`session_id`、model`、`request_id` 的语义规范和组合行为。

---

## 实体关系设计

```
(run_id, model_name) 组合唯一 ──► 模型实例
    │
    └── 1:n ──► session_id (会话)
                   │
                   └── 1:n ──► request_id (请求)
```

### 关系约束

| 关系 | 基数 | 说明 |
|------|------|------|
| (run_id, model_name) → 模型 | 1:1 | 组合唯一标识一个模型实例 |
| 模型 → session_id | 1:n | 一个模型服务多个会话 |
| session_id → request_id | 1:n | 一个会话包含多个请求 |

---

## ID 设计

| ID | 格式 | 唯一性 | 说明 |
|----|------|--------|------|
| (run_id, model_name) | 组合键 | UNIQUE | 模型实例唯一标识 |
| session_id | 任意字符串 | **全局唯一** | 会话标识，不可重复 |
| request_id | UUID | UNIQUE | 请求标识 |
| unique_id | `{session_id},{request_id}` | UNIQUE | 请求记录唯一标识 |

---

## 字段语义

| 字段 | 语义 | 格式约束 | 来源 | 用途 |
|------|------|----------|------|------|
| **run_id** | 运行任务标识 | 任意非逗号字符串，**可选** | 路径 > header > model参数 | 模型路由隔离 |
| **session_id** | 会话标识 | **全局唯一**，任意字符串 | 路径/Header | 轨迹分组查询 |
| **model** | 模型名称 | 任意字符串 | 请求体 | 模型类型 |
| **request_id** | 请求标识 | UUID | 服务端生成 | 单次请求追踪 |

### 核心约定

1. **run_id 可选**：不提供时为空，不设默认值
2. **session_id 全局唯一**：session_id 在整个系统内不可重复，用于唯一标识一个会话
3. **session_id 存储原始值**：不自动补充前缀
4. **run_id 独立存储**：在数据库中独立列，便于查询统计
5. **unique_id 保持不变**：`{session_id},{request_id}`

---

## 推荐使用模式

### 场景一（推荐）：model 参数携带 run-id

```bash
POST /v1/chat/completions
Headers:
  x-session-id: task-uuid    # 可选
Body:
  {"model": "qwen3.5-2b,run_001", "messages": [...]}
```

处理后：
- `run_id = run_001`（从 model 提取）
- `session_id = task-uuid`（原始值）

### 场景二：header 传递 run-id

```bash
POST /v1/chat/completions
Headers:
  x-run-id: run_001          # 可选
  x-session-id: task-uuid    # 可选
Body:
  {"model": "qwen3.5-2b", "messages": [...]}
```

处理后：
- `run_id = run_001`（从 header 获取）
- `session_id = task-uuid`（原始值）

### Nginx 对外接口

**只支持两种路径格式**：

```bash
# 格式1：有 run_id 和 session_id
POST /s/{run_id}/{session_id}/v1/chat/completions
Body:
  {"model": "qwen3.5-2b", "messages": [...]}

# 格式2：只有 session_id
POST /s/{session_id}/v1/chat/completions
Body:
  {"model": "qwen3.5-2b", "messages": [...]}
```

**路径示例**：
- `/s/run_001/task-uuid/v1/chat/completions`（run_id 和 session_id 都有值）
- `/s/task-uuid/v1/chat/completions`（只有 session_id）

---

## run_id 提取优先级

```
优先级1（最高）: 路径参数 /s/{run_id}/{session_id}/v1/...
优先级2: x-run-id Header
优先级3: model 参数逗号后
       model: "gpt-4,run_002" → run_id = "run_002"
优先级4（最低）: 空（不设默认值）
```

## session_id 提取优先级

```
优先级1（最高）: 路径参数 /s/{run_id}/{session_id}/v1/... 或 /s/{session_id}/v1/...
优先级2: x-session-id Header
优先级3: x-sandbox-traj-id Header
优先级4（最低）: 空
```

**注意**：session_id 必须全局唯一，不可重复使用。

---

## 数据库设计

### model_registry 表

| 字段 | 说明 |
|------|------|
| `run_id` | 运行任务ID |
| `model_name` | 模型名称 |
| **约束** | `UNIQUE (run_id, model_name)` |

### request_metadata 表

| 字段 | 存储内容 | 示例 |
|------|----------|------|
| `unique_id` | `{session_id},{request_id}` | `task-123,req-uuid` |
| `session_id` | 原始 session_id | `task-123` |
| `run_id` | 运行ID（独立存储） | `run_001` |
| `model` | 模型名称 | `gpt-4` |

### 表关联

```
┌─────────────────────────┐
│     model_registry      │
│  UNIQUE(run_id, model)  │
└───────────┬─────────────┘
            │ 1:n
            ▼
┌─────────────────────────┐
│    request_metadata     │
│  unique_id (UNIQUE)     │
│  run_id                 │
│  session_id             │
│  model                  │
└───────────┬─────────────┘
            │ 1:1
            ▼
┌─────────────────────────┐
│ request_details_active  │
│  unique_id (FK)         │
└─────────────────────────┘
```

---

## 查询示例

```sql
-- 查询特定模型实例的所有请求
SELECT * FROM request_metadata
WHERE run_id = 'run_001' AND model = 'gpt-4';

-- 查询特定会话的所有请求
SELECT * FROM request_metadata
WHERE session_id = 'task-123';

-- 统计各模型的请求数
SELECT run_id, model, COUNT(*) as request_count
FROM request_metadata
GROUP BY run_id, model;

-- 按 run_id 统计
SELECT run_id, COUNT(*) as request_count
FROM request_metadata
GROUP BY run_id;
```

---

## 变更历史

| 日期 | 版本 | 变更说明 |
|------|------|----------|
| 2026-04-10 | v7.0 | 明确实体关系：模型→会话→请求 层级结构；unique_id 保持 `{session_id},{request_id}` |
| 2026-04-10 | v6.0 | session_id 不再自动补充 run_id 前缀，存储原始值；数据库新增独立 run_id 列 |
