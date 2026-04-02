# Run ID 和 Model Name 组合定义规则

本文档定义了 TrajProxy 中 `run_id` 和 `model_name` 的组合行为规范。

---

## 概述

TrajProxy 使用 `(run_id, model_name)` 作为模型的唯一标识键，支持多租户场景下的模型隔离。

### 核心概念

- **run_id**: 运行ID，用于区分同一模型名称的不同实例
- **model_name**: 模型名称，如 `gpt-4`、`qwen3.5-2b`
- **DEFAULT**: 当 run_id 为空时的默认值

---

## 注册模型接口

**端点**: `POST /models/register`

| 场景 | run_id | model_name | 行为 |
|------|--------|------------|------|
| 一 | 空 | 非空 | run_id 赋值为 `DEFAULT`，存储键为 `(DEFAULT, model_name)` |
| 二 | 非空 | 非空 | 直接存储，键为 `(run_id, model_name)` |

**示例**:

```bash
# 场景一：run_id 为空，自动使用 DEFAULT
curl -X POST http://localhost:12300/models/register \
  -H "Content-Type: application/json" \
  -d '{
    "model_name": "gpt-4",
    "url": "https://api.openai.com/v1",
    "api_key": "sk-xxxxx"
  }'
# 结果：存储为 (DEFAULT, gpt-4)

# 场景二：指定 run_id
curl -X POST http://localhost:12300/models/register \
  -H "Content-Type: application/json" \
  -d '{
    "run_id": "app_001",
    "model_name": "gpt-4",
    "url": "https://api.openai.com/v1",
    "api_key": "sk-xxxxx"
  }'
# 结果：存储为 (app_001, gpt-4)
```

---

## 删除模型接口

**端点**: `DELETE /models?model_name=xxx&run_id=xxx`

| 场景 | run_id | model_name | 行为 |
|------|--------|------------|------|
| 一 | 空 | 非空 | run_id 赋值为 `DEFAULT`，删除键 `(DEFAULT, model_name)` |
| 二 | 非空 | 非空 | 直接删除键 `(run_id, model_name)` |

**示例**:

```bash
# 场景一：删除 DEFAULT 模型
curl -X DELETE "http://localhost:12300/models?model_name=gpt-4"
# 结果：删除 (DEFAULT, gpt-4)

# 场景二：删除指定 run_id 的模型
curl -X DELETE "http://localhost:12300/models?model_name=gpt-4&run_id=app_001"
# 结果：删除 (app_001, gpt-4)
```

---

## 推理对话接口

**端点**: `POST /v1/chat/completions`

### Run ID 解析规则

按以下优先级确定 run_id：

1. **Model 参数包含逗号**: 从 `model` 参数解析，格式为 `{model_name},{run_id}`
2. **Session ID 包含逗号**: 从 `session_id` 解析，格式为 `{run_id},{sample_id},{task_id}`
3. **默认值**: 使用 `DEFAULT`

### 场景矩阵

| 场景 | model 格式 | session_id 格式 | run_id 来源 | 查找键 |
|------|-----------|-----------------|-------------|--------|
| 1.1 | `model_name` (无逗号) | 空 | 默认 `DEFAULT` | `(DEFAULT, model_name)` |
| 1.2 | `model_name` (无逗号) | 有值，无逗号 | 默认 `DEFAULT` | `(DEFAULT, model_name)` |
| 1.3 | `model_name` (无逗号) | 有值，有逗号 | session_id 第一段 | `(session_run_id, model_name)` |
| 2.1 | `model_name,run_id` (有逗号) | 空 | model 第二段 | `(model_run_id, model_name)` |
| 2.2 | `model_name,run_id` (有逗号) | 有值，无逗号 | model 第二段 | `(model_run_id, model_name)` |
| 2.3 | `model_name,run_id` (有逗号) | 有值，有逗号 | model 第二段 | `(model_run_id, model_name)` |

**重要**: 当 model 参数包含逗号时，model 中的 run_id 优先级最高，忽略 session_id 中的 run_id。

### 示例

```bash
# 场景 1.1：无 session_id，使用 DEFAULT
curl -X POST http://localhost:12300/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4",
    "messages": [{"role": "user", "content": "你好"}]
  }'
# 查找键：(DEFAULT, gpt-4)

# 场景 1.2：session_id 无逗号，使用 DEFAULT
curl -X POST http://localhost:12300/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "x-session-id: simple_session" \
  -d '{
    "model": "gpt-4",
    "messages": [{"role": "user", "content": "你好"}]
  }'
# 查找键：(DEFAULT, gpt-4)

# 场景 1.3：从 session_id 提取 run_id
curl -X POST http://localhost:12300/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "x-session-id: app_001,sample_001,task_001" \
  -d '{
    "model": "gpt-4",
    "messages": [{"role": "user", "content": "你好"}]
  }'
# 查找键：(app_001, gpt-4)

# 场景 2.1：从 model 参数提取 run_id
curl -X POST http://localhost:12300/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4,app_002",
    "messages": [{"role": "user", "content": "你好"}]
  }'
# 查找键：(app_002, gpt-4)

# 场景 2.3：model 和 session_id 都有 run_id，model 优先
curl -X POST http://localhost:12300/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "x-session-id: app_001,sample_001,task_001" \
  -d '{
    "model": "gpt-4,app_002",
    "messages": [{"role": "user", "content": "你好"}]
  }'
# 查找键：(app_002, gpt-4) - 使用 model 中的 run_id
```

---

## Session ID 传递方式

Session ID 可以通过以下方式传递（按优先级）：

1. **路径传递**: `/s/{session_id}/v1/chat/completions`
2. **请求头传递**: `x-session-id` 请求头

**注意**: 旧的 `model@session_id` 格式已不再用于提取 run_id，仅作为 session_id 传递方式之一。

---

## 模型隔离说明

TrajProxy 采用**严格隔离**策略：

- 不同 `run_id` 的模型完全隔离
- 不存在回退逻辑（如找不到指定 run_id 的模型，不会回退到 DEFAULT 模型）
- 每个模型必须精确匹配 `(run_id, model_name)` 才能被访问

---

## 代码实现

### 核心常量

```python
# traj_proxy/utils/validators.py
DEFAULT_RUN_ID = "DEFAULT"
```

### 解析函数

```python
# traj_proxy/proxy_core/routes.py
def _parse_model_and_run_id(model: str, session_id: str = None) -> tuple:
    """
    解析 model 和 session_id，返回 (实际model, run_id)
    
    优先级：
    1. model 中包含逗号 -> 从 model 解析 run_id
    2. session_id 中包含逗号 -> 从 session_id 解析 run_id
    3. 默认 -> run_id = DEFAULT
    """
    actual_model = model

    # model 中有逗号，格式为 {model_name},{run_id}
    if ',' in model:
        parts = model.split(',', 1)
        actual_model = parts[0].strip()
        run_id = parts[1].strip()
        return actual_model, run_id

    # session_id 有逗号，提取 run_id
    if session_id and ',' in session_id:
        run_id = session_id.split(',')[0].strip()
        return actual_model, run_id

    # 默认 run_id
    return actual_model, DEFAULT_RUN_ID
```

---

## 变更历史

| 日期 | 版本 | 变更说明 |
|------|------|----------|
| 2026-04-02 | v2.0 | 重构 run_id 和 model_name 组合定义，引入 DEFAULT 常量，支持 model,run_id 格式 |
