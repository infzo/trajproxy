# TrajProxy 可观测性方案

> **状态**: 待审核（V3 - P0/P1/P2 修复版）
> **作者**: AI Assistant
> **日期**: 2026-06-11
> **变更**: V2 → V3 修复 429 outcome 不可达(P0)、新增信号量/轨迹存储/断连指标(P1)、字段守卫/缓存优化/多进程约束(P2)；V1 → V2 新增解耦架构设计、outcome 判定规则、EventBus 模式；修正告警规则 BUG、
> ContextVar 传播边界、健康检查覆盖范围；删除冗余指标；精简 Dashboard

---

## 一、现状分析与核心问题

### 1.1 当前能力

| 能力 | 现状 | 评级 |
|------|------|------|
| 请求日志 | 纯文本 f-string，~100+ 处散落，两套 request_id | ⭐⭐⭐ |
| 分阶段耗时 | ProcessContext 记录各阶段 ms（~20 处手动 `time.perf_counter()`） | ⭐⭐⭐⭐ |
| 健康检查 | `{"status": "ok"}`，无依赖检查 | ⭐ |
| Metrics | **完全没有** | ❌ |
| Tracing | **完全没有** | ❌ |
| 请求聚合/统计 | **完全没有**（只能查 DB） | ❌ |

### 1.2 当前代码中的观测耦合现状

当前观测逻辑**散布在 6 个核心模块、14 处异常处理层**中：

