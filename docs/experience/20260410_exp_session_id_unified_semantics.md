# Session ID 统一语义规范重构

> **文档类型**: 架构演进
> **日期**: 2026-04-10
> **影响范围**: API 接口、路径解析、模型路由

---

## 问题背景

TrajProxy 中存在多个标识字段（`run_id`、`session_id`、`model`、`request_id`），长期以来语义混淆：

### 变更前的问题

| 问题 | 描述 |
|------|------|
| **语义混淆** | `session_id` 既想作为轨迹分组标识，又想从中提取 `run_id` |
| **格式限制** | 文档定义 `session_id` 为三段格式 `{run_id},{sample_id},{task_id}`，但 Pangu 接口使用 UUID |
| **来源不一致** | `run_id` 有时从 `model` 参数提取，有时从 `session_id` 提取 |
| **兼容困境** | 新旧接口格式不一，需要一种方式同时兼容 Pangu 模式和三段格式模式 |

---

## 统一语义定义

### 核心原则

**各字段各司其职，互不耦合**：

| 字段 | 语义 | 格式 | 来源 | 用途 |
|------|------|------|------|------|
| **run_id** | 租户标识 | 任意非逗号字符串 | 路径 > model参数 > DEFAULT | 模型路由隔离 |
| **request_id** | 请求标识 | UUID | 服务端生成 | 单次请求追踪 |
| **session_id** | 会话标识 | **任意字符串（不定义分割格式）** | 路径/Header | 轨迹分组查询 |
| **model** | 模型名称 | 任意字符串 | 请求体 | 模型类型 |

### 关键决策

1. **session_id 解耦**：不再从中提取 `run_id`，格式完全由客户端自定义
2. **路径参数增强**：支持 `/s/{run_id},{session_id}/` 格式，实现完全兼容
3. **优先级明确**：路径参数 > model 参数 > 默认值

---

## 解决方案

### 路径格式设计

```
/s/{session_id}/v1/chat/completions          # 格式1：仅 session_id（run_id 从 header/model 获取）
/s/{run_id}/{session_id}/v1/chat/completions # 格式2：run_id + session_id
```

**解析规则**：
- 路径有两个段 → 第一个为 `run_id`，第二个为 `session_id`
- 路径只有一个段 → 整体为 `session_id`，`run_id` 从 header 或 model 参数提取

**重要**：session_id 必须全局唯一，不可重复使用。

### 兼容性矩阵

| 场景 | 请求示例 | run_id | session_id | 兼容情况 |
|------|----------|--------|------------|----------|
| Pangu 原版 | `POST /v1/chat/completions`<br>model: `gpt-4,app_001`<br>header: `x-session-id: uuid` | app_001 (model) | uuid (header) | ✅ 无影响 |
| Pangu 路径式 | `POST /s/uuid/v1/chat/completions`<br>model: `gpt-4,app_001` | app_001 (model) | uuid (路径) | ✅ 无影响 |
| 新版明确指定 | `POST /s/run_001/uuid/v1/chat/completions`<br>model: `gpt-4` | run_001 (路径) | uuid (路径) | ✅ 新功能 |
| 仅 session | `POST /s/uuid/v1/chat/completions`<br>model: `gpt-4` | 无 (None) | uuid (路径) | ✅ 兼容 |

---

## 代码修改

### Nginx 配置

**文件**: `configs/nginx.conf`, `configs/nginx_allinone.conf`

```nginx
# 变更前
proxy_set_header x-session-id $1;

# 变更后
proxy_set_header x-path-session $1;
```

将路径段完整传递给后端解析，不再在 nginx 层面假设格式。

### 路径解析函数

**文件**: `traj_proxy/serve/routes.py`

```python
def _parse_path_session(path_segment: str) -> tuple:
    """
    解析路径中的 session 参数

    路径格式：
    - "uuid-string" → (None, "uuid-string")  # 无 run_id
    - "run_001,uuid-string" → ("run_001", "uuid-string")  # 有 run_id

    Returns:
        (run_id, session_id): run_id 可能为 None
    """
    if ',' in path_segment:
        parts = path_segment.split(',', 1)
        return parts[0].strip(), parts[1].strip()
    return None, path_segment
```

### 模型解析函数

**文件**: `traj_proxy/serve/routes.py`

```python
def _parse_model_and_run_id(model: str, run_id_from_path: str = None) -> tuple:
    """
    解析 model 参数和 run_id

    优先级：
    1. 路径参数中的 run_id（最高优先级）
    2. model 参数中的 run_id（格式：model_name,run_id）
    3. DEFAULT（默认值）
    """
    # 优先级1: 路径中的 run_id
    if run_id_from_path:
        return model.strip(), run_id_from_path

    # 优先级2: model 参数中的 run_id
    if ',' in model:
        parts = model.split(',', 1)
        return parts[0].strip(), parts[1].strip()

    # 优先级3: DEFAULT
    return model.strip(), "DEFAULT"
```

### 校验函数简化

**文件**: `traj_proxy/utils/validators.py`

```python
def validate_session_id(session_id: Optional[str]) -> Tuple[bool, str]:
    """
    校验 session_id 格式

    session_id 为任意字符串，用于轨迹分组查询。
    不定义分割格式，完全由客户端自定义。
    """
    if session_id is None or session_id == "":
        return True, ""  # 允许为空
    return True, ""  # 任意非空字符串都合法
```

---

## 提取优先级规则

### run_id 提取优先级

```
优先级1（最高）: 路径参数逗号前
       /s/run_001,session-abc/... → run_id = "run_001"

优先级2: model 参数逗号后
       model: "gpt-4,run_002" → run_id = "run_002"

优先级3（最低）: DEFAULT
       无路径、无 model run_id → run_id = "DEFAULT"
```

### session_id 提取优先级

```
优先级1（最高）: 路径参数
       /s/session-abc/... → session_id = "session-abc"
       /s/run_001,session-abc/... → session_id = "session-abc"

优先级2: x-sandbox-traj-id Header

优先级3: x-session-id Header
```

---

## 经验总结

### 设计原则

1. **语义清晰**：每个字段有且只有一个职责
2. **解耦设计**：字段之间不应相互依赖或包含
3. **优先级明确**：多来源时，优先级顺序要有文档说明
4. **向后兼容**：通过巧妙的路径设计，实现新旧格式自动兼容

### 踩坑记录

| 问题 | 原因 | 解决 |
|------|------|------|
| `session_id` 格式限制 | 正则校验过于严格 | 移除三段格式限制，允许任意字符串 |
| `run_id` 来源不一致 | 有时从 `session_id`，有时从 `model` | 统一为路径 > model > DEFAULT |
| 路径逗号解析 | nginx 直接设置 header | 改为传递完整路径段，后端解析 |

### 适用场景

此设计模式适用于：
- 多租户系统需要同时支持多种客户端格式
- 标识字段需要灵活组合
- 向后兼容旧版本接口

---

## 相关文档

- [ID 设计规范](../design/identifier_design.md)
- [API 参考文档](../develop/api_reference.md)

---

## 变更历史

| 日期 | 版本 | 变更说明 |
|------|------|----------|
| 2026-04-10 | v3.0 | 重新定义 run_id、session_id、model 语义，支持路径参数同时传递 run_id 和 session_id |