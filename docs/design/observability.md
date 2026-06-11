# TrajProxy 可观测性方案

> **状态**: 待审核
> **作者**: AI Assistant
> **日期**: 2026-06-11

---

## 一、现状分析与核心问题

### 1.1 当前能力

| 能力 | 现状 | 评级 |
|------|------|------|
| 请求日志 | 纯文本 f-string，~100+ 处散落，两套 request_id | ⭐⭐⭐ |
| 分阶段耗时 | ProcessContext 记录各阶段 ms | ⭐⭐⭐⭐ |
| 健康检查 | `{"status": "ok"}`，无依赖检查 | ⭐ |
| Metrics | **完全没有** | ❌ |
| Tracing | **完全没有** | ❌ |
| 请求聚合/统计 | **完全没有**（只能查 DB） | ❌ |

### 1.2 核心痛点

| 痛点 | 影响 |
|------|------|
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
5. **零侵入** — 观测逻辑通过中间件/装饰器注入，不改变业务代码路径

---

## 三、方案总览

```
┌─────────────────────────────────────────────────────────────────┐
│                        Obs 方案五大模块                          │
│                                                                  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐│
│  │ Request  │ │ 结构化   │ │Prometheus│ │ 请求摘要 │ │ 增强   ││
│  │ ID 统一  │ │ JSON日志 │ │ Metrics  │ │ Summary  │ │ 健康   ││
│  │          │ │          │ │          │ │ Log      │ │ 检查   ││
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └───┬────┘│
│       │             │            │             │           │     │
│       │     ┌───────┴────────────┴─────────────┴───────────┘     │
│       │     │              observability/ 模块                   │
│       │     │  ├── request_context.py  (ContextVar 传播)        │
│       │     │  ├── metrics_collector.py(Prometheus 指标)        │
│       │     │  ├── request_summary.py  (摘要日志)               │
│       │     │  ├── json_formatter.py   (JSON 日志格式)          │
│       │     │  └── health_checker.py   (健康检查)               │
│       └─────┘                                                    │
└─────────────────────────────────────────────────────────────────┘
```

**新增依赖**：`prometheus-client`、`python-json-logger`（仅此两个）

---

## 四、模块详细设计

### 4.1 Request ID 统一

**问题**：中间件生成 `str(uuid.uuid4())[:8]`（8位），路由层生成 `str(uuid.uuid4())`（完整），两者无法关联。

**方案**：中间件生成唯一 request_id，通过 `request.state` 传递给路由层。

```python
# workers/worker.py - request_logging_middleware
@self.app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    start_time = time.time()
    # 统一使用完整 UUID，响应头仍返回短 ID 便于人眼查看
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id          # ← 传递给路由层
    request.state.request_start_time = start_time   # ← 传递起始时间

    # ...

    response.headers["X-Request-ID"] = request_id[:8]  # 短 ID 给客户端
    return response
```

```python
# serve/routes.py - chat_completions
async def chat_completions(request: Request, ...):
    request_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())
    # 路由层不再自己生成，复用中间件 ID
```

**影响范围**：
- `workers/worker.py`：中间件生成并注入 `request.state.request_id`
- `serve/routes.py`：路由层从 `request.state` 取 request_id，不再独立生成

---

### 4.2 结构化 JSON 日志

**目标**：日志可被 ELK/Loki/Grafana Loki 等系统直接解析索引。

**方案**：新增 `observability/json_formatter.py`，提供 JSON 格式的 `logging.Formatter`。

```python
# observability/json_formatter.py
import json
import logging
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    """JSON 格式日志输出器，兼容日志聚合系统"""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "module": record.module,
            "worker_id": getattr(record, "worker_id", "-"),
            "message": record.getMessage(),
        }
        # 附加 request_id（如果存在）
        request_id = getattr(record, "request_id", None)
        if request_id:
            log_entry["request_id"] = request_id
        # 附加 exception
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)
```

**配置切换**：通过环境变量 `LOG_FORMAT=json` 启用（默认保持 `text`）。

```python
# utils/logger.py 改动
def get_logger(name, log_level=None, log_dir=None, worker_id=None):
    # ...
    log_format = os.getenv("LOG_FORMAT", "text")  # text | json

    if log_format == "json":
        from traj_proxy.observability.json_formatter import JsonFormatter
        formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(
            '%(asctime)s | %(levelname)s | %(module)s | %(worker_id)s | %(message)s'
        )
```

**request_id 注入**：利用 `logging.Filter` 将 request_id 注入到每条日志记录：

```python
class RequestIDFilter(logging.Filter):
    """从 ContextVar 获取 request_id，注入日志记录"""

    def filter(self, record: logging.LogRecord) -> bool:
        from traj_proxy.observability.request_context import get_request_id
        record.request_id = get_request_id("")  # 空字符串表示无请求上下文
        return True
```

**request_id 上下文传播**：使用 `contextvars.ContextVar`，在中间件层设置，全链路自动传播：

```python
# observability/request_context.py
from contextvars import ContextVar

_request_id: ContextVar[str] = ContextVar("request_id", default="")


def set_request_id(request_id: str) -> None:
    _request_id.set(request_id)


def get_request_id(default: str = "") -> str:
    return _request_id.get(default)
```

```python
# workers/worker.py - 中间件中设置
from traj_proxy.observability.request_context import set_request_id

@self.app.middleware("http")
async def request_logging_middleware(request, call_next):
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    set_request_id(request_id)  # ← ContextVar 设置，async 自动传播
    # ...
```

这样全链路（路由 → Processor → Pipeline → InferClient → DB）的日志自动携带 request_id，无需手动传参。

---

### 4.3 Prometheus Metrics

**目标**：提供量化观测能力 — QPS、延迟分布、错误率、Token 消耗、推理延迟。

**新增依赖**：`prometheus-client`

**核心指标设计**：

```python
# observability/metrics_collector.py
from prometheus_client import Counter, Histogram, Gauge


# ===== 请求级指标 =====

REQUEST_TOTAL = Counter(
    "trajproxy_requests_total",
    "Total number of requests",
    ["method", "model", "status", "stream"]    # 按模型、状态码、流/非流 分桶
)

REQUEST_DURATION = Histogram(
    "trajproxy_request_duration_seconds",
    "Request end-to-end duration",
    ["model", "stream"],
    buckets=[0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60]  # 覆盖 100ms~60s
)

# ===== 阶段级指标（Pipeline 内部耗时）=====

PHASE_DURATION = Histogram(
    "trajproxy_phase_duration_seconds",
    "Duration of processing phases",
    ["model", "phase"],                        # phase: transform/encode/inference/decode/store
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10]
)

# ===== Token 指标 =====

TOKENS_TOTAL = Counter(
    "trajproxy_tokens_total",
    "Total tokens processed",
    ["model", "type"]                          # type: prompt/completion/cached
)

# ===== 流式指标 =====

TTFT_SECONDS = Histogram(
    "trajproxy_ttft_seconds",
    "Time to first token (streaming)",
    ["model"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1, 2, 5]
)

# ===== 系统指标 =====

ACTIVE_REQUESTS = Gauge(
    "trajproxy_active_requests",
    "Currently active requests"
)

MODELS_REGISTERED = Gauge(
    "trajproxy_models_registered",
    "Number of registered models",
    ["type"]                                   # type: static/dynamic
)

DB_POOL_USAGE = Gauge(
    "trajproxy_db_pool_usage",
    "Database connection pool usage",
    ["state"]                                  # state: used/available/peak
)

# ===== 推理服务指标 =====

INFER_DURATION = Histogram(
    "trajproxy_infer_duration_seconds",
    "Inference service call duration",
    ["model"],
    buckets=[0.1, 0.5, 1, 2, 5, 10, 30, 60]
)

INFER_ERRORS = Counter(
    "trajproxy_infer_errors_total",
    "Inference service errors",
    ["model", "error_type"]                    # error_type: timeout/connection/http_error
)
```

**埋点位置与策略**：

| 指标 | 埋点位置 | 方式 |
|------|---------|------|
| `REQUEST_TOTAL` / `REQUEST_DURATION` / `ACTIVE_REQUESTS` | `request_logging_middleware` | 中间件层，request/response 前后 |
| `PHASE_DURATION` | `base.py._update_timing()` + Pipeline 各阶段 | 从 ProcessContext 读取耗时 |
| `TOKENS_TOTAL` | Pipeline 完成时（`_store_trajectory` 之后） | 从 ProcessContext 读取 token 数 |
| `TTFT_SECONDS` | Pipeline 首 Token 产生时 | 从 ProcessContext.ttft_ms 读取 |
| `INFER_DURATION` / `INFER_ERRORS` | `InferClient` 调用前后 | 在 InferClient 的 send_* 方法中 |
| `DB_POOL_USAGE` | `DatabaseManager._monitor_pool()` | 复用已有的监控逻辑 |
| `MODELS_REGISTERED` | `ProcessorManager` 注册/删除时 | 在 register/unregister 方法中 |

**Metrics 端点注册**：

```python
# workers/route_registrar.py
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

@self.app.get("/metrics", tags=["Observability"])
async def metrics():
    """Prometheus metrics endpoint"""
    from starlette.responses import Response
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
```

**Prometheus 采集配置（多节点部署方案）**：

每个 ProxyWorker 在各自端口暴露 `/metrics`（12300, 12301, ...），不做 WorkerManager 聚合。

**理由**：当前部署为多节点静态部署（Docker/裸机），单节点 10+ Workers。如果在应用层做聚合，需要跨 Ray 进程拉取指标、合并注册表、处理 Worker 动态启停，复杂度高且收益低。50~100 targets 对 Prometheus 毫无性能压力（轻松处理数万 target）。

部署时自动生成 targets 文件，Prometheus 热加载：

```yaml
# prometheus/targets.json — 部署脚本自动生成
[
  {
    "targets": [
      "node-1:12300", "node-1:12301", "node-1:12302", "...",
      "node-2:12300", "node-2:12301", "...",
      "node-3:12300", "node-3:12301", "..."
    ],
    "labels": { "job": "trajproxy-workers" }
  }
]
```

```yaml
# prometheus.yml
scrape_configs:
  - job_name: "trajproxy"
    file_sd_configs:
      - files: ["targets.json"]
        refresh_interval: 30s

    # 跨 Worker/节点聚合（PromQL 示例）
    # 全局 QPS:          rate(trajproxy_requests_total[5m])
    # 按模型 QPS:        sum by (model) (rate(trajproxy_requests_total[5m]))
    # P95 延迟:          histogram_quantile(0.95, sum by (le) (rate(trajproxy_request_duration_seconds_bucket[5m])))
    # 推理服务错误率:    sum by (model) (rate(trajproxy_infer_errors_total[5m])) / sum by (model) (rate(trajproxy_requests_total[5m]))
```

> **不做的事**：
> - 不引入 Pushgateway（Worker 是常驻服务，不需要推送模式）
> - 不做 WorkerManager 聚合（运维复杂度高于维护 targets.json 的收益）
> - 不引入 OpenTelemetry（当前链路短，Prometheus + 摘要日志足够）