| 耦合类型 | 数量 | 位置 |
|----------|------|------|
| 散落日志 `logger.info/error` | **100+ 处** | processor.py, pipeline/*.py, infer_client.py, routes.py, worker.py |
| 手动计时 `time.perf_counter()` | **20+ 处** | token_pipeline.py(5阶段), direct_pipeline.py, base.py, worker.py, routes.py |
| 异常处理 `try/except + log + raise` | **14 层嵌套** | processor → pipeline → infer_client → routes，重复 except 模式 |

**核心问题**：观测逻辑与业务逻辑高度耦合，新增观测维度需修改多个业务文件。

### 1.3 核心痛点

| 痛点 | 影响 |
|------|------|
| **观测与业务耦合** | 新增指标/日志需改 pipeline/processor，违反开闭原则 |
| **无法量化负载** | 不知道 QPS、P95 延迟、错误率，全靠"感觉" |
| **无法快速定位问题** | 纯文本日志 grep 效率低，请求生命周期散落在多处日志行 |
| **两套 request_id** | 中间件生成 8 位短 ID，路由层生成完整 UUID，跨层关联困难 |
| **不知道瓶颈在哪** | 不知道延迟卡在消息转换、Token 编码、推理请求、还是 DB 写入 |
| **不知道依赖状态** | 健康检查不检查 DB/推理服务，出问题时无预警 |

---

## 二、设计原则

1. **简单有效** — 解决 80% 的问题，不引入不必要的复杂度
2. **增量演进** — 每项改进可独立上线，不做"大爆炸"式改造
3. **利用已有** — ProcessContext 已有丰富的分阶段耗时数据，充分利用
4. **标准兼容** — 遵循 Prometheus / JSON 日志等业界标准，便于接入 Grafana 等工具
5. **解耦优先** — 观测逻辑通过 EventBus + 装饰器 + 中间件注入，业务代码**不感知**观测模块的存在
6. **可独立禁用** — `observability.disable()` 一行关闭全部观测，业务功能完全不受影响

---

## 三、解耦架构设计

### 3.1 核心理念

```
业务代码只负责产出数据（ProcessContext），观测代码只负责消费数据。
两者通过 EventBus + 装饰器 + 中间件 连接，互不感知。
```

### 3.2 方案选型

| 方案 | 评估 | 结论 |
|------|------|------|
| **纯 EventBus** | 业务代码需处处 `emit()`，侵入性反而更大 | ❌ |
| **纯 Middleware** | 拿不到 ProcessContext（在路由内部创建），无 Pipeline 细节 | ❌ |
| **纯装饰器** | 无法捕获请求级全局视图（outcome 需综合 error + stream 状态） | ❌ |
| **ProcessContext 观察者** | 需大幅重构 ProcessContext，风险高 | ❌ |
| **三层拦截 + EventBus（混合）** | 各取所长，分层截获，业务改动最小 | ✅ |

### 3.3 三层拦截架构

```
┌─────────────────────────────────────────────────────────────────┐
│  Layer 1: Middleware（已存在）     HTTP 级：request_id, 耗时，状态码 │
│                    ↓ 通过 request.state 传递                      │
│  Layer 1.5: Routes 拦截（+3行）  429限流/信号量等待：不经过Processor │
│                    ↓ 通过 EventBus.emit() 发射事件                 │
│  Layer 2: Processor 拦截（+3行）   请求级：ProcessContext 全状态     │
│                    ↓ 通过 EventBus.emit() 发射事件                 │
│  Layer 3: InferClient 装饰器（+4行）推理级：调用耗时, 错误, 重试      │
│                    ↓ 通过 EventBus.emit() 发射事件                 │
│  Layer 4: ProcessorManager 装饰器  生命周期：模型注册/注销/缓存     │
│  Layer 5: Pipeline 装饰器（+2行） 轨迹存储失败/流式断连检测          │
├─────────────────────────────────────────────────────────────────┤
│  EventBus (进程内同步, <50行)                                       │
│                    ↓                                               │
│  订阅者:                                                            │
│    metrics_collector.register_all()  → Prometheus 指标更新         │
│    request_summary.register()       → 摘要日志输出                  │
│    run_id_tracker.register()        → 基数白名单维护               │
├─────────────────────────────────────────────────────────────────┤
│  observability/                                                     │
│  ├── event_bus.py              轻量事件总线（<50行）                │
│  ├── events.py                 事件类型定义（含新增4类事件）         │
│  ├── outcome.py                outcome 判定（纯函数）               │
│  ├── label_guards.py           基数防护（含 known model 缓存）     │
│  ├── decorators.py             @observe_inference 等装饰器          │
│  ├── metrics_collector.py      Prometheus 指标（订阅者）            │
│  ├── request_summary.py        请求摘要日志（订阅者）               │
│  ├── request_context.py        ContextVar 传播                     │
│  ├── json_formatter.py         JSON 日志格式器                     │
│  ├── health_checker.py         分层健康检查（可配置路径）           │
│  └── __init__.py               setup() / disable()                 │
└─────────────────────────────────────────────────────────────────┘
```

### 3.4 EventBus 实现

```python
# observability/event_bus.py
"""
进程内同步事件总线

设计约束：
- 同步执行（无 async overhead），subscriber 必须轻量（仅打点/写日志）
- 失败隔离：单个 subscriber 异常不影响其他 subscriber 和业务代码
- 可全局禁用：observability.disable() 一行关闭全部观测
- 无外部依赖，纯 Python 实现
"""

import logging
from collections import defaultdict
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)

_subscribers: Dict[str, List[Callable[..., None]]] = defaultdict(list)
_enabled: bool = True


def subscribe(event_name: str, handler: Callable[..., None]) -> None:
    """注册事件订阅者"""
    _subscribers[event_name].append(handler)


def emit(event_name: str, **kwargs: Any) -> None:
    """触发事件，依次调用所有订阅者（失败隔离）"""
    if not _enabled:
        return
    for handler in _subscribers.get(event_name, []):
        try:
            handler(**kwargs)
        except Exception as e:
            logger.error(f"EventBus subscriber error [{event_name}]: {e}")


def disable() -> None:
    """全局禁用全部观测（紧急关停 / 测试隔离用）"""
    global _enabled
    _enabled = False


def enable() -> None:
    global _enabled
    _enabled = True


def reset() -> None:
    """清空所有订阅（测试用）"""
    _subscribers.clear()
```

> **⚠️ 多进程约束**：EventBus 使用模块级变量，`_subscribers` / `_enabled` 为**进程级状态**。
> - `setup()` 必须在每个 Worker 进程的 `initialize()` 中各调用一次
> - `disable()` / `reset()` 仅影响当前进程，无法跨 Worker 统一禁用
> - 如需全局禁用，应通过共享环境变量 + 进程重启实现

### 3.5 事件类型定义

```python
# observability/events.py
"""可观测性事件类型 — 每个事件的 kwargs 契约在此声明"""

# ── 请求开始 ──
# kwargs: model: str, is_stream: bool, max_concurrent: int
EVENT_REQUEST_STARTED = "request.started"

# ── 请求完成（成功或失败）──
# kwargs: context: ProcessContext, exception: Optional[Exception]
EVENT_REQUEST_COMPLETED = "request.completed"

# ── 推理调用完成（每次 InferClient 调用）──
# kwargs: model: str, duration_ms: float, retry_count: int,
#         error: Optional[Exception], error_type: Optional[str]
EVENT_INFERENCE_COMPLETED = "inference.completed"

# ── 模型生命周期 ──
# kwargs: action: str("register"|"unregister"), model: str,
#         run_id: str, model_type: str("static"|"dynamic")
EVENT_MODEL_LIFECYCLE = "model.lifecycle"

# ── 并发限流（429）── routes/Middleware 层发出，不经过 Processor ──
# kwargs: model: str, run_id: str, wait_duration_ms: float
EVENT_CONCURRENCY_REJECTED = "concurrency.rejected"

# ── Semaphore 等待完成 ──
# kwargs: wait_duration_ms: float, model: str
EVENT_SEMAPHORE_ACQUIRED = "semaphore.acquired"

# ── 轨迹存储失败（静默失败显式化）──
# kwargs: model: str, error_type: str, error_message: str
EVENT_TRAJECTORY_STORE_ERROR = "trajectory.store_error"

# ── 流式客户端断连 ──
# kwargs: model: str, chunk_count: int, duration_ms: float
EVENT_STREAM_CLIENT_DISCONNECT = "stream.client_disconnect"
```

### 3.6 Outcome 判定 — 纯函数，零侵入

**业务代码完全不感知 outcome 的存在。** outcome 由观测模块从 ProcessContext 状态推导：

```python
# observability/outcome.py
"""
请求结果归因 — 纯函数，仅依赖 ProcessContext + 异常类型

业务代码（Pipeline/Processor/Routes）完全不需要知道 outcome 的存在。
此函数由 EventBus 订阅者调用，不改变业务执行路径。
"""

from typing import Optional


def determine_outcome(context, exception: Optional[Exception] = None) -> str:
    """从 ProcessContext 状态 + 异常类型 推导 outcome"""
    from traj_proxy.exceptions import InferTimeoutError, InferServiceError

    # ── 异常驱动 ──
    # 注意：InferTimeoutError 继承自 InferServiceError，
    # 因此 isinstance 判断顺序至关重要：必须先匹配子类 InferTimeoutError，
    # 再匹配父类 InferServiceError，否则 timeout 会被误判为 error_infer。
    if exception:
        if isinstance(exception, InferTimeoutError):
            return "timeout"
        if isinstance(exception, InferServiceError):
            return "error_infer"
        status = getattr(exception, "status_code", None)
        if status == 429:
            return "error_rate_limit"
        if status and 400 <= status < 500:
            return "error_client"
        return "error_server"

    # ── 流式请求特殊判定（HTTP 200 但不一定"成功"）──
    if getattr(context, "is_stream", False):
        if getattr(context, "stream_finished", True):
            return "success"
        if getattr(context, "stream_chunk_count", 0) > 0:
            return "stream_partial"  # 首 token 后崩溃
        return "error_server"

    # ── 非流式：有 error 字段但无异常 ──
    if getattr(context, "error", None):
        return "error_server"

    return "success"
```

> **⚠️ `error_rate_limit` (429) 的路径特殊性**：
> `determine_outcome()` 中的 `status_code == 429` 分支**仅在异常携带 429 status_code 时触发**。
> 但在实际代码路径中，并发限流（`routes.py` 的 `semaphore.acquire()` 超时）直接抛出 `HTTPException(429)`，
> **不经过 Processor 层的 emit()**。因此 `error_rate_limit` outcome 由 §3.5 新增的
> `EVENT_CONCURRENCY_REJECTED` 事件在 routes/Middleware 层单独记录，而非依赖 `determine_outcome()`。

**Outcome 判定映射表**（完整规则）：

| 异常类型 | status_code | outcome | 说明 |
|----------|-------------|---------|------|
| `InferTimeoutError` | 504 | `timeout` | 推理超时（区别于连接错误） |
| `InferServiceError` | 502 | `error_infer` | 推理服务连接/HTTP 错误 |
| `HTTPException(429)` / `ProxyCoreError(429)` | 429 | `error_rate_limit` | 并发限流 |
| `TokenizerNotFoundError` / `SessionIdError` | 400/422 | `error_client` | 客户端参数问题 |
| `CacheError` / `DatabaseError` | 500/503 | `error_server` | TrajProxy 内部故障 |
| 未分类 `Exception` | 500 | `error_server` | 未知异常 |
| 流式中途断连（无异常，但 stream_finished=False） | 200 | `stream_partial` | 首 Token 后推理服务崩溃 |
| 流式正常完成 | 200 | `success` | — |
| 非流式正常完成 | 200 | `success` | — |

### 3.7 装饰器 — InferClient 观测注入

```python
# observability/decorators.py
"""观测装饰器 — 透明包裹 InferClient / ProcessorManager 方法"""

import time
import functools
from typing import Optional

from traj_proxy.observability.event_bus import emit
from traj_proxy.observability.events import (
    EVENT_INFERENCE_COMPLETED,
    EVENT_MODEL_LIFECYCLE,
)
from traj_proxy.exceptions import InferTimeoutError, InferServiceError


def _classify_infer_error(e: Exception) -> str:
    """将推理异常分类为 error_type 枚举"""
    if isinstance(e, InferTimeoutError):
        return "timeout"
    if isinstance(e, InferServiceError):
        msg = str(e).lower()
        if "连接" in msg or "connection" in msg:
            return "connection"
        return "http_error"
    return "unknown"


def observe_inference(func):
    """装饰 InferClient 的非流式请求方法
    
    契约依赖：
    1. InferClient._last_retry_count：InferClient._handle_request() 重试循环末尾须设置
       self._last_retry_count = attempt  # 供观测装饰器读取
       → 实施时须在 InferClient 类 docstring 中明确此属性用途
    2. model 参数位置：当前假设 model 为第 2 个位置参数或 keyword 参数。
       若 InferClient 方法签名变更，须同步更新此处提取逻辑。
    """
    @functools.wraps(func)
    async def wrapper(self, *args, **kwargs):
        # model 提取：优先 keyword，否则取第 2 个位置参数
        # 当前 InferClient.send_* 签名 model 均在第 2 位（index=1）
        model = kwargs.get("model", "") or (args[1] if len(args) > 1 else "")
        t0 = time.perf_counter()
        error = None
        retry_count = 0
        try:
            result = await func(self, *args, **kwargs)
            retry_count = getattr(self, "_last_retry_count", 0)
            return result
        except Exception as e:
            error = e
            retry_count = getattr(self, "_last_retry_count", 0)
            raise
        finally:
            duration_ms = (time.perf_counter() - t0) * 1000
            emit(
                EVENT_INFERENCE_COMPLETED,
                model=model,
                duration_ms=duration_ms,
                retry_count=retry_count,
                error=error,
                error_type=_classify_infer_error(error) if error else None,
            )
    return wrapper


def observe_inference_stream(func):
    """装饰 InferClient 的流式请求方法
    
    契约依赖：
    1. model 参数位置：当前假设 model 为第 2 个位置参数或 keyword 参数。
       若 InferClient 方法签名变更，须同步更新此处提取逻辑。
    """
    @functools.wraps(func)
    async def wrapper(self, *args, **kwargs):
        model = kwargs.get("model", "") or (args[1] if len(args) > 1 else "")
        t0 = time.perf_counter()
        error = None
        try:
            async for chunk in func(self, *args, **kwargs):
                yield chunk
        except Exception as e:
            error = e
            raise
        finally:
            duration_ms = (time.perf_counter() - t0) * 1000
            emit(
                EVENT_INFERENCE_COMPLETED,
                model=model,
                duration_ms=duration_ms,
                retry_count=0,
                error=error,
                error_type=_classify_infer_error(error) if error else None,
            )
    return wrapper


def observe_model_lifecycle(action: str, model_type: str):
    """装饰 ProcessorManager 的 register/unregister 方法
    
    注意：register_from_config(config: ModelConfig) 第一参数是 ModelConfig 对象，
    unregister_by_key(key: Tuple[str,str]) 第一参数是元组，需做类型适配。
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(self, *args, **kwargs):
            result = await func(self, *args, **kwargs)
            # 适配不同参数签名
            first_arg = args[0] if args else None
            if hasattr(first_arg, "model_name"):        # ModelConfig 对象
                model = first_arg.model_name
                run_id = getattr(first_arg, "run_id", "")
            elif isinstance(first_arg, tuple):           # (run_id, model_name)
                model = first_arg[1] if len(first_arg) > 1 else "unknown"
                run_id = first_arg[0] if len(first_arg) > 0 else ""
            else:
                model = kwargs.get("model_name", str(first_arg) if first_arg else "unknown")
                run_id = kwargs.get("run_id", "")
            emit(
                EVENT_MODEL_LIFECYCLE,
                action=action,
                model=model,
                run_id=run_id,
                model_type=model_type,
            )
            return result
        return wrapper
    return decorator
```

### 3.8 注册机制 — 启动时一次性绑定

```python
# observability/__init__.py
"""可观测性模块 — 一键启用/禁用"""

import logging

logger = logging.getLogger(__name__)
_initialized = False


def setup(app=None) -> None:
    """初始化可观测性系统（Worker 启动时调用一次）"""
    global _initialized
    if _initialized:
        return

    import os
    if os.getenv("OBSERVABILITY_ENABLED", "true").lower() != "true":
        logger.info("可观测性系统已通过环境变量禁用")
        return

    from traj_proxy.observability.event_bus import enable
    from traj_proxy.observability import metrics_collector
    from traj_proxy.observability import request_summary
    from traj_proxy.observability import run_id_tracker

    enable()
    metrics_collector.register_all()
    request_summary.register()
    run_id_tracker.register()

    _initialized = True
    logger.info("可观测性系统已初始化")


def teardown() -> None:
    """关闭可观测性系统"""
    from traj_proxy.observability.event_bus import disable, reset
    disable()
    reset()
    global _initialized
    _initialized = False
    logger.info("可观测性系统已关闭")
```

```python
# worker.py 修改 — initialize() 中仅加 1 行
async def initialize(self):
    # ... 原有初始化代码 ...
    from traj_proxy.observability import setup as setup_observability
    setup_observability(self.app)   # ← 仅此一行
```

### 3.9 侵入性量化对比

| 文件 | 原方案修改量 | 解耦方案V3修改量 | 改动性质 |
|------|------------|-------------|---------|
| `processor.py` | 不改 | **+10 行** | `import` + 2处 `emit(STARTED)` + 2处 `emit(COMPLETED)` in finally |
| `infer_client.py` | **+10-15 行**（内联埋点） | **+8 行** | 4 处装饰器 + 2 处 `self._last_retry_count = attempt` |
| `pipeline/base.py` | **+20 行**（调用 emit_summary） | **+4 行** | 轨迹存储失败 emit（P1修复） |
| `pipeline/direct_pipeline.py` | 可能需改 | **+4 行** | 同上 |
| `pipeline/token_pipeline.py` | 可能需改 | **0 行** | — |
| `processor_manager.py` | **+5 行**（注册时打指标） | **+2 行** | 仅加装饰器 |
| `worker.py` | **+15 行**（中间件埋点） | **+1 行** | `setup_observability()` |
| `routes.py` | **+3 行** | **+8 行** | request_id 统一 + 429/信号量 emit（P0修复） |
| **合计** | **~53 行 / 6 文件** | **~41 行 / 7 文件** | 覆盖度更高，侵入性仍可控 |

**核心收益**：
- **Pipeline 层零修改** — direct_pipeline.py 和 token_pipeline.py 完全不动
- **outcome 判定完全外置** — 纯函数从 ProcessContext 推导，不改变业务路径
- **可独立禁用** — `observability.disable()` + `observability.teardown()`

---

## 四、方案总览

```
┌─────────────────────────────────────────────────────────────────┐
│                   可观测性方案（解耦架构版）                       │
│                                                                  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐│
│  │ Request  │ │ 结构化   │ │Prometheus│ │ 请求摘要 │ │ 增强   ││
│  │ ID 统一  │ │ JSON日志 │ │ Metrics  │ │ Log      │ │ 健康   ││
│  │          │ │          │ │          │ │          │ │ 检查   ││
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └───┬────┘│
│       │             │            │             │           │     │
│       │     ┌───────┴────────────┴─────────────┴───────────┘     │
│       │     │              observability/ 模块                   │
│       │     │  ├── event_bus.py          (轻量事件总线)         │
│       │     │  ├── events.py             (事件类型定义)         │
│       │     │  ├── outcome.py            (outcome 纯函数判定)   │
│       │     │  ├── label_guards.py       (基数防护)             │
│       │     │  ├── decorators.py         (观测装饰器)           │
│       │     │  ├── metrics_collector.py  (Prometheus 订阅者)    │
│       │     │  ├── request_summary.py    (摘要日志订阅者)       │
│       │     │  ├── request_context.py    (ContextVar 传播)      │
│       │     │  ├── json_formatter.py     (JSON 日志格式)        │
│       │     │  └── health_checker.py     (健康检查)             │
│       └─────┘                                                    │
└─────────────────────────────────────────────────────────────────┘
```

**新增依赖**：`prometheus-client`（仅此 1 个，JsonFormatter 自行实现仅 20 行）

---

## 五、模块详细设计

### 5.1 Request ID 统一

**问题**：中间件生成 `str(uuid.uuid4())[:8]`（8位），路由层生成 `str(uuid.uuid4())`（完整），两者无法关联。

**方案**：中间件生成唯一 request_id，通过 `request.state` 传递给路由层。

```python
# workers/worker.py - request_logging_middleware
@self.app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    start_time = time.time()
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    request.state.request_start_time = start_time
    # ... 原有日志逻辑 ...
    response.headers["X-Request-ID"] = request_id[:8]  # 短 ID 给客户端
    return response
```

```python
# serve/routes.py - chat_completions
async def chat_completions(request: Request, ...):
    request_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())
    # 路由层不再自己生成，复用中间件 ID
```

**影响范围**：`workers/worker.py` + `serve/routes.py`

---

### 5.2 结构化 JSON 日志

```python
# observability/json_formatter.py
import json
import logging
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    """JSON 格式日志输出器"""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "module": record.module,
            "worker_id": getattr(record, "worker_id", "-"),
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None)
        if request_id:
            log_entry["request_id"] = request_id
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)
```

**配置切换**：`LOG_FORMAT=json`（默认 `text`）。

```python
# utils/logger.py 改动
log_format = os.getenv("LOG_FORMAT", "text")
if log_format == "json":
    from traj_proxy.observability.json_formatter import JsonFormatter
    formatter = JsonFormatter()
else:
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)s | %(module)s | %(worker_id)s | %(message)s'
    )
```

**request_id 注入**：`logging.Filter` + `ContextVar`：

```python
class RequestIDFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        from traj_proxy.observability.request_context import get_request_id
        record.request_id = get_request_id("")
        return True
```

#### ContextVar 传播机制与边界

```python
# observability/request_context.py
from contextvars import ContextVar

_request_id: ContextVar[str] = ContextVar("request_id", default="")

def set_request_id(request_id: str) -> None:
    _request_id.set(request_id)

def get_request_id(default: str = "") -> str:
    return _request_id.get(default)
```

**⚠️ ContextVar 传播边界**：

| 场景 | 传播? | 说明 |
|------|-------|------|
| `async def` 协程 | ✅ | async/await 链自动继承 |
| `async for` 生成器 | ✅ | 继承创建时的上下文 |
| `asyncio.create_task()` | ✅ | 新 Task 继承父 Task |
| `asyncio.to_thread()` | ❌ | 线程池不继承 ContextVar |

**线程池处理**：Tokenizer 等 CPU 密集操作如需 `to_thread()`，需显式传递：

```python
current_request_id = get_request_id()
result = await asyncio.to_thread(cpu_intensive_fn, current_request_id, ...)
```

---

### 5.3 Prometheus Metrics

**目标**：量化观测 — QPS、延迟、错误率（含结果归因）、Token、推理延迟/重试、流式完整性、并发水位。

#### 5.3.1 结果归因（outcome）

outcome 由 `observability/outcome.py` 纯函数判定（§3.6），**业务代码无感知**。

#### 5.3.2 核心指标定义（16 个核心 + 3 个可选 + ProcessCollector）

```python
# observability/metrics_collector.py
from prometheus_client import Counter, Histogram, Gauge

# ===== A. 请求核心 (5个) =====
REQUEST_TOTAL = Counter("trajproxy_requests_total", "...",
    ["method", "model", "stream", "outcome", "run_id"])
REQUEST_DURATION = Histogram("trajproxy_request_duration_seconds", "...",
    ["model", "stream", "run_id"],
    buckets=[0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60])
ACTIVE_REQUESTS = Gauge("trajproxy_active_requests", "...")
CONCURRENCY_UTILIZATION = Gauge("trajproxy_concurrency_utilization", "...")
MAX_CONCURRENT = Gauge("trajproxy_max_concurrent_requests", "...",
    ["model"])   # 暴露为常量 Gauge，Grafana 计算利用率更灵活

# ===== A2. 并发控制 (2个) =====
SEMAPHORE_WAIT_DURATION = Histogram("trajproxy_semaphore_wait_seconds",
    "信号量等待耗时", ["model"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10])
SEMAPHORE_REJECTED = Counter("trajproxy_semaphore_rejected_total",
    "信号量超时拒绝数", ["model"])

# ===== B. 流式 (2个核心 + 1个可选) =====
TTFT_SECONDS = Histogram("trajproxy_ttft_seconds", "...",
    ["model", "run_id"], buckets=[0.05, 0.1, 0.25, 0.5, 1, 2, 5])
STREAM_COMPLETION = Counter("trajproxy_stream_completion_total", "...",
    ["model", "run_id", "result"])
STREAM_CLIENT_DISCONNECT = Counter("trajproxy_stream_client_disconnect_total",
    "流式传输中途客户端断连", ["model"])   # Phase 3+
# STREAM_CHUNKS — 可选，Phase 3+

# ===== C. 阶段 (1个) =====
PHASE_DURATION = Histogram("trajproxy_phase_duration_seconds", "...",
    ["model", "phase"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10])

# ===== D. Token (1个) =====
TOKENS_TOTAL = Counter("trajproxy_tokens_total", "...",
    ["model", "run_id", "type"])

# ===== E. 下游依赖 (5个) =====
INFER_DURATION = Histogram("trajproxy_infer_duration_seconds", "...",
    ["model"], buckets=[0.1, 0.5, 1, 2, 5, 10, 30, 60])
INFER_ERRORS = Counter("trajproxy_infer_errors_total", "...",
    ["model", "error_type"])
INFER_RETRIES = Counter("trajproxy_infer_retries_total", "...",
    ["model", "outcome"])   # 区分"推理慢了"还是"重试多了"
DB_POOL_USAGE = Gauge("trajproxy_db_pool_usage", "...", ["state"])
TRAJECTORY_STORE_ERRORS = Counter("trajproxy_trajectory_store_errors_total",
    "轨迹存储失败数（DB写入静默失败显式化）", ["model", "error_type"])

# ===== F. 可选（Phase 3+）=====
MODEL_LIFECYCLE = Counter("trajproxy_model_lifecycle_total", "...",
    ["action", "type"])
STREAM_CHUNKS = Histogram("trajproxy_stream_chunks_per_request", "...",
    ["model", "run_id"], buckets=[1, 10, 50, 100, 200, 500, 1000, 5000])
PROCESSOR_CACHE_OPS = Counter("trajproxy_processor_cache_operations_total",
    "Processor缓存操作", ["result"])   # result: hit/miss/evicted
```

> **已删除 `REQUESTS_INFLIGHT_PEAK`**：用 PromQL `max_over_time(trajproxy_active_requests[24h])` 替代。

#### 5.3.3 ProcessCollector（零侵入进程级指标）

```python
from prometheus_client import ProcessCollector
ProcessCollector()  # 自动暴露: process_cpu_seconds, process_resident_memory_bytes,
                    # process_open_fds, process_max_fds, process_start_time_seconds
```

#### 5.3.4 埋点位置（解耦架构版）

| 指标 | 截获层 | 方式 |
|------|--------|------|
| `REQUEST_TOTAL` / `REQUEST_DURATION` / `ACTIVE_REQUESTS` / `CONCURRENCY_UTILIZATION` | Layer 2: `processor.py` emit | EventBus 订阅 |
| `TTFT_SECONDS` / `STREAM_COMPLETION` / `PHASE_DURATION` / `TOKENS_TOTAL` | Layer 2: 从 ProcessContext 读取 | 订阅者处理 |
| `SEMAPHORE_WAIT_DURATION` / `SEMAPHORE_REJECTED` | **Layer 1.5: `routes.py` emit** | EventBus 订阅 |
| `INFER_DURATION` / `INFER_ERRORS` / `INFER_RETRIES` | Layer 3: `@observe_inference` | 装饰器透明 |
| `DB_POOL_USAGE` | `DatabaseManager._monitor_pool()` | 已有 |
| `TRAJECTORY_STORE_ERRORS` | **Layer 5: `pipeline/*.py` emit** | EventBus 订阅 |
| `STREAM_CLIENT_DISCONNECT` | **Layer 5: StreamingResponse 异常捕获** | EventBus 订阅 |
| `MODEL_LIFECYCLE` | Layer 4: `@observe_model_lifecycle` | 装饰器 |
| ProcessCollector | Prometheus 内置 | `setup()` 一行注册 |

#### 5.3.5 Metrics 订阅者实现

```python
# observability/metrics_collector.py — EventBus 订阅者

from traj_proxy.observability.event_bus import subscribe
from traj_proxy.observability.events import (
    EVENT_REQUEST_COMPLETED, EVENT_INFERENCE_COMPLETED, EVENT_MODEL_LIFECYCLE,
    EVENT_CONCURRENCY_REJECTED, EVENT_SEMAPHORE_ACQUIRED,
    EVENT_TRAJECTORY_STORE_ERROR, EVENT_STREAM_CLIENT_DISCONNECT,
)
from traj_proxy.observability.outcome import determine_outcome
from traj_proxy.observability.label_guards import safe_model_label, safe_run_id_label

# ── ProcessContext 字段守卫：防止字段重命名导致指标静默消失 ──
_REQUIRED_CONTEXT_FIELDS = frozenset({
    "model", "is_stream", "processing_duration_ms", "request_id", "run_id",
})
_OPTIONAL_CONTEXT_FIELDS = frozenset({
    "transform_duration_ms", "encode_duration_ms", "cache_db_query_ms",
    "cache_prefix_match_ms", "inference_duration_ms", "decode_duration_ms",
    "prompt_tokens", "completion_tokens", "cache_hit_tokens", "ttft_ms",
    "stream_finished", "stream_chunk_count", "error",
})


def validate_context_fields(context) -> None:
    """启动时或首次请求时校验 ProcessContext 字段完整性"""
    missing = _REQUIRED_CONTEXT_FIELDS - {
        f for f in _REQUIRED_CONTEXT_FIELDS if hasattr(context, f)
    }
    if missing:
        import logging
        logging.getLogger(__name__).error(
            f"ProcessContext 缺少必需字段: {missing}，指标可能不完整"
        )


def _on_request_started(model, is_stream, max_concurrent) -> None:
    ACTIVE_REQUESTS.inc()
    if max_concurrent > 0:
        CONCURRENCY_UTILIZATION.set(ACTIVE_REQUESTS._value.get() / max_concurrent)
        MAX_CONCURRENT.labels(safe_model_label(model)).set(max_concurrent)


def _on_request_completed(context, exception) -> None:
    ACTIVE_REQUESTS.dec()
    model = safe_model_label(context.model)
    run_id = safe_run_id_label(context.run_id or "")
    stream = str(getattr(context, "is_stream", False)).lower()
    outcome = determine_outcome(context, exception)
    duration_s = (getattr(context, "processing_duration_ms", 0) or 0) / 1000

    REQUEST_TOTAL.labels("POST", model, stream, outcome, run_id).inc()
    REQUEST_DURATION.labels(model, stream, run_id).observe(duration_s)

    # 分阶段耗时
    for phase, attr in [
        ("transform", "transform_duration_ms"), ("encode", "encode_duration_ms"),
        ("cache_db_query", "cache_db_query_ms"), ("cache_prefix_match", "cache_prefix_match_ms"),
        ("inference", "inference_duration_ms"), ("decode", "decode_duration_ms"),
    ]:
        ms = getattr(context, attr, None)
        if ms is not None:
            PHASE_DURATION.labels(model, phase).observe(ms / 1000)

    # Token + 流式指标 ... (省略重复代码，完整实现同上文)


def _on_semaphore_acquired(wait_duration_ms, model) -> None:
    """信号量等待耗时记录"""
    SEMAPHORE_WAIT_DURATION.labels(safe_model_label(model)).observe(wait_duration_ms / 1000)


def _on_concurrency_rejected(model, run_id, wait_duration_ms) -> None:
    """429 限流记录（P0 修复：此事件由 routes.py 发出，不经过 Processor）"""
    SEMAPHORE_REJECTED.labels(safe_model_label(model)).inc()
    REQUEST_TOTAL.labels("POST", safe_model_label(model), "false",
                         "error_rate_limit", safe_run_id_label(run_id or "")).inc()


def _on_trajectory_store_error(model, error_type, error_message) -> None:
    """轨迹存储失败记录（DB 写入静默失败显式化）"""
    TRAJECTORY_STORE_ERRORS.labels(safe_model_label(model), error_type).inc()


def _on_stream_client_disconnect(model, chunk_count, duration_ms) -> None:
    """流式客户端断连记录"""
    STREAM_CLIENT_DISCONNECT.labels(safe_model_label(model)).inc()


def _on_inference_completed(model, duration_ms, retry_count, error, error_type) -> None:
    model = safe_model_label(model)
    INFER_DURATION.labels(model).observe(duration_ms / 1000)
    if error:
        INFER_ERRORS.labels(model, error_type).inc()
    if retry_count > 0:
        INFER_RETRIES.labels(model, "success" if not error else "failed").inc(retry_count)


def register_all() -> None:
    subscribe(EVENT_REQUEST_STARTED, _on_request_started)
    subscribe(EVENT_REQUEST_COMPLETED, _on_request_completed)
    subscribe(EVENT_INFERENCE_COMPLETED, _on_inference_completed)
    subscribe(EVENT_MODEL_LIFECYCLE, _on_model_lifecycle)
    subscribe(EVENT_CONCURRENCY_REJECTED, _on_concurrency_rejected)
    subscribe(EVENT_SEMAPHORE_ACQUIRED, _on_semaphore_acquired)
    subscribe(EVENT_TRAJECTORY_STORE_ERROR, _on_trajectory_store_error)
    subscribe(EVENT_STREAM_CLIENT_DISCONNECT, _on_stream_client_disconnect)
```

#### 5.3.6 Processor 拦截点（+6 行）

```python
# processor.py — 非流式
from traj_proxy.observability.event_bus import emit
from traj_proxy.observability.events import EVENT_REQUEST_COMPLETED

async def process_request(self, messages, request_id, ...) -> ProcessContext:
    context = self._pipeline._create_context(...)
    emit(EVENT_REQUEST_STARTED, model=context.model, is_stream=context.is_stream,
         max_concurrent=self._max_concurrent)   # ← 请求开始
    exception = None
    try:
        context = await self._pipeline.process(messages, context)
        return context
    except Exception as e:
        exception = e
        raise
    finally:
        emit(EVENT_REQUEST_COMPLETED, context=context, exception=exception)

# processor.py — 流式（async generator）
async def process_stream(self, messages, request_id, ...):
    context = self._pipeline._create_context(...)
    emit(EVENT_REQUEST_STARTED, model=context.model, is_stream=True,
         max_concurrent=self._max_concurrent)   # ← 请求开始
    exception = None
    try:
        async for chunk in self._pipeline.process_stream(messages, context):
            yield chunk
    except Exception as e:
        exception = e
        raise
    finally:
        emit(EVENT_REQUEST_COMPLETED, context=context, exception=exception)
```

#### 5.3.7 Routes 层 429 限流拦截点（+3 行）— P0 修复

**问题**：并发限流（`HTTPException(429)`）在 `routes.py` 直接抛出，**不经过 Processor 层**，
导致 `error_rate_limit` outcome 在 Processor emit 事件中永远不可达。

```python
# routes.py — semaphore 获取失败时 emit
from traj_proxy.observability.event_bus import emit
from traj_proxy.observability.events import (
    EVENT_CONCURRENCY_REJECTED, EVENT_SEMAPHORE_ACQUIRED,
)

try:
    t_wait = time.perf_counter()
    await asyncio.wait_for(semaphore.acquire(), timeout=semaphore_timeout)
    wait_ms = (time.perf_counter() - t_wait) * 1000
    emit(EVENT_SEMAPHORE_ACQUIRED, wait_duration_ms=wait_ms, model=model)
except asyncio.TimeoutError:
    emit(EVENT_CONCURRENCY_REJECTED, model=model, run_id=run_id,
         wait_duration_ms=semaphore_timeout * 1000)
    raise HTTPException(status_code=429, detail="Concurrency limit exceeded")
```

**侵入性**：`routes.py` 改动从 +3 行增加至 **+8 行**（含时间计量）。

#### 5.3.8 Pipeline 层轨迹存储拦截点（+2 行）

**问题**：`base.py:152-154` 和 `direct_pipeline.py:182` 中，轨迹存储失败仅 `catch + log`，不中断主流程。
DB 故障可能长期不被发现。

```python
# pipeline/base.py — _store_trajectory 的 except 块中追加
from traj_proxy.observability.event_bus import emit
from traj_proxy.observability.events import EVENT_TRAJECTORY_STORE_ERROR

try:
    await self._store_trajectory(context)
except DatabaseError as e:
    logger.error(f"轨迹存储失败: {e}")
    emit(EVENT_TRAJECTORY_STORE_ERROR,
         model=context.model,
         error_type=type(e).__name__,
         error_message=str(e)[:200])
```

**侵入性**：`pipeline/base.py` 新增 **+4 行**（原"零修改"变为 +4 行），`direct_pipeline.py` 类似 +4 行。

**Metrics 端点**：

```python
# workers/route_registrar.py
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

@self.app.get("/metrics", tags=["Observability"])
async def metrics():
    from starlette.responses import Response
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
```

---

### 5.4 请求摘要日志（EventBus 订阅者）

**解耦方式**：订阅 `EVENT_REQUEST_COMPLETED`，Pipeline 层无需调用。

```python
# observability/request_summary.py
import logging

logger = logging.getLogger(__name__)
_worker_id: str = "unknown"


def _on_request_completed(context, exception) -> None:
    from traj_proxy.observability.outcome import determine_outcome
    from traj_proxy.observability.label_guards import safe_model_label

    outcome = determine_outcome(context, exception)
    model_raw = getattr(context, "model", "unknown")
    model_known = safe_model_label(model_raw) != "unknown"

    summary = {
        "request_id": getattr(context, "request_id", ""),
        "worker_id": _worker_id,
        "model": model_raw,
        "model_known": model_known,
        "stream": getattr(context, "is_stream", False),
        "outcome": outcome,
        "duration_ms": round(getattr(context, "processing_duration_ms", 0) or 0, 1),
    }
    optional = {
        "run_id": getattr(context, "run_id", None),
        "session_id": getattr(context, "session_id", None),
        "client_ip": getattr(context, "client_ip", None),
        "prompt_tokens": getattr(context, "prompt_tokens", None),
        "completion_tokens": getattr(context, "completion_tokens", None),
        "cache_hit_tokens": getattr(context, "cache_hit_tokens", None),
        "ttft_ms": getattr(context, "ttft_ms", None),
        "inference_ms": getattr(context, "inference_duration_ms", None),
        "transform_ms": getattr(context, "transform_duration_ms", None),
        "encode_ms": getattr(context, "encode_duration_ms", None),
        "decode_ms": getattr(context, "decode_duration_ms", None),
    }
    summary.update({k: v for k, v in optional.items() if v is not None})
    if exception:
        summary["error_type"] = type(exception).__name__
        summary["error_message"] = str(exception)[:200]

    if outcome.startswith("error") or outcome in ("timeout", "stream_partial"):
        logger.error("request_summary", extra={"summary": summary})
    else:
        logger.info("request_summary", extra={"summary": summary})


def register() -> None:
    from traj_proxy.observability.event_bus import subscribe
    from traj_proxy.observability.events import EVENT_REQUEST_COMPLETED
    subscribe(EVENT_REQUEST_COMPLETED, _on_request_completed)
```

**摘要日志特性**：
- 包含 `worker_id`（多 Worker 定位）和 `client_ip`（异常客户端识别）
- `model_known` 字段与 Metrics 的 `safe_model_label` 对齐

---

### 5.5 增强健康检查

**改进**：按 `base_url` 去重后全量检查（原来只查 3 个，50 模型会有 47 个盲区）。

```python
# observability/health_checker.py（关键变更）

# 默认健康检查路径，可通过配置覆盖
_DEFAULT_HEALTH_PATH = "/health"


async def deep_check(self, processor_manager, config: dict | None = None) -> Dict[str, Any]:
    # ... DB 检查 ...

    # 推理服务：按 base_url 去重后全量检查
    if processor_manager:
        processors_info = processor_manager.get_all_processors_info()
        seen_urls = set()
        unique_services = []
        for info in processors_info:
            url = info.get("infer_client_url", "").rstrip("/")
            if url and url not in seen_urls:
                seen_urls.add(url)
                unique_services.append(info)

        errors = []
        for info in unique_services:
            url = info.get("infer_client_url", "")
            # 支持每模型配置健康检查路径（兼容 vLLM/Ollama/TGI 等不同框架）
            health_path = info.get("health_check_path", _DEFAULT_HEALTH_PATH)
            try:
                async with httpx.AsyncClient(timeout=3) as client:
                    resp = await client.get(f"{url}{health_path}")
                    if resp.status_code >= 500:
                        errors.append(f"{info.get('model_name', '?')}: HTTP {resp.status_code}")
            except Exception as e:
                errors.append(f"{info.get('model_name', '?')}: {str(e)[:100]}")
        # ...
```

**配置示例**（`configs/config.yaml`）：

```yaml
models:
  - name: "llama-3-70b"
    infer_url: "http://vllm-node:8000"
    health_check_path: "/health"        # vLLM 默认
  - name: "qwen-72b"
    infer_url: "http://ollama-node:11434"
    health_check_path: "/api/tags"      # Ollama 使用此路径
```

---

## 六、目录结构

```
traj_proxy/
├── observability/                          # ← 新增模块（自包含）
│   ├── __init__.py                         # setup() / teardown()
│   ├── event_bus.py                        # 轻量事件总线（<50行）
│   ├── events.py                           # 事件类型常量
│   ├── outcome.py                          # outcome 纯函数
│   ├── label_guards.py                     # 基数防护
│   ├── decorators.py                       # @observe_inference 等
│   ├── metrics_collector.py                # Prometheus 订阅者
│   ├── request_summary.py                  # 摘要日志订阅者
│   ├── request_context.py                  # ContextVar
│   ├── json_formatter.py                   # JSON 格式器
│   └── health_checker.py                   # 健康检查
├── proxy_core/
│   ├── processor.py                        # 改动：+6行 emit()
│   ├── infer_client.py                     # 改动：+4行装饰器
│   ├── processor_manager.py                # 改动：+2行装饰器
│   ├── pipeline/base.py                    # 改动：+4行 轨迹存储emit（P1修复）
│   └── pipeline/direct|token_pipeline.py   # 改动：+4行 同上
├── workers/worker.py                       # 改动：+1行 setup
└── serve/routes.py                         # 改动：+8行 request_id + 429/信号量emit（P0修复）
```

**新增文件 11 个，修改文件 7 个（核心业务文件改动仍 <10 行），新增依赖 1 个。**

---

## 七、实施路线

### Phase 1：基础 + 解耦骨架（2-3 天）

- [ ] 新增 `observability/` 模块骨架（含 `event_bus.py`, `events.py`, `outcome.py`, `label_guards.py`）
- [ ] 实现 `request_context.py`（ContextVar 传播）
- [ ] 实现 `json_formatter.py`
- [ ] 修改 `utils/logger.py` 支持 `LOG_FORMAT=json` 切换
- [ ] Request ID 统一（中间件 → `request.state` → 路由层）
- [ ] 中间件设置 ContextVar
- [ ] `observability/__init__.py` 实现 `setup()` / `teardown()` / `disable()`
- [ ] Worker 初始化中调用 `setup_observability()`
- [ ] 添加 ContextVar 传播边界测试（含流式路径和 to_thread 场景）

> **价值**：日志可被聚合系统解析，request_id 全链路可追溯。全部观测可一键禁用。

### Phase 2：Metrics（2-3 天）

- [ ] 实现 `metrics_collector.py`（11 核心指标 + ProcessCollector）
- [ ] 实现 `decorators.py`（`@observe_inference`、`@observe_model_lifecycle`）
- [ ] `processor.py` 增加 EventBus emit（finally 块 +6 行）
- [ ] `infer_client.py` 添加装饰器（4 处）+ `_last_retry_count` 属性
- [ ] `processor_manager.py` 添加装饰器（2 处）
- [ ] 注册 `/metrics` 端点
- [ ] `requirements.txt` 添加 `prometheus-client`

> **价值**：可量化 QPS、延迟分布、错误率（含 7 种结果归因）、推理重试、作业级成本。

### Phase 3：增强（1-2 天）

- [ ] 实现 `request_summary.py`（EventBus 订阅者）
- [ ] 实现 `health_checker.py`（按 base_url 去重 + 可配置检查路径）
- [ ] 增强 `/health` 端点
- [ ] （可选）启用 `MODEL_LIFECYCLE` / `STREAM_CHUNKS` 可选指标
- [ ] 添加基数稳定性测试
- [ ] 实现 `PROCESSOR_CACHE_OPS`（Processor 缓存 hit/miss/evicted 指标）
- [ ] 实现 `STREAM_CLIENT_DISCONNECT`（StreamingResponse 断连检测，通过 `aclose()` 捕获）
- [ ] `label_guards.refresh_known_models()` 与 model 注册/注销联动

### Phase 4：可视化与告警（2-3 天，独立于应用层代码）

- [ ] 部署 Prometheus + Grafana + targets.json
- [ ] 导入 Level 1 Dashboard（9 核心面板，详见 §13）
- [ ] 配置关键告警规则（见 §12.6）
- [ ] （Level 2）部署 Loki + Vector，扩展 Dashboard 到完整版
- [ ] （Level 2）配置 AlertManager + 钉钉/邮件推送

---

## 八、配置项

```yaml
# configs/config.yaml 新增
observability:
  enabled: true                      # 是否启用可观测性系统（环境变量 OBSERVABILITY_ENABLED 可覆盖）
  log_format: text                   # text | json，环境变量 LOG_FORMAT 可覆盖
  metrics_enabled: true              # 是否启用 Prometheus metrics
  health_deep_check: false           # 健康检查默认是否做深度检查
```

**环境变量**（优先级高于配置文件）：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OBSERVABILITY_ENABLED` | `true` | 全局开关，`false` 时 setup() 直接跳过 |
| `LOG_FORMAT` | `text` | `json` 启用结构化日志 |

---

## 九、数据存储与内存分析

### 9.1 各模块数据持有情况

| 模块 | 进程内持久持有 | 持有内容 | 内存风险 |
|------|-------------|---------|---------|
| `event_bus` | 是 | 订阅者函数引用列表（3-5 个回调） | 极小，< 1KB |
| `outcome.py` | 否 | 无状态纯函数 | 无 |
| `decorators.py` | 否 | 无状态函数包裹 | 无 |
| `label_guards.py` | 是 | `_KNOWN_RUN_IDS: set` + 已知 model set 引用 | < 10KB |
| `request_context` | 否 | 当前请求的 `request_id` | 请求级，async context 结束回收 |
| `json_formatter` | 否 | 无状态 | 无 |
| `request_summary` | 否 | 无状态，调用 logger 后丢弃 | 无 |
| `health_checker` | 否 | 无状态，每次即时查询 | 无 |
| **`metrics_collector`** | **是** | 时间序列注册表 | **见下文** |

### 9.2 Prometheus 指标内存模型

按典型规模（50 个注册模型 × 10 个 run_id）：

| 指标 | label 组合 | 序列数 |
|------|-----------|-------|
| `REQUEST_TOTAL(method × model × stream × outcome × run_id)` | 1 × 50 × 2 × 7 × 10 | ~7000 |
| `REQUEST_DURATION(model × stream × run_id)` | 50 × 2 × 10 | ~1000 |
| `STREAM_COMPLETION(model × run_id × result)` | 50 × 10 × 3 | ~1500 |
| `TTFT_SECONDS(model × run_id)` | 50 × 10 | ~500 |
| `PHASE_DURATION(model × phase)` | 50 × 6 | ~300 |
| `TOKENS_TOTAL(model × run_id × type)` | 50 × 10 × 3 | ~1500 |
| `SEMAPHORE_WAIT_DURATION(model)` | 50 | ~50 |
| `SEMAPHORE_REJECTED(model)` | 50 | ~50 |
| `MAX_CONCURRENT(model)` | 50 | ~50 |
| `INFER_DURATION(model)` | 50 | ~50 |
| `INFER_ERRORS(model × error_type)` | 50 × 3 | ~150 |
| `INFER_RETRIES(model × outcome)` | 50 × 2 | ~100 |
| `TRAJECTORY_STORE_ERRORS(model × error_type)` | 50 × 2 | ~100 |
| `DB_POOL_USAGE` / `ACTIVE_REQUESTS` / `CONCURRENCY_UTILIZATION` | 固定 | ~8 |
| **合计** | | **~13858 序列，约 5-10MB / Worker** |

> **内存影响**：每 Worker 增量 < 15MB，进程重启清零。

### 9.3 基数爆炸防护

```python
# observability/label_guards.py
"""基数防护 — 防止高 cardinality 导致 Prometheus 内存爆炸"""

_KNOWN_RUN_IDS: set = set()
_MAX_RUN_ID_CARDINALITY = 100

# ── known model 缓存：避免每次请求重新获取 ──
_KNOWN_MODELS: set = set()
_models_cache_initialized: bool = False


def refresh_known_models(models: set) -> None:
    """由 model 注册/注销事件触发，增量更新缓存"""
    global _KNOWN_MODELS
    _KNOWN_MODELS = set(models)


def safe_model_label(model: str) -> str:
    """未注册模型归为 'unknown'，防止恶意 model 名制造序列
    使用缓存的 _KNOWN_MODELS，避免每次请求调用 ProcessorManager"""
    return model if model in _KNOWN_MODELS else "unknown"


def register_known_run_id(run_id: str) -> None:
    _KNOWN_RUN_IDS.add(run_id)


def unregister_known_run_id(run_id: str) -> None:
    _KNOWN_RUN_IDS.discard(run_id)


def safe_run_id_label(run_id: str) -> str:
    """run_id = 作业/租户级标识，基数封顶 100"""
    if not run_id:
        return ""
    if len(_KNOWN_RUN_IDS) >= _MAX_RUN_ID_CARDINALITY and run_id not in _KNOWN_RUN_IDS:
        return "other"
    return run_id
```

> **缓存同步机制**：`refresh_known_models()` 由 `EVENT_MODEL_LIFECYCLE` 订阅者在
> `register`/`unregister` 事件触发时调用，确保每次模型变更后立即更新缓存。
> 不再依赖每次请求时 `ProcessorManager.get_known_model_set()` 的实时查询。

**基数上限约束**：

| label | 允许值 | 上限 |
|-------|--------|------|
| `model` | ProcessorManager 已注册 model_name | ≤ 注册数 + 1 |
| `method` | `POST`, `GET`, `DELETE`, `OTHER` | ≤ 4 |
| `stream` | `true`, `false` | 2 |
| `outcome` | 7 种归因（固定枚举） | 7 |
| `run_id` | 已注册 run_id，超出归 `"other"` | ≤ 100 |
| `phase` | 7 个处理阶段 | 7 |
| `type` (token) | `prompt`, `completion`, `cached` | 3 |
| `result` (stream) | `full`, `partial`, `empty` | 3 |
| `error_type` | `timeout`, `connection`, `http_error` | 3 |
| `outcome` (retry) | `success`, `failed` | 2 |

**🚫 禁止行为**：`request_id`、`session_id`、原始 `error_message` 作为 label。

### 9.4 内存泄露验证

1. **基数稳定性测试**：压测 1h，`GET /metrics` 序列数稳定在预期值
2. **未知 model 攻击测试**：1000 个随机 model 名，确认 `model=unknown` 只有一个序列
3. **进程重启清零**：压测中重启进程，序列从 0 开始
4. **基线对比**：对比有无 metrics 的 Worker 内存增量 < 5MB

---

## 十、不做的事（有意识排除）

| 方案 | 原因 |
|------|------|
| **OpenTelemetry** | 单服务代理链路短，Prometheus + 摘要日志足够 |
| **ELK/Loki 集成代码** | JSON 日志即可被任意聚合系统消费，不在应用层集成 |
| **实时告警** | 由 AlertManager / Grafana 端处理 |
| **跨 Worker 指标聚合** | 各 Worker 独立暴露，Prometheus 服务端聚合 |
| **自定义 Dashboard** | 提供模板即可，不在应用层嵌入 UI |
| **请求体/响应体日志** | 安全规则禁止，大体积冲刷有用信息 |
| **引入 blinker / 第三方事件库** | EventBus < 50 行自行实现，不值得引入新依赖 |

---

## 十一、验证方式

| 验证项 | 方法 |
|--------|------|
| Request ID 统一 | 同一请求在中间件、路由、摘要日志中 request_id 一致 |
| ContextVar 传播 | 流式处理路径日志携带 request_id；to_thread 场景有显式传递 |
| JSON 日志 | `LOG_FORMAT=json` 后日志为合法 JSON |
| Prometheus 指标 | `curl /metrics` 返回所有定义指标 |
| 请求摘要 | 发请求后 grep `request_summary` 可见完整摘要（含 worker_id, client_ip） |
| outcome 判定 | 模拟 InferTimeoutError → outcome=timeout；流式中途断连 → stream_partial |
| 健康检查 | `/health` 返回 200；`/health?deep=1` 返回所有推理服务状态 |
| EventBus 禁用 | `observability.disable()` 后 `/metrics` 返回空，业务不受影响 |
| 基数防护 | 1000 个随机 model 名 → `model=unknown` 只有一个序列 |
| 异常场景 | 推理不可达 → `infer_errors_total` 递增，`infer_retries_total` 递增 |

---

## 十二、完整指标清单

### 12.1 A 类：请求核心指标（4 个）

| 指标名 | 类型 | Label | 用途 |
|--------|------|-------|------|
| `trajproxy_requests_total` | Counter | `method, model, stream, outcome, run_id` | 请求计数 |
| `trajproxy_request_duration_seconds` | Histogram | `model, stream, run_id` | 端到端延迟 |
| `trajproxy_active_requests` | Gauge | — | 当前活跃请求 |
| `trajproxy_concurrency_utilization` | Gauge | — | 并发使用率 |

### 12.2 B 类：流式请求指标（2 个核心 + 1 个可选）

| 指标名 | 类型 | Label | 阶段 |
|--------|------|-------|------|
| `trajproxy_ttft_seconds` | Histogram | `model, run_id` | Phase 2 |
| `trajproxy_stream_completion_total` | Counter | `model, run_id, result` | Phase 2 |
| `trajproxy_stream_chunks_per_request` | Histogram | `model, run_id` | Phase 3+（可选） |

### 12.3 C 类：阶段级指标（1 个）

| 指标名 | 类型 | Label | 用途 |
|--------|------|-------|------|
| `trajproxy_phase_duration_seconds` | Histogram | `model, phase` | Pipeline 内部阶段 |

`phase`: `transform` / `encode` / `cache_db_query` / `cache_prefix_match` / `inference` / `decode`

> **注**：`store` phase 暂不纳入指标（ProcessContext 无 `store_duration_ms` 字段），如需可后续扩展。

### 12.4 D 类：Token 消耗指标（1 个）

| 指标名 | 类型 | Label | 用途 |
|--------|------|-------|------|
| `trajproxy_tokens_total` | Counter | `model, run_id, type` | Token 消耗累计 |

### 12.5 E 类：下游依赖指标（3+1 个）

| 指标名 | 类型 | Label | 用途 |
|--------|------|-------|------|
| `trajproxy_infer_duration_seconds` | Histogram | `model` | 推理延迟 |
| `trajproxy_infer_errors_total` | Counter | `model, error_type` | 推理错误 |
| `trajproxy_infer_retries_total` | Counter | `model, outcome` | **推理重试（新增）** |
| `trajproxy_db_pool_usage` | Gauge | `state` | DB 连接池 |

### 12.6 F 类：模型生命周期（可选）

| 指标名 | 类型 | Label | 阶段 |
|--------|------|-------|------|
| `trajproxy_model_lifecycle_total` | Counter | `action, type` | Phase 3+ |

### 12.7 G 类：进程级指标（ProcessCollector 自动暴露）

| 指标名 | 类型 | 来源 |
|--------|------|------|
| `process_cpu_seconds_total` | Counter | prometheus-client 内置 |
| `process_resident_memory_bytes` | Gauge | prometheus-client 内置 |
| `process_open_fds` | Gauge | prometheus-client 内置 |
| `process_start_time_seconds` | Gauge | prometheus-client 内置 |

### 12.8 指标总数汇总

| 类别 | Counter | Histogram | Gauge | 小计 | 阶段 |
|------|---------|-----------|-------|------|------|
| A. 请求核心 | 1 | 1 | 2 | 4 | Phase 2 |
| B. 流式 | 1 | 1 | 0 | 2 | Phase 2 |
| C. 阶段 | 0 | 1 | 0 | 1 | Phase 2 |
| D. Token | 1 | 0 | 0 | 1 | Phase 2 |
| E. 下游依赖 | 2 | 1 | 1 | 4 | Phase 2 |
| F. 生命周期 | 1 | 0 | 0 | 1 | Phase 3+ |
| G. 进程级 | 1 | 0 | 3 | 4 | Phase 2 |
| **核心合计** | **6** | **4** | **3** | **13** | |
| **含可选** | **7** | **5** | **3** | **15** | |

### 12.9 Outcome 枚举值定义

| outcome | 含义 | 典型场景 |
|---------|------|---------|
| `success` | 正常完成 | 非流式 200、流式完整返回 |
| `stream_partial` | 流式中途崩溃 | 首 Token 后推理断连 |
| `error_client` | 客户端错误 | 参数非法（HTTP 4xx） |
| `error_server` | TrajProxy 内部错误 | 代码异常（HTTP 5xx） |
| `error_infer` | 推理服务错误 | 502/连接失败 |
| `error_rate_limit` | 并发限流 | HTTP 429 |
| `timeout` | 推理超时 | InferTimeoutError (504) |

### 12.10 告警规则

```yaml
# prometheus/alert_rules.yml
groups:
  - name: trajproxy
    rules:
      # 1. 错误率过高（使用 outcome label，非 status）
      - alert: HighErrorRate
        expr: >
          sum by (node) (rate(trajproxy_requests_total{outcome=~"error_.+|timeout|stream_partial"}[5m]))
          / sum by (node) (rate(trajproxy_requests_total[5m])) > 0.05
        for: 5m
        labels: { severity: warning }
        annotations:
          summary: "{{ $labels.node }} 错误率 > 5%"

      # 2. P95 延迟过高
      - alert: HighLatency
        expr: >
          histogram_quantile(0.95,
            sum by (le, node) (rate(trajproxy_request_duration_seconds_bucket[5m]))
          ) > 60
        for: 5m
        labels: { severity: warning }

      # 3. 推理服务故障
      - alert: InferErrors
        expr: >
          sum by (model, node) (rate(trajproxy_infer_errors_total[5m])) > 0.5
        for: 3m
        labels: { severity: critical }

      # 4. 推理重试频繁（新增）
      - alert: InferRetriesHigh
        expr: >
          sum by (model) (rate(trajproxy_infer_retries_total[5m])) > 1
        for: 5m
        labels: { severity: warning }
        annotations:
          summary: "模型 {{ $labels.model }} 重试频率过高"

      # 5. DB 连接池饱和
      - alert: DBPoolSaturation
        expr: >
          trajproxy_db_pool_usage{state="used"}
          / trajproxy_db_pool_usage{state="peak"} > 0.85
        for: 5m
        labels: { severity: warning }

      # 6. Worker 不可达
      - alert: WorkerDown
        expr: up{job="trajproxy"} == 0
        for: 1m
        labels: { severity: critical }

      # 7. Worker 内存过高（新增）
      - alert: WorkerHighMemory
        expr: process_resident_memory_bytes > 2147483648  # 2GB
        for: 10m
        labels: { severity: warning }
```

---

## 十三、Grafana Dashboard 设计

### 13.1 部署形态分级

| Level | 适用规模 | Dashboard 面板数 | 基础设施 |
|-------|---------|----------------|---------|
| **Level 1** | 1-2 节点，1 人团队 | **9 个核心面板** | Prometheus + Grafana |
| **Level 2** | 3-5 节点 | **完整版 ~30 面板** | + Loki + AlertManager |
| **Level 3** | 5+ 节点 | 完整 + 自定义 | + Thanos/Mimir |

### 13.2 Level 1 Dashboard（9 个核心面板）

**Row 1：全局概览（6 个）**

| 面板 | 类型 | PromQL |
|------|------|--------|
| 全局 QPS | Stat | `sum(rate(trajproxy_requests_total[5m]))` |
| 平均延迟 | Stat | `sum(rate(..._sum[5m])) / sum(rate(..._count[5m]))` |
| 成功率% | Gauge | `sum(rate(...{outcome="success"}[5m])) / sum(rate(...[5m])) * 100` |
| 当前活跃 | Stat | `sum(trajproxy_active_requests)` |
| 1h 总量 | Stat | `sum(increase(trajproxy_requests_total[1h]))` |
| 并发利用率 | Gauge | `avg(trajproxy_concurrency_utilization) * 100` |

**Row 2：核心错误（3 个）**

| 面板 | 类型 | PromQL |
|------|------|--------|
| 错误率 by outcome | Stacked Time Series | `sum by (outcome) (rate(...{outcome=~"error_.+"}[5m]))` |
| 推理错误 by model | Table | `sum by (model) (increase(trajproxy_infer_errors_total[1h]))` |
| 推理重试 trend | Time Series | `sum(rate(trajproxy_infer_retries_total[5m]))` |

### 13.3 Level 2+ 完整面板（追加约 20 个）

完整面板按 "概览 → 负载 → 性能 → 错误 → 系统" 五行布局：

- **Row 2 负载**：QPS by model/run_id/outcome/stream、Token by run_id
- **Row 3 性能**：延迟 P50/P95/P99、TTFT、阶段耗时趋势、推理延迟
- **Row 4 错误**（扩展）：错误率 run_id、流式完整率、429 限流、生命周期事件
- **Row 5 系统**：DB 连接池（按节点标识）、Worker up/down、QPS by 实例、Worker 内存 RSS

### 13.4 面板布局示意

```
┌──────────────────── Grafana Dashboard (Level 1) ────────────────────┐
│                                                                      │
│  ┌── Row 1 概览 ──────────────────────────────────────────────┐     │
│  │ [QPS] [延迟] [成功率%] [活跃] [1h总量] [并发利用率%]         │     │
│  └────────────────────────────────────────────────────────────┘     │
│                                                                      │
│  ┌── Row 2 核心错误 ─────────────────────────────────────────┐     │
│  │ [错误率 by outcome]  [推理错误 by model]  [重试趋势]        │     │
│  └────────────────────────────────────────────────────────────┘     │
│                                                                      │
│  (Level 2+ 追加 Row 3-5: 负载/性能/系统)                            │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 十四、业务场景 → 面板/指标映射

### 14.1 宏观健康度

| 问题 | 面板 | PromQL |
|------|------|--------|
| 整体服务健不健康？ | Row 1 | 成功率 + QPS + 延迟 |
| 最近处理了多少请求？ | Row 1 | `increase(...[1h])` |

### 14.2 负载归因（Level 2+）

| 问题 | 面板 | PromQL |
|------|------|--------|
| 哪个模型负载最高？ | Row 2 负载 | `sum by (model) (rate(...))` |
| 哪个作业在消耗资源？ | Row 2 负载 | `sum by (run_id) (rate(...))` |
| Token 消耗排行 | Row 2 负载 | `sum by (run_id) (increase(tokens_total[1h]))` |

### 14.3 性能定位（Level 2+）

| 问题 | 面板 | PromQL |
|------|------|--------|
| P95 延迟？ | Row 3 | `histogram_quantile(0.95, ...)` |
| 哪个阶段是瓶颈？ | Row 3 | phase_duration by phase |
| 推理服务延迟？ | Row 3 | infer_duration P95 |

### 14.4 失败归因

| 问题 | 面板/指标 | 说明 |
|------|----------|------|
| 多少请求真正成功？ | Row 1 成功率 | `outcome="success"` |
| 失败归因 | Row 2 错误 | outcome stacked |
| 是推理慢还是重试多？ | **推理重试 trend（新增）** | `infer_retries_total` vs `infer_duration_seconds` |
| 哪个 run_id 最不稳定？ | Level 2+ | 错误率 by run_id |

### 14.5 定位能力边界

**本方案能定位**：
- 推理服务挂了 / DB 连接池瓶颈
- Pipeline 各阶段耗时异常 / 并发限流
- 推理重试频繁 / 特定 run_id 的高错误率

**本方案不能直接定位**（诚实说明）：

| 场景 | 局限 | 缓解方式 |
|------|------|---------|
| 具体哪条 SQL 慢 | 只知道 `store` phase 长 | 可在 RequestRepository 加 SQL 级日志 |
| 推理服务内部排队 | 只知道端到端延迟 | 推理服务侧需有自己的 metrics |
| Tokenizer CPU 热点 | 知道 `encode` 慢，不知内部 | 需 py-spy/pyroscope |
| 单请求调用链 | 无分布式追踪 | 通过 request_id grep 摘要日志 |

---

## 十五、部署方案

### 15.1 组件全景

```
┌─────────────────────────────────────────────────────────────┐
│                      TrajProxy Workers                        │
│  (暴露 /metrics, /health, /health?deep=1; 输出 JSON 日志)    │
└────────────┬──────────────────────┬──────────────────────────┘
             │                      │
     /metrics (pull)           stdout / log 文件
             │                      │
             ▼                      ▼
      ┌──────────┐           ┌──────────────┐
      │Prometheus│           │ Vector/日志   │
      │  (tsdb)  │           │ 采集器       │
      └────┬─────┘           └──────┬───────┘
           │                       │
           ▼                       ▼
     ┌──────────┐            ┌───────────┐
     │ Grafana  │◄───────────│   Loki    │
     └────┬─────┘            └───────────┘
          │
          ▼
    ┌───────────┐
    │  Alert    │──► 钉钉/邮件
    │  Manager  │
    └───────────┘
```

### 15.2 部署步骤

**TrajProxy 应用层**：
1. `pip install prometheus-client`
2. 部署代码
3. 设置 `LOG_FORMAT=json`
4. 生成 `prometheus/targets.json`
5. 重启 Workers

**监控节点**（一次性）：

| 组件 | 资源 |
|------|------|
| Prometheus | CPU < 1, RAM 2-4GB, Disk 50-200GB |
| Grafana | CPU < 0.5, RAM 500MB |
| Loki（可选）| CPU < 1, RAM 1-2GB |
| AlertManager（可选）| CPU < 0.2, RAM 200MB |

### 15.3 Prometheus 多节点配置

```yaml
# prometheus.yml
scrape_configs:
  - job_name: "trajproxy"
    file_sd_configs:
      - files: ["targets.json"]
        refresh_interval: 30s
```

```json
// prometheus/targets.json
[
  {"targets": ["node-a:12300", "node-a:12301", "..."], "labels": {"job": "trajproxy", "node": "a"}},
  {"targets": ["node-b:12300", "node-b:12301", "..."], "labels": {"job": "trajproxy", "node": "b"}}
]
```

---

## 十六、总结

### 核心改进

| 模块 | 解决问题 | 解耦方式 |
|------|---------|---------|
| **EventBus + 解耦架构** | 观测逻辑与业务逻辑分离 | 订阅者模式，业务代码不感知 |
| Request ID 统一 | 跨层日志无法关联 | 中间件 + ContextVar |
| 结构化 JSON 日志 | 日志无法被机器解析 | 环境变量切换 |
| Prometheus 指标（7 种 outcome + retry + 信号量 + 存储失败） | 无法量化 QPS/延迟/错误率 | 五层拦截 + 装饰器 |
| 429 限流指标 | 并发饱和不可见 | routes 层独立 emit（P0 修复） |
| ProcessContext 字段守卫 | 字段重命名导致指标静默消失 | 启动时必填字段校验 |
| 请求摘要日志 | 单请求生命周期散乱 | EventBus 订阅者 |
| 增强健康检查 | 依赖故障无预警 | base_url 去重 + 可配置检查路径 |

### 关键数字

| 维度 | V1（原方案） | V2（V1+P0/P1/P2 修复版） |
|------|------------|------------------------|
| Prometheus 核心指标 | 14 | **16 核心 + 3 可选**（新增 signal/rejected/store_error/disconnect） |
| 业务文件修改量 | ~53 行 / 6 文件 | **~41 行 / 7 文件** |
| Pipeline 层修改 | 需改动 base.py | **+4 行**（轨迹存储 emit，P1 修复） |
| outcome 判定 | 中侵入（写入 context.outcome） | **零侵入**（纯函数推导 + routes 层独立记录 429） |
| 可独立禁用 | 否 | **是**（`observability.disable()`，进程级） |
| 新增依赖 | 2 个 | **1 个**（prometheus-client） |
| 每 Worker 内存增量 | < 15MB | < 10MB（~14000 序列） |
| 新增文件 | 5 | 11（均为 observability/ 目录，模块化） |
| 预计实施工作量 | 5-8 天 | 7-10 天（含 P0/P1 修复） |
| Grafana 面板（Level 1）| 30 个 | **9 个核心面板** |

### 核心价值

```
五层拦截（Middleware + Routes + Processor + InferClient + Pipeline）
  × EventBus × outcome 纯函数 × 字段守卫
  = 业务代码极低侵入 + 观测可独立演进/禁用/测试
  = 429 限流、轨迹存储失败、信号量等待均可观测
  = 回答"有多少请求 / 谁在用 / 有多快 / 失败在哪 / 卡在哪 / 资源够不够"
  = 定位错误边界：客户端层 vs TrajProxy 层 vs 推理服务层 vs DB 存储层
```