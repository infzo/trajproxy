"""
Prometheus 指标定义 + EventBus 订阅者

指标创建采用惰性初始化（在 register_all() 中一次性创建），
避免 Ray 多进程 / working_dir 机制导致的重复注册 ValueError。
"""

import logging
from typing import Any, Optional

from prometheus_client import Counter, Histogram, Gauge, ProcessCollector, REGISTRY

from traj_proxy.observability.event_bus import subscribe
from traj_proxy.observability.events import (
    EVENT_REQUEST_STARTED,
    EVENT_REQUEST_COMPLETED,
    EVENT_INFERENCE_COMPLETED,
    EVENT_MODEL_LIFECYCLE,
    EVENT_CONCURRENCY_REJECTED,
    EVENT_SEMAPHORE_ACQUIRED,
    EVENT_TRAJECTORY_STORE_ERROR,
    EVENT_STREAM_CLIENT_DISCONNECT,
)
from traj_proxy.observability.outcome import determine_outcome
from traj_proxy.observability.label_guards import (
    safe_model_label,
    safe_run_id_label,
)

logger = logging.getLogger(__name__)

# ── 指标实例（惰性初始化，由 register_all() 填充）──
# A. 请求核心 (5个)
REQUEST_TOTAL: Optional[Counter] = None
REQUEST_DURATION: Optional[Histogram] = None
ACTIVE_REQUESTS: Optional[Gauge] = None
CONCURRENCY_UTILIZATION: Optional[Gauge] = None
MAX_CONCURRENT: Optional[Gauge] = None

# A2. 并发控制 (2个)
SEMAPHORE_WAIT_DURATION: Optional[Histogram] = None
SEMAPHORE_REJECTED: Optional[Counter] = None

# B. 流式 (2个核心 + 1个可选)
TTFT_SECONDS: Optional[Histogram] = None
STREAM_COMPLETION: Optional[Counter] = None
STREAM_CLIENT_DISCONNECT: Optional[Counter] = None

# C. 阶段 (1个)
PHASE_DURATION: Optional[Histogram] = None

# D. Token (1个)
TOKENS_TOTAL: Optional[Counter] = None

# E. 下游依赖 (5个)
INFER_DURATION: Optional[Histogram] = None
INFER_ERRORS: Optional[Counter] = None
INFER_RETRIES: Optional[Counter] = None
DB_POOL_USAGE: Optional[Gauge] = None
TRAJECTORY_STORE_ERRORS: Optional[Counter] = None

# F. 可选（Phase 3+）
MODEL_LIFECYCLE: Optional[Counter] = None

# 幂等标志
_registered = False


# ── ProcessContext 字段守卫 ──
_REQUIRED_CONTEXT_FIELDS = frozenset(
    {"model", "is_stream", "processing_duration_ms", "request_id", "run_id"}
)
_OPTIONAL_CONTEXT_FIELDS = frozenset(
    {
        "transform_duration_ms",
        "encode_duration_ms",
        "cache_db_query_ms",
        "cache_prefix_match_ms",
        "inference_duration_ms",
        "decode_duration_ms",
        "prompt_tokens",
        "completion_tokens",
        "cache_hit_tokens",
        "ttft_ms",
        "stream_finished",
        "stream_chunk_count",
        "error",
    }
)


def validate_context_fields(context: Any) -> None:
    """校验 ProcessContext 字段完整性（启动时或首次请求时）"""
    missing = _REQUIRED_CONTEXT_FIELDS - {
        f for f in _REQUIRED_CONTEXT_FIELDS if hasattr(context, f)
    }
    if missing:
        logger.error(f"ProcessContext missing fields: {missing}")


def _on_request_started(model: str, is_stream: bool, max_concurrent: int) -> None:
    ACTIVE_REQUESTS.inc()  # type: ignore[union-attr]
    if max_concurrent > 0:
        current = ACTIVE_REQUESTS._value.get()  # type: ignore[union-attr]
        CONCURRENCY_UTILIZATION.set(current / max_concurrent)  # type: ignore[union-attr]
        MAX_CONCURRENT.labels(safe_model_label(model)).set(max_concurrent)  # type: ignore[union-attr]