---

### 4.4 请求摘要日志（Request Summary Log）

**目标**：每个请求产生一条结构化摘要日志，一眼可见完整生命周期，替代散落的多行日志。

**方案**：在 Pipeline/BasePipeline 处理结束时，输出统一格式的摘要日志。

```python
# observability/request_summary.py
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


def emit_request_summary(
    request_id: str,
    model: str,
    stream: bool,
    status: str,                              # "ok" | "error"
    duration_ms: float,
    run_id: str = "",
    session_id: str = "",
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
    cache_hit_tokens: Optional[int] = None,
    ttft_ms: Optional[float] = None,
    inference_duration_ms: Optional[float] = None,
    transform_duration_ms: Optional[float] = None,
    encode_duration_ms: Optional[float] = None,
    decode_duration_ms: Optional[float] = None,
    error_type: Optional[str] = None,
    error_message: Optional[str] = None,
) -> None:
    """输出一条结构化请求摘要日志"""
    summary = {
        "request_id": request_id,
        "model": model,
        "stream": stream,
        "status": status,
        "duration_ms": round(duration_ms, 1),
    }
    # 可选字段（非 None 时才加入）
    optional = {
        "run_id": run_id,
        "session_id": session_id,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cache_hit_tokens": cache_hit_tokens,
        "ttft_ms": round(ttft_ms, 1) if ttft_ms else None,
        "inference_ms": round(inference_duration_ms, 1) if inference_duration_ms else None,
        "transform_ms": round(transform_duration_ms, 1) if transform_duration_ms else None,
        "encode_ms": round(encode_duration_ms, 1) if encode_duration_ms else None,
        "decode_ms": round(decode_duration_ms, 1) if decode_duration_ms else None,
        "error_type": error_type,
        "error_message": error_message,
    }
    summary.update({k: v for k, v in optional.items() if v is not None})

    if status == "error":
        logger.error("request_summary", extra={"summary": summary})
    else:
        logger.info("request_summary", extra={"summary": summary})
```

**埋点位置**：`BasePipeline` 的 `_update_timing` 之后调用 `emit_request_summary`。

```python
# pipeline/base.py - process() 末尾
# 在 _store_trajectory 之后
emit_request_summary(
    request_id=context.request_id,
    model=context.model,
    stream=context.is_stream,
    status="error" if context.error else "ok",
    duration_ms=context.processing_duration_ms,
    run_id=context.run_id or "",
    session_id=context.session_id or "",
    prompt_tokens=context.prompt_tokens,
    completion_tokens=context.completion_tokens,
    cache_hit_tokens=context.cache_hit_tokens,
    ttft_ms=context.ttft_ms,
    inference_duration_ms=context.inference_duration_ms,
    transform_duration_ms=context.transform_duration_ms,
    encode_duration_ms=context.encode_duration_ms,
    decode_duration_ms=context.decode_duration_ms,
    error_type=type(context.error).__name__ if context.error else None,
    error_message=str(context.error) if context.error else None,
)
```

**输出示例**（JSON 格式下）：

```json
{
  "timestamp": "2026-06-11T08:30:15.123+00:00",
  "level": "INFO",
  "module": "request_summary",
  "worker_id": "worker-0",
  "message": "request_summary",
  "summary": {
    "request_id": "a1b2c3d4-e5f6-...",
    "model": "qwen-72b",
    "stream": false,
    "status": "ok",
    "duration_ms": 3247.5,
    "run_id": "exp-001",
    "session_id": "sess-abc",
    "prompt_tokens": 1024,
    "completion_tokens": 256,
    "cache_hit_tokens": 768,
    "inference_ms": 2800.0,
    "encode_ms": 150.0,
    "ttft_ms": null
  }
}
```

**价值**：一条日志 = 完整请求画像。可直接导入日志系统做聚合分析（P95 延迟、慢请求 TopN、模型错误率排行等）。

---

### 4.5 增强健康检查

**目标**：提供分层健康检查，快速判断依赖是否可用。

**方案**：扩展 `GET /health` 端点，增加可选的 `?deep=1` 参数做深度检查。

```python
# workers/route_registrar.py
@self.app.get("/health", tags=["Health"])
async def health(request: Request, deep: bool = False):
    """
    健康检查端点
    
    - 默认(GET /health): 轻量级，检查进程存活
    - 深度(GET /health?deep=1): 检查 DB 连接、推理服务连通性
    """
    from traj_proxy.observability.health_checker import HealthChecker

    checker = HealthChecker(request.app)

    if deep:
        result = await checker.deep_check()
    else:
        result = checker.shallow_check()

    status_code = 200 if result["status"] == "healthy" else 503
    return JSONResponse(content=result, status_code=status_code)
```

```python
# observability/health_checker.py
import logging
import time
from typing import Any, Dict

logger = logging.getLogger(__name__)


class HealthChecker:
    """分层健康检查器"""

    def __init__(self, app):
        self.app = app

    def shallow_check(self) -> Dict[str, Any]:
        """轻量级检查：进程存活 + 基本状态"""
        return {
            "status": "healthy",
            "timestamp": time.time(),
            "worker_id": getattr(self.app.state, "worker_id", "unknown"),
            "processor_count": len(
                getattr(self.app.state, "processor_manager", None)
                and self.app.state.processor_manager.list_models() or []
            ),
        }

    async def deep_check(self) -> Dict[str, Any]:
        """深度检查：DB 连接 + 推理服务可达性"""
        checks: Dict[str, Any] = {}
        overall_healthy = True

        # 1. DB 连接池检查
        db_manager = getattr(self.app.state, "db_manager", None) if hasattr(self.app, "state") else None
        # 注意: db_manager 挂在 worker 实例上，需要适配
        if db_manager:
            try:
                async with db_manager.pool.connection(timeout=2) as conn:
                    await conn.execute("SELECT 1")
                checks["database"] = {"status": "healthy"}
            except Exception as e:
                checks["database"] = {"status": "unhealthy", "error": str(e)}
                overall_healthy = False
        else:
            checks["database"] = {"status": "unknown", "error": "db_manager not initialized"}
            overall_healthy = False

        # 2. ProcessorManager 检查
        processor_manager = getattr(self.app.state, "processor_manager", None)
        if processor_manager:
            models = processor_manager.list_models()
            checks["processors"] = {
                "status": "healthy",
                "total": len(models),
            }
        else:
            checks["processors"] = {"status": "unknown"}

        # 3. 推理服务连通性（仅检查第一个模型的 URL 是否可达）
        infer_ok = True
        if processor_manager:
            processed_info = processor_manager.get_all_processors_info()
            for info in processed_info[:3]:  # 最多检查 3 个
                url = info.get("url", "")
                if url:
                    try:
                        import httpx
                        async with httpx.AsyncClient(timeout=3) as client:
                            resp = await client.get(f"{url}/health")
                            if resp.status_code >= 500:
                                infer_ok = False
                                checks.setdefault("infer_services", {})["errors"] = (
                                    checks.get("infer_services", {}).get("errors", [])
                                    + [f"{info['model_name']}: HTTP {resp.status_code}"]
                                )
                    except Exception as e:
                        infer_ok = False
                        checks.setdefault("infer_services", {})["errors"] = (
                            checks.get("infer_services", {}).get("errors", [])
                            + [f"{info['model_name']}: {str(e)[:100]}"]
                        )
        if "infer_services" not in checks:
            checks["infer_services"] = {"status": "healthy" if infer_ok else "unhealthy"}
        else:
            checks["infer_services"]["status"] = "unhealthy" if not infer_ok else "degraded"

        return {
            "status": "healthy" if overall_healthy and infer_ok else "degraded",
            "timestamp": time.time(),
            "worker_id": getattr(self.app.state, "worker_id", "unknown"),
            "checks": checks,
        }
```

**响应示例**（深度检查）：

```json
{
  "status": "degraded",
  "timestamp": 1718091015.123,
  "worker_id": "worker-0",
  "checks": {
    "database": {"status": "healthy"},
    "processors": {"status": "healthy", "total": 5},
    "infer_services": {
      "status": "degraded",
      "errors": ["qwen-72b: Connection timeout"]
    }
  }
}
```

---

## 五、目录结构

```
traj_proxy/
├── observability/                          # ← 新增模块
│   ├── __init__.py
│   ├── request_context.py                 # ContextVar request_id 传播
│   ├── json_formatter.py                  # JSON 日志格式器
│   ├── metrics_collector.py               # Prometheus 指标定义 + 埋点辅助函数
│   ├── request_summary.py                 # 请求摘要日志
│   └── health_checker.py                  # 分层健康检查
├── utils/
│   └── logger.py                          # 改动：支持 JSON 格式切换
├── workers/
│   ├── worker.py                          # 改动：中间件设置 ContextVar + request.state
│   └── route_registrar.py                 # 改动：注册 /metrics 端点 + 增强 /health
├── proxy_core/
│   ├── pipeline/
│   │   └── base.py                        # 改动：_update_timing 后调用 emit_request_summary
│   └── infer_client.py                    # 改动：INFER_DURATION / INFER_ERRORS 埋点
└── serve/
    └── routes.py                          # 改动：从 request.state 获取 request_id
```

**新增文件 5 个，修改文件 5 个，新增依赖 2 个。**

---

## 六、实施路线

### Phase 1：基础（1-2 天）

- [ ] 新增 `observability/` 模块骨架
- [ ] 实现 `request_context.py`（ContextVar 传播）
- [ ] 实现 `json_formatter.py`
- [ ] 修改 `utils/logger.py` 支持 `LOG_FORMAT=json` 切换
- [ ] Request ID 统一（中间件 → `request.state` → 路由层）
- [ ] 中间件设置 ContextVar

> **价值**：日志可被聚合系统解析，request_id 全链路可追溯。

### Phase 2：Metrics（2-3 天）

- [ ] 实现 `metrics_collector.py`（核心指标定义）
- [ ] 中间件层埋点（REQUEST_TOTAL / REQUEST_DURATION / ACTIVE_REQUESTS）
- [ ] InferClient 埋点（INFER_DURATION / INFER_ERRORS）
- [ ] Pipeline 阶段埋点（PHASE_DURATION / TOKENS_TOTAL / TTFT_SECONDS）
- [ ] 注册 `/metrics` 端点
- [ ] `requirements.txt` 添加 `prometheus-client`

> **价值**：可量化 QPS、延迟分布、错误率；可接入 Grafana 做可视化告警。

### Phase 3：增强（1-2 天）

- [ ] 实现 `request_summary.py`
- [ ] Pipeline 处理完成时输出摘要日志
- [ ] 实现 `health_checker.py`
- [ ] 增强 `/health` 端点

> **价值**：单请求快速可观测；依赖健康状态可监控。

---

## 七、配置项

```yaml
# configs/config.yaml 新增
observability:
  log_format: text                    # text | json，环境变量 LOG_FORMAT 可覆盖
  metrics_enabled: true               # 是否启用 Prometheus metrics
  health_deep_check: false            # 健康检查默认是否做深度检查
```

---

## 八、数据存储与内存分析