def _on_request_completed(context: Any, exception: Optional[Exception]) -> None:
    ACTIVE_REQUESTS.dec()  # type: ignore[union-attr]
    model = safe_model_label(getattr(context, "model", "unknown") or "unknown")
    run_id = safe_run_id_label(getattr(context, "run_id", None) or "")
    stream = str(getattr(context, "is_stream", False)).lower()
    outcome = determine_outcome(context, exception)
    duration_s = (getattr(context, "processing_duration_ms", 0) or 0) / 1000

    REQUEST_TOTAL.labels("POST", model, stream, outcome, run_id).inc()  # type: ignore[union-attr]
    REQUEST_DURATION.labels(model, stream, run_id).observe(duration_s)  # type: ignore[union-attr]

    # 分阶段耗时
    for phase, attr in [
        ("transform", "transform_duration_ms"),
        ("encode", "encode_duration_ms"),
        ("cache_db_query", "cache_db_query_ms"),
        ("cache_prefix_match", "cache_prefix_match_ms"),
        ("inference", "inference_duration_ms"),
        ("decode", "decode_duration_ms"),
    ]:
        ms = getattr(context, attr, None)
        if ms is not None:
            PHASE_DURATION.labels(model, phase).observe(ms / 1000)  # type: ignore[union-attr]

    # Tokens
    prompt_tok = getattr(context, "prompt_tokens", None)
    completion_tok = getattr(context, "completion_tokens", None)
    cache_tok = getattr(context, "cache_hit_tokens", None)
    if prompt_tok:
        TOKENS_TOTAL.labels(model, run_id, "prompt").inc(prompt_tok)  # type: ignore[union-attr]
    if completion_tok:
        TOKENS_TOTAL.labels(model, run_id, "completion").inc(completion_tok)  # type: ignore[union-attr]
    if cache_tok:
        TOKENS_TOTAL.labels(model, run_id, "cached").inc(cache_tok)  # type: ignore[union-attr]

    # Stream
    if stream == "true":
        ttft = getattr(context, "ttft_ms", None)
        if ttft is not None:
            TTFT_SECONDS.labels(model, run_id).observe(ttft / 1000)  # type: ignore[union-attr]
        finished = getattr(context, "stream_finished", False)
        chunk_count = getattr(context, "stream_chunk_count", 0)
        if finished and chunk_count > 0:
            STREAM_COMPLETION.labels(model, run_id, "full").inc()  # type: ignore[union-attr]
        elif chunk_count > 0:
            STREAM_COMPLETION.labels(model, run_id, "partial").inc()  # type: ignore[union-attr]
        else:
            STREAM_COMPLETION.labels(model, run_id, "empty").inc()  # type: ignore[union-attr]


def _on_semaphore_acquired(wait_duration_ms: float, model: str) -> None:
    SEMAPHORE_WAIT_DURATION.labels(safe_model_label(model)).observe(  # type: ignore[union-attr]
        wait_duration_ms / 1000
    )


def _on_concurrency_rejected(
    model: str, run_id: str, wait_duration_ms: float
) -> None:
    safe_model = safe_model_label(model)
    safe_run_id = safe_run_id_label(run_id or "")
    SEMAPHORE_REJECTED.labels(safe_model).inc()  # type: ignore[union-attr]
    REQUEST_TOTAL.labels(  # type: ignore[union-attr]
        "POST", safe_model, "false", "error_rate_limit", safe_run_id
    ).inc()


def _on_trajectory_store_error(
    model: str, error_type: str, error_message: str
) -> None:
    TRAJECTORY_STORE_ERRORS.labels(safe_model_label(model), error_type).inc()  # type: ignore[union-attr]


def _on_stream_client_disconnect(
    model: str, chunk_count: int, duration_ms: float
) -> None:
    STREAM_CLIENT_DISCONNECT.labels(safe_model_label(model)).inc()  # type: ignore[union-attr]


def _on_inference_completed(
    model: str,
    duration_ms: float,
    retry_count: int,
    error: Optional[Exception],
    error_type: Optional[str],
) -> None:
    safe_model = safe_model_label(model)
    INFER_DURATION.labels(safe_model).observe(duration_ms / 1000)  # type: ignore[union-attr]
    if error:
        INFER_ERRORS.labels(safe_model, error_type or "unknown").inc()  # type: ignore[union-attr]
    if retry_count > 0:
        outcome_label = "success" if not error else "failed"
        INFER_RETRIES.labels(safe_model, outcome_label).inc(retry_count)  # type: ignore[union-attr]


def _on_model_lifecycle(
    action: str, model: str, run_id: str, model_type: str
) -> None:
    MODEL_LIFECYCLE.labels(action, model_type).inc()  # type: ignore[union-attr]


def _create_metrics() -> None:
    """创建所有 Prometheus 指标（惰性初始化，仅执行一次）"""
    global REQUEST_TOTAL, REQUEST_DURATION, ACTIVE_REQUESTS
    global CONCURRENCY_UTILIZATION, MAX_CONCURRENT
    global SEMAPHORE_WAIT_DURATION, SEMAPHORE_REJECTED
    global TTFT_SECONDS, STREAM_COMPLETION, STREAM_CLIENT_DISCONNECT
    global PHASE_DURATION, TOKENS_TOTAL
    global INFER_DURATION, INFER_ERRORS, INFER_RETRIES
    global DB_POOL_USAGE, TRAJECTORY_STORE_ERRORS
    global MODEL_LIFECYCLE

    # A. 请求核心
    REQUEST_TOTAL = Counter(
        "trajproxy_requests_total",
        "Total request count",
        ["method", "model", "stream", "outcome", "run_id"],
    )
    REQUEST_DURATION = Histogram(
        "trajproxy_request_duration_seconds",
        "End-to-end request latency",
        ["model", "stream", "run_id"],
        buckets=[0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60],
    )
    ACTIVE_REQUESTS = Gauge(
        "trajproxy_active_requests", "Currently active requests"
    )
    CONCURRENCY_UTILIZATION = Gauge(
        "trajproxy_concurrency_utilization", "Concurrency utilization (0-1)"
    )
    MAX_CONCURRENT = Gauge(
        "trajproxy_max_concurrent_requests",
        "Max concurrent requests per model",
        ["model"],
    )

    # A2. 并发控制
    SEMAPHORE_WAIT_DURATION = Histogram(
        "trajproxy_semaphore_wait_seconds",
        "Semaphore wait duration",
        ["model"],
        buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10],
    )
    SEMAPHORE_REJECTED = Counter(
        "trajproxy_semaphore_rejected_total",
        "Semaphore timeout rejection count",
        ["model"],
    )

    # B. 流式
    TTFT_SECONDS = Histogram(
        "trajproxy_ttft_seconds",
        "Time to first token",
        ["model", "run_id"],
        buckets=[0.05, 0.1, 0.25, 0.5, 1, 2, 5],
    )
    STREAM_COMPLETION = Counter(
        "trajproxy_stream_completion_total",
        "Stream completion count",
        ["model", "run_id", "result"],
    )
    STREAM_CLIENT_DISCONNECT = Counter(
        "trajproxy_stream_client_disconnect_total",
        "Stream client disconnect count",
        ["model"],
    )

    # C. 阶段
    PHASE_DURATION = Histogram(
        "trajproxy_phase_duration_seconds",
        "Pipeline phase duration",
        ["model", "phase"],
        buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10],
    )

    # D. Token
    TOKENS_TOTAL = Counter(
        "trajproxy_tokens_total", "Token usage", ["model", "run_id", "type"]
    )

    # E. 下游依赖
    INFER_DURATION = Histogram(
        "trajproxy_infer_duration_seconds",
        "Inference call duration",
        ["model"],
        buckets=[0.1, 0.5, 1, 2, 5, 10, 30, 60],
    )
    INFER_ERRORS = Counter(
        "trajproxy_infer_errors_total",
        "Inference error count",
        ["model", "error_type"],
    )
    INFER_RETRIES = Counter(
        "trajproxy_infer_retries_total",
        "Inference retry count",
        ["model", "outcome"],
    )
    DB_POOL_USAGE = Gauge(
        "trajproxy_db_pool_usage", "DB pool usage", ["state"]
    )
    TRAJECTORY_STORE_ERRORS = Counter(
        "trajproxy_trajectory_store_errors_total",
        "Trajectory store error count",
        ["model", "error_type"],
    )

    # F. 可选
    MODEL_LIFECYCLE = Counter(
        "trajproxy_model_lifecycle_total",
        "Model lifecycle events",
        ["action", "type"],
    )


def register_all() -> None:
    """注册所有 EventBus 订阅者（幂等，多次调用安全）

    Ray 多进程 / working_dir 机制下同模块可能重复 import，
    必须保证指标创建和事件订阅只执行一次。
    """
    global _registered
    if _registered:
        return
    _registered = True

    # 创建 Prometheus 指标
    _create_metrics()

    # 注册 ProcessCollector（零侵入进程级指标，容错重复注册）
    try:
        ProcessCollector()
    except ValueError:
        pass  # 已被其他组件（如 Ray）注册

    # 订阅所有事件
    subscribe(EVENT_REQUEST_STARTED, _on_request_started)
    subscribe(EVENT_REQUEST_COMPLETED, _on_request_completed)
    subscribe(EVENT_INFERENCE_COMPLETED, _on_inference_completed)
    subscribe(EVENT_MODEL_LIFECYCLE, _on_model_lifecycle)
    subscribe(EVENT_CONCURRENCY_REJECTED, _on_concurrency_rejected)
    subscribe(EVENT_SEMAPHORE_ACQUIRED, _on_semaphore_acquired)
    subscribe(EVENT_TRAJECTORY_STORE_ERROR, _on_trajectory_store_error)
    subscribe(EVENT_STREAM_CLIENT_DISCONNECT, _on_stream_client_disconnect)