### 8.1 各模块数据持有情况

| 模块 | 进程内持久持有 | 持有内容 | 生命周期 | 内存风险 |
|------|-------------|---------|---------|---------|
| `request_context` | 否 | 当前请求的 `request_id` | 请求级，async context 结束自动回收 | 无 |
| `json_formatter` | 否 | 无状态 | — | 无 |
| `request_summary` | 否 | 无状态，调用 `logger.info()` 后丢弃 | — | 无 |
| `health_checker` | 否 | 无状态，每次即时查询 | — | 无 |
| **`metrics_collector`** | **是** | 时间序列注册表（prometheus-client 进程内 registry） | **进程生命周期，重启清零** | **需见下文** |

**核心结论**：除 Prometheus 指标注册表外，方案不引入任何进程内数据持有，不存在内存累积。

### 8.2 Prometheus 指标内存模型

`prometheus-client` 的内存模型：**每出现一个唯一的 label 值组合，就创建一个序列对象，进程生命周期内不释放。**

各对象内存开销：

| 指标类型 | 单序列内存 | 组成 |
|---------|-----------|------|
| `Counter` | ~几百字节 | 一个 `float` 值 |
| `Gauge` | ~几百字节 | 一个 `float` 值 |
| `Histogram`（N buckets） | ~几百字节 × N | N 个 bucket counter + sum + count |

**本方案序列数量估算**（假设 50 个注册模型）：

| 指标 | label 组合 | 序列数 |
|------|-----------|-------|
| `REQUEST_TOTAL(method × model × status × stream)` | 3 × 50 × 5 × 2 | ~1500 |
| `REQUEST_DURATION(model × stream)` | 50 × 2，每序列 11 buckets | ~100 |
| `PHASE_DURATION(model × phase)` | 50 × 5，每序列 10 buckets | ~250 |
| `TOKENS_TOTAL(model × type)` | 50 × 3 | ~150 |
| `TTFT_SECONDS(model)` | 50，每序列 8 buckets | ~50 |
| `INFER_DURATION(model)` | 50，每序列 9 buckets | ~50 |
| `INFER_ERRORS(model × error_type)` | 50 × 3 | ~150 |
| `MODELS_REGISTERED`, `DB_POOL_USAGE`, `ACTIVE_REQUESTS` | 固定 | ~10 |
| **合计** | | **~2260 序列，< 2MB** |

进程重启后全部清零，无持久化负担。

### 8.3 基数爆炸防护（必须遵守）

**禁止行为**：

```python
# ❌ 禁止：用 request_id 做 label — 每请求一个序列，无限增长
Counter("trajproxy_requests_total", ["request_id", "model"])

# ❌ 禁止：用 session_id / run_id 做 label — 高基数且不可预测
Histogram("...", ["model", "session_id"])

# ❌ 禁止：用原始异常消息做 label — 基数不可控
Counter("...", ["model", "error_message"])  # ← 每条异常消息不同
```

**正确做法**：将高基数数据放入**日志**，只将有限枚举值放入 label：

```python
# ✅ 正确：label 仅限已知枚举值
INFER_ERRORS = Counter("...", ["model", "error_type"])  # error_type: timeout | connection | http_error
# 详细异常消息通过 logger.error() 输出

# ✅ 正确：未知 model 名统一归入 "unknown"，防止恶意 model 名制造序列
def safe_model_label(model: str, known_models: set[str]) -> str:
    return model if model in known_models else "unknown"
```

**基数上限约束**：

| label | 允许值范围 | 基数上限 |
|-------|-----------|---------|
| `model` | ProcessorManager 中已注册的 model_name；未注册值归为 `"unknown"` | ≤ 注册数 + 1 |
| `method` | `POST`, `GET`, `DELETE`, `OTHER` | ≤ 4 |
| `status` | `200`, `400`, `404`, `500`, `502` 等整数分组 | ≤ 10 |
| `stream` | `true`, `false` | 2 |
| `phase` | `transform`, `encode`, `inference`, `decode`, `store` | 5 |
| `type` (token) | `prompt`, `completion`, `cached` | 3 |
| `error_type` | `timeout`, `connection`, `http_error` | 3 |
| `state` (db pool) | `used`, `available`, `peak` | 3 |

**`model` label 防护实现**（`metrics_collector.py` 中提供）：

```python
def safe_model_label(model: str) -> str:
    """将 model 名归一化：未注册模型统一为 'unknown'，防止未知 model 名制造时间序列"""
    from traj_proxy.proxy_core.processor_manager import ProcessorManager
    # 通过全局或 app.state 获取 ProcessorManager 的 known_models 集合
    known = ProcessorManager.get_known_model_set()  # 类方法，O(1) 查询
    return model if model in known else "unknown"
```

### 8.4 内存泄露验证

实施时必须通过以下测试确认无内存泄露：

1. **基数稳定性测试**：运行 1 小时压测后，检查 `GET /metrics` 输出的序列数量不再增长（稳定在预期值附近）
2. **未知 model 攻击测试**：用 `curl` 发送 1000 个不同随机 model 名的请求，确认 `model=unknown` 的序列只有一个
3. **进程重启后清零**：压测中重启进程，确认序列数从 0 重新开始
4. **基线内存监控**：对比有无 metrics 模块的 Worker 基准内存占用，增量应在 < 5MB / Worker 以内

### 8.5 数据存储总结

```
本方案不引入任何自建持久化存储：
  - Prometheus 数据 → prometheus-client 进程内 registry（重启清零，< 2MB）
  - 日志数据        → 写入文件/标准输出（已有 RotatingFileHandler 管理）
  - 请求详情        → 已有 RequestRepository → PostgreSQL（无新增）
  - 健康状态        → 即时查询，不持久化
```

---

## 九、不做的事（有意识排除）

| 方案 | 不采用的原因 |
|------|-------------|
| **OpenTelemetry 分布式追踪** | 当前架构为单服务代理+推理服务，链路短，Prometheus + 摘要日志足够；引入 OTel 增加运维复杂度但不解决核心问题 |
| **ELK/Loki 集成代码** | 日志格式改为 JSON 即可被任意聚合系统消费，不在应用层做集成 |
| **实时告警** | 告警逻辑由 Prometheus AlertManager / Grafana 端处理，应用层只负责指标暴露 |
| **跨 Worker 指标聚合** | 各 Worker 独立暴露 `/metrics`，由 Prometheus 服务端聚合；不引入 Pushgateway |
| **自定义 Dashboard** | 提供指标和 Grafana dashboard JSON 模板即可，不在应用层嵌入 UI |
| **请求体/响应体日志** | 安全规则禁止，且大体积内容会冲刷有用信息 |

---

## 十、验证方式

| 验证项 | 方法 |
|--------|------|
| Request ID 统一 | 同一请求在中间件日志、路由日志、摘要日志中 request_id 一致 |
| JSON 日志格式 | `LOG_FORMAT=json` 启动后，日志输出为合法 JSON |
| Prometheus 指标 | `curl http://localhost:12300/metrics` 返回所有定义的指标 |
| 请求摘要日志 | 发送一个请求后，grep `request_summary` 可见完整摘要 |
| 健康检查 | `curl /health` 返回 200；`curl /health?deep=1` 返回 DB/推理服务状态 |
| 异常场景 | 模拟推理服务不可达 → 指标 `trajproxy_infer_errors_total` 递增，摘要日志 status=error |

---

## 十一、完整部署方案

### 11.1 组件全景

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           可观测性部署全景                               │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────┐       │
│  │                    业务请求流（已有）                         │       │
│  │                                                             │       │
│  │  Client ──► TrajProxy Workers ──► Infer Services            │       │
│  │                          │                                  │       │
│  │                          ├──── /v1/chat/completions          │       │
│  │                          ├──── /health[?deep=1]              │       │
│  │                          └──── /metrics                      │       │
│  └──────────────────────────────────────────────────────────────┘      │
│                                                                         │
│  ┌──────────────────────────────────────────────────────────────┐      │
│  │                   指标流（Prometheus 拉取）                   │       │
│  │                                                             │       │
│  │  Worker-0 :12300 ─┐                                         │       │
│  │  Worker-1 :12301 ─┤                                         │       │
│  │  Worker-2 :12302 ─┼──► file_sd targets.json ──► Prometheus  │       │
│  │  ...              │         ▲                    │           │       │
│  │  Worker-N :1230X ─┘         │                    ▼           │       │
│  │                      部署脚本生成          Grafana (可视化)    │       │
│  │                                       AlertManager (告警)   │       │
│  └──────────────────────────────────────────────────────────────┘      │
│                                                                         │
│  ┌──────────────────────────────────────────────────────────────┐      │
│  │                 日志流（文件/标准输出）                       │       │
│  │                                                             │       │
│  │  Worker stdout ─┐                                           │       │
│  │  Worker stdout ─┼─► 本地日志文件 (RotatingFileHandler)       │       │
│  │  Worker stdout ─┘       │                                   │       │
│  │                         └──► 可选: 日志采集 (Fluentd/        │       │
│  │                              Vector/Filebeat)               │       │
│  │                                    │                        │       │
│  │                                    ▼                        │       │
│  │                              Loki / ELK                     │       │
│  │                              (查询 & 告警)                  │       │
│  └──────────────────────────────────────────────────────────────┘      │
│                                                                         │
│  ┌──────────────────────────────────────────────────────────────┐      │
│  │                    依赖流（业务直连）                          │       │
│  │                                                             │       │
│  │  TrajProxy Workers ──── PostgreSQL (已有)                   │       │
│  │  TrajProxy Workers ──── Infer Services (已有)               │       │
│  └──────────────────────────────────────────────────────────────┘      │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### 11.2 组件清单

| 组件 | 类型 | 职责 | 是否新增 |
|------|------|------|---------|
| **TrajProxy Workers** | 应用 | 处理业务请求；暴露 `/metrics`、`/health`、`/health?deep=1`；输出结构化日志 | 已有（需改造） |
| **WorkerManager** | 应用 | 管理 Workers 生命周期 | 已有 |
| **Prometheus** | 基础设施 | 拉取 Workers 的 `/metrics`；聚合多节点、多 Worker 指标；存储时间序列 | **需新增** |
| **Grafana** | 基础设施 | 查询 Prometheus，展示 Dashboard；告警规则可视化 | **需新增**（可选，但强烈推荐） |
| **AlertManager** | 基础设施 | 接收 Prometheus 告警规则触发，推送到钉钉/邮件/PagerDuty | **需新增**（可选） |
| **日志采集器**（Vector/Fluentd/Filebeat） | 基础设施 | 采集 Worker 日志文件/stdout，转发至日志聚合系统 | **需新增**（可选） |
| **Loki 或 ELK** | 基础设施 | 日志存储、查询、告警（Loki 更轻量，与 Grafana 生态整合好） | **需新增**（可选，但有 Loki 后查询效率大幅提升） |
| **Prometheus targets.json** | 配置文件 | 声明所有 Worker 的 `<host>:<port>` 列表，由部署脚本生成 | **需新增** |
| **PostgreSQL** | 基础设施 | 业务数据存储（请求轨迹、模型注册） | 已有 |
| **Infer Services** | 外部依赖 | LLM 推理服务 | 已有 |

### 11.3 三条核心数据流

#### A. 指标流（Metrics）

```
[Worker 进程内]                 [Prometheus]              [消费者]
     │                             │                        │
     │  prometheus-client 维护      │  每 15s scrape         │
     │  时间序列注册表              │                         │
     │                            │                         │
     ├─ /metrics (pull) ◄─────────┤                        │
     │                            │                         │
     │                            ├─────► 时序存储 (tsdb)   │
     │                            │         (本地磁盘 /     │
     │                            │          TSDB block)   │
     │                            │                         │
     │                            ├─────► Grafana (查询)    │
     │                            │                │        │
     │                            ├─────► AlertManager──────┤
     │                            │         (告警触发)  ┌───┴───┐
     │                            │                    │ 钉钉  │
     │                            │                    │ 邮件  │
     │                            │                    │ PagerDuty │
     │                            │                    └────────┘
```

**关键决策点**：
- **Pull 模型**：Prometheus 主动拉取（不是 Workers 推送），Workers 完全无感知，无阻塞
- **存储位置**：Prometheus 的 TSDB（本地磁盘默认；如需长期存储可对接 Thanos/Cortex，**当前不需要**）
- **数据保留**：默认 15 天，可配置延长；历史更久数据通过 Grafana 接入其他数据源（如 Loki），但不在 Prometheus 内堆积

#### B. 日志流（Logs）

```
[Worker 进程]            [日志文件/stdout]         [采集器]       [聚合系统]
     │                         │                     │                  │
     │  结构化 JSON            │                     │                  │
     ├─► stdout ──────────────► docker logs ────────►│                  │
     │  (容器化时)              │                     │                  │
     │                          │                     │                  │
     ├─► traj_proxy.log ────────► 日志文件           │                  │
     │  (RotatingFileHandler)    │                     │                  │
     │                          │                     │                  │
     │                          └──── file_sd ────────┤                  │
     │                          (或 docker log driver) ▼                  │
     │                                           Vector/Fluentd          │
     │                                                 │                  │
     │                                                 ▼                  │
     │                                            Loki / ELK            │
     │                                            (查询 + 告警)          │
     │                                                 │                  │
     │                                                 └──► Grafana       │
```

**两种部署形态**：

| 部署方式 | 日志流 | 推荐度 |
|---------|-------|--------|
| **Docker Compose** | Worker stdout → Docker daemon → 通过 log driver 写入宿主机文件 → Vector tail → Loki | ⭐⭐⭐ |
| **裸机部署** | Worker 直接写 `logs/traj_proxy.log`（JSON 格式）→ Vector tail → Loki | ⭐⭐⭐⭐（最直接） |
| **K8s（未来）** | Worker stdout → K8s 日志采集 DaemonSet → Loki/ELK | 未来再考虑 |

**当前场景（裸机/Docker 静态部署）**：推荐裸机直接写文件 + Vector 采集，最少组件、最稳定。

#### C. 业务请求流（已有，不变化）

```
Client ──► TrajProxy Workers ──► Infer Services
                  │
                  ├─► PostgreSQL (轨迹存储、模型注册)
                  └─► /health, /metrics (观测端点，仅读取，不写入业务数据)
```

### 11.4 多节点部署拓扑

```
┌──────────────────────────────────────────────────────────────────────┐
│                          监控节点（1 台）                             │
│                                                                     │
│  ┌──────────┐   ┌──────────┐   ┌──────────────┐   ┌──────────┐     │
│  │Prometheus│──►│  Loki    │──►│   Grafana    │──►│  Alert   │     │
│  │ (tsdb)   │   │ (日志)   │   │ (Dashboard)  │   │ Manager  │     │
│  └──────────┘   └──────────┘   └──────────────┘   └──────────┘     │
│       ▲              ▲                                │             │
│       │              │                                │             │
└───────┼──────────────┼────────────────────────────────┼─────────────┘
        │              │                                │
        │              │                                ▼
        │              │                          钉钉/邮件
        │              │
┌───────┼──────────────┼──────────────────────────────────────────────┐
│       │           生产节点 1 (Worker Node A)                          │
│       │                                                             │
│  ┌────┴──────────────────────────┐   ┌──────────────┐               │
│  │  WorkerManager                 │   │   Vector     │               │
│  │  ├── Worker-0 :12300 /metrics  │   │  (日志采集)  │               │
│  │  ├── Worker-1 :12301 /metrics  │   └──────┬───────┘               │
│  │  ├── ...                       │          │                       │
│  │  └── Worker-N :1230N /metrics  │          │ tail                  │
│  │                                │          │ logs/traj_proxy.log   │
│  │  stdout/stderr (容器化时)      │          │                       │
│  └────────────────────────────────┘          │                       │
└──────────────────────────────────────────────┼───────────────────────┘
                                               │
┌──────────────────────────────────────────────┼───────────────────────┐
│                                           生产节点 2 (Worker Node B)  │
│                                               │                       │
│  ┌────────────────────────────────┐  ┌────────┴───────┐              │
│  │  WorkerManager                  │  │   Vector        │              │
│  │  ├── Worker-0 :12300 /metrics   │  └─────────────────┘              │
│  │  ├── Worker-1 :12301 /metrics   │                                   │
│  │  └── ...                        │                                   │
│  └─────────────────────────────────┘                                   │
└──────────────────────────────────────────────────────────────────────┘
```

**Prometheus `targets.json` 示例**：

```json
[
  {
    "targets": [
      "node-a:12300", "node-a:12301", "node-a:12302", "node-a:12303",
      "node-a:12304", "node-a:12305", "node-a:12306", "node-a:12307",
      "node-a:12308", "node-a:12309", "node-a:12310"
    ],
    "labels": { "job": "trajproxy", "node": "node-a" }
  },
  {
    "targets": [
      "node-b:12300", "node-b:12301", "node-b:12302"
    ],
    "labels": { "job": "trajproxy", "node": "node-b" }
  }
]
```

通过 `node` label 可以在 Grafana 按节点维度聚合查询（对比节点间负载分布）。

### 11.5 部署步骤（一次性）

**1. TrajProxy 应用层（每个生产节点）**

| 步骤 | 操作 |
|------|------|
| a | 新增依赖：`pip install prometheus-client python-json-logger` |
| b | 部署代码（已包含可观测性改造） |
| c | 设置环境变量：`LOG_FORMAT=json`，`METRICS_ENABLED=true` |
| d | 生成 `prometheus/targets.json`（遍历节点 × Worker 端口，按部署约定） |
| e | 重启 Workers |

**2. 监控节点（一次性搭建）**

| 步骤 | 操作 | 资源占用（预估） |
|------|------|--------------|
| a | 安装 Prometheus，配置 `scrape_configs` + `file_sd_configs` | CPU < 1, RAM 2-4GB, Disk 50-200GB |
| b | 安装 Grafana，对接 Prometheus 数据源 | CPU < 0.5, RAM 500MB |
| c | （可选）安装 Loki，对接 Grafana | CPU < 1, RAM 1-2GB, Disk 50-500GB |
| d | （可选）安装 AlertManager，对接钉钉/邮件 | CPU < 0.2, RAM 200MB |
| e | 导入预置 Dashboard（Grafana JSON 模板） | — |
| f | 配置关键告警规则（见下方告警示例） | — |

**3. 每个生产节点**

| 步骤 | 操作 |
|------|------|
| a | 安装 Vector（或 Fluentd），配置 tail TrajProxy 日志文件 |
| b | 配置 Vector 输出到 Loki |

### 11.6 告警示例（Prometheus 告警规则）

```yaml
# prometheus/alert_rules.yml
groups:
  - name: trajproxy
    rules:
      # 1. 错误率过高
      - alert: HighErrorRate
        expr: >
          sum by (node) (rate(trajproxy_requests_total{status=~"5.."}[5m]))
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
        annotations:
          summary: "P95 延迟 > 60s"

      # 3. 推理服务故障
      - alert: InferErrors
        expr: >
          sum by (model, node) (rate(trajproxy_infer_errors_total[5m])) > 0.5
        for: 3m
        labels: { severity: critical }
        annotations:
          summary: "模型 {{ $labels.model }} 在 {{ $labels.node }} 推理服务持续报错"

      # 4. DB 连接池饱和
      - alert: DBPoolSaturation
        expr: >
          trajproxy_db_pool_usage{state="used"}
          / trajproxy_db_pool_usage{state="peak"} > 0.85
        for: 5m
        labels: { severity: warning }
        annotations:
          summary: "DB 连接池使用率 > 85%"

      # 5. Worker 健康检查失败
      - alert: WorkerDown
        expr: up{job="trajproxy"} == 0
        for: 1m
        labels: { severity: critical }
        annotations:
          summary: "Worker {{ $labels.instance }} 不可达"
```

### 11.7 资源总览

| 类别 | 数量 | 资源 |
|------|------|------|
| TrajProxy 应用代码改造 | 5 个新文件 + 5 处修改 | 每个 Worker 增加 < 5MB 内存 |
| 监控节点 | 1~2 台（Prometheus + Grafana + Loki 同机部署） | 4-8 CPU, 8-16GB RAM, 200-500GB Disk |
| 生产节点采集器 | 每个生产节点 1 个 Vector | CPU < 0.3, RAM 50-100MB/节点 |
| 新增 Python 依赖 | 2 个包（prometheus-client, python-json-logger） | 安装体积 < 5MB |

### 11.8 部署形态分级

根据团队和规模选择不同等级：

```
Level 1 — 最小可用（1 人团队，1-2 节点）
  ✅ Grafana + Prometheus（同机部署）
  ✅ Prometheus file_sd 自动 target 发现
  ❌ Loki（暂不部署，日志仍走文件 grep）
  ❌ AlertManager（暂不配置告警）

Level 2 — 标准生产（推荐起步）
  ✅ Level 1 全部内容
  ✅ Loki + 日志采集器（Vector）
  ✅ AlertManager + 关键告警规则

Level 3 — 完整观测（5+ 节点规模）
  ✅ Level 2 全部内容
  ✅ Prometheus 长期存储（Thanos 或 Grafana Mimir）
  ✅ 更多 Dashboard 定制
  ✅ 告警分级、值班系统接入
```

**建议起步为 Level 1**，1-2 天内可搭建；根据实际使用反馈，逐步升级到 Level 2、3。

---

## 十二、总结

本方案聚焦 **五个核心改进**，新增 **2 个依赖**，**5 个新文件**，**5 处修改**：

| 模块 | 解决的核心问题 |
|------|---------------|
| Request ID 统一 | 跨层日志无法关联 |
| 结构化 JSON 日志 | 日志无法被机器解析索引 |
| Prometheus Metrics | 无法量化负载、延迟、错误率 |
| 请求摘要日志 | 单请求生命周期散乱、难以快速定位 |
| 增强健康检查 | 依赖故障无预警 |

**预计实施工作量**：4-7 天（分阶段可独立上线）
