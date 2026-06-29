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
    EVENT_API_ERROR,
    EVENT_STREAM_CLIENT_DISCONNECT,
    EVENT_TRAJECTORY_QUERY_COMPLETED,
)
from traj_proxy.observability.decorators import classify_infer_error
from traj_proxy.observability.outcome import determine_outcome
from traj_proxy.observability.label_guards import (
    safe_model_label,
    safe_run_id_label,
)

logger = logging.getLogger(__name__)

# ── LRU 淘汰清理：记录哪些指标包含 run_id 及其在 label_tuple 中的位置 ──
_METRICS_WITH_RUN_ID: list = []  # [(metric_object, run_id_index_in_label_tuple), ...]


def cleanup_evicted_run_id(run_id: str) -> None:
    """清理被淘汰 run_id 的所有 Prometheus 子指标

    prometheus_client 的 remove() 需要完整的 label tuple，
    因此必须遍历 _metrics 找到匹配的子指标。
    """
    for metric, run_id_idx in _METRICS_WITH_RUN_ID:
        if metric is None:
            continue
        try:
            # 找到所有在预期位置包含该 run_id 的 label tuple
            to_remove = [
                label_tuple for label_tuple in list(metric._metrics.keys())
                if label_tuple[run_id_idx] == run_id
            ]
            for label_tuple in to_remove:
                metric.remove(*label_tuple)
        except Exception as exc:
            logger.warning(f"清理 run_id={run_id} 的指标子项失败: {exc}")

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

# D. Token (2个)
TOKENS_TOTAL: Optional[Counter] = None
SEQUENCE_LENGTH_TOKENS: Optional[Histogram] = None

# E. 下游依赖 (5个)
INFER_DURATION: Optional[Histogram] = None
INFER_ERRORS: Optional[Counter] = None
INFER_RETRIES: Optional[Counter] = None
DB_POOL_USAGE: Optional[Gauge] = None
TRAJECTORY_STORE_ERRORS: Optional[Counter] = None
TRAJECTORY_STORE_DURATION: Optional[Histogram] = None

# F. 可选（Phase 3+）
MODEL_LIFECYCLE: Optional[Counter] = None

# G. HTTP API 层 (2个)
API_REQUESTS_TOTAL: Optional[Counter] = None
API_REQUEST_DURATION: Optional[Histogram] = None

# H. API 错误归因 (1个)
API_ERRORS: Optional[Counter] = None

# I. 请求级推理错误细分 (1个)
REQUEST_INFER_ERRORS: Optional[Counter] = None

# J. 轨迹查询指标 (2个)
TRAJECTORY_RECORD_COUNT: Optional[Histogram] = None
TRAJECTORY_RESPONSE_SIZE_BYTES: Optional[Histogram] = None

# K. TITO 缓存监控 (1个)
CACHE_LOOKUPS: Optional[Counter] = None

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
        "store_duration_ms",
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
        logger.error(f"ProcessContext 缺少字段: {missing}")


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

    pipeline_mode = getattr(context, "pipeline_mode", "direct") or "direct"
    base_url = getattr(context, "base_url", "unknown") or "unknown"
    REQUEST_TOTAL.labels("POST", model, stream, outcome, run_id, pipeline_mode, base_url).inc()  # type: ignore[union-attr]
    # 请求级推理错误细分打点（仅当 outcome 为推理服务错误或超时）
    if outcome in ("error_infer", "timeout") and exception is not None:
        err_type = classify_infer_error(exception)
        REQUEST_INFER_ERRORS.labels(model, err_type, run_id).inc()  # type: ignore[union-attr]
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
            PHASE_DURATION.labels(model, phase, run_id).observe(ms / 1000)  # type: ignore[union-attr]

    # 轨迹存储耗时
    store_ms = getattr(context, "store_duration_ms", None)
    if store_ms is not None:
        TRAJECTORY_STORE_DURATION.labels(model=model, run_id=run_id).observe(store_ms / 1000)  # type: ignore[union-attr]

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

    # TITO 缓存命中率打点（仅当实际执行了缓存查询时）
    if pipeline_mode == "tito" and getattr(context, "cache_db_query_ms", None) is not None:
        cache_result = "hit" if (getattr(context, "cache_hit_tokens", None) or 0) > 0 else "miss"
        CACHE_LOOKUPS.labels(model, run_id, cache_result).inc()  # type: ignore[union-attr]

    # 序列长度（prompt + completion tokens 总和）
    total_seq_len = (prompt_tok or 0) + (completion_tok or 0)
    if total_seq_len > 0:
        SEQUENCE_LENGTH_TOKENS.labels(model, run_id).observe(total_seq_len)  # type: ignore[union-attr]

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
        "POST", safe_model, "false", "error_rate_limit", safe_run_id, "direct", "unknown"
    ).inc()


def _on_trajectory_store_error(
    model: str, error_type: str, error_message: str, run_id: str = ""
) -> None:
    safe_run_id = safe_run_id_label(run_id or "")
    TRAJECTORY_STORE_ERRORS.labels(safe_model_label(model), error_type, safe_run_id).inc()  # type: ignore[union-attr]


def _on_api_error(route: str, run_id: str, error_category: str) -> None:
    safe_run_id = safe_run_id_label(run_id or "")
    API_ERRORS.labels(route, safe_run_id, error_category).inc()  # type: ignore[union-attr]


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


def _on_trajectory_query_completed(
    route: str, run_id: str, record_count: int, response_size_bytes: int
) -> None:
    safe_run_id = safe_run_id_label(run_id or "")
    TRAJECTORY_RECORD_COUNT.labels(route, safe_run_id).observe(record_count)  # type: ignore[union-attr]
    TRAJECTORY_RESPONSE_SIZE_BYTES.labels(route, safe_run_id).observe(response_size_bytes)  # type: ignore[union-attr]


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
    global PHASE_DURATION, TOKENS_TOTAL, SEQUENCE_LENGTH_TOKENS
    global INFER_DURATION, INFER_ERRORS, INFER_RETRIES
    global DB_POOL_USAGE, TRAJECTORY_STORE_ERRORS, TRAJECTORY_STORE_DURATION
    global MODEL_LIFECYCLE
    global API_REQUESTS_TOTAL, API_REQUEST_DURATION, API_ERRORS
    global REQUEST_INFER_ERRORS
    global TRAJECTORY_RECORD_COUNT, TRAJECTORY_RESPONSE_SIZE_BYTES
    global CACHE_LOOKUPS

    # A. 请求核心
    REQUEST_TOTAL = Counter(
        "trajproxy_requests_total",
        "Total request count",
        ["method", "model", "stream", "outcome", "run_id", "pipeline_mode", "base_url"],
    )
    REQUEST_DURATION = Histogram(
        "trajproxy_request_duration_seconds",
        "End-to-end request latency",
        ["model", "stream", "run_id"],
        buckets=[0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600, 1200],
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
        buckets=[0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120],
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
        ["model", "phase", "run_id"],
        buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600, 1200],
    )

    # D. Token
    TOKENS_TOTAL = Counter(
        "trajproxy_tokens_total", "Token usage", ["model", "run_id", "type"]
    )
    SEQUENCE_LENGTH_TOKENS = Histogram(
        "trajproxy_sequence_length_tokens",
        "Total sequence length (prompt + completion tokens) per request",
        ["model", "run_id"],
        buckets=[128, 256, 512, 1024, 2048, 4096, 8192, 16384, 32768, 65536, 131072],
    )

    # E. 下游依赖
    INFER_DURATION = Histogram(
        "trajproxy_infer_duration_seconds",
        "Inference call duration",
        ["model"],
        buckets=[0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600, 1200],
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
        ["model", "error_type", "run_id"],
    )
    TRAJECTORY_STORE_DURATION = Histogram(
        "trajproxy_trajectory_store_duration_seconds",
        "Trajectory store duration",
        labelnames=["model", "run_id"],
        buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5],
    )

    # F. 可选
    MODEL_LIFECYCLE = Counter(
        "trajproxy_model_lifecycle_total",
        "Model lifecycle events",
        ["action", "type"],
    )

    # G. HTTP API 层
    API_REQUESTS_TOTAL = Counter(
        "trajproxy_api_requests_total",
        "HTTP API request count",
        labelnames=["route", "method", "status_code", "run_id"],
    )
    API_REQUEST_DURATION = Histogram(
        "trajproxy_api_request_duration_seconds",
        "HTTP API request duration",
        labelnames=["route", "run_id"],
        buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600, 1200],
    )

    # H. API 错误归因
    API_ERRORS = Counter(
        "trajproxy_api_errors_total",
        "HTTP API categorized error count",
        labelnames=["route", "run_id", "error_category"],
    )

    # I. 请求级推理错误细分
    REQUEST_INFER_ERRORS = Counter(
        "trajproxy_request_infer_errors_total",
        "Request-level inference errors with run_id and error_type (for per-run drill-down)",
        ["model", "error_type", "run_id"],
    )

    # J. 轨迹查询
    TRAJECTORY_RECORD_COUNT = Histogram(
        "trajproxy_trajectory_record_count",
        "Number of records returned per trajectory query",
        labelnames=["route", "run_id"],
        buckets=[1, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000],
    )
    TRAJECTORY_RESPONSE_SIZE_BYTES = Histogram(
        "trajproxy_trajectory_response_size_bytes",
        "Serialized response body size in bytes per trajectory query",
        labelnames=["route", "run_id"],
        buckets=[1024, 10240, 102400, 1048576, 5242880, 10485760, 52428800, 104857600],
    )

    # K. TITO 缓存监控
    CACHE_LOOKUPS = Counter(
        "trajproxy_cache_lookups_total",
        "TITO prefix cache lookup count (hit/miss)",
        labelnames=["model", "run_id", "result"],
    )

    # 注册包含 run_id 的指标，用于 LRU 淘汰时清理对应的子指标
    # 格式：(指标变量, run_id 在 label_tuple 中的索引位置)
    _METRICS_WITH_RUN_ID.extend([
        (REQUEST_TOTAL, 4),           # ["method","model","stream","outcome","run_id","pipeline_mode","base_url"]
        (REQUEST_DURATION, 2),        # ["model","stream","run_id"]
        (TTFT_SECONDS, 1),            # ["model","run_id"]
        (STREAM_COMPLETION, 1),       # ["model","run_id","result"]
        (PHASE_DURATION, 2),          # ["model","phase","run_id"]
        (TOKENS_TOTAL, 1),            # ["model","run_id","type"]
        (SEQUENCE_LENGTH_TOKENS, 1),  # ["model","run_id"]
        (TRAJECTORY_STORE_ERRORS, 2), # ["model","error_type","run_id"]
        (TRAJECTORY_STORE_DURATION, 1),  # ["model","run_id"]
        (API_REQUESTS_TOTAL, 3),      # ["route","method","status_code","run_id"]
        (API_REQUEST_DURATION, 1),    # ["route","run_id"]
        (API_ERRORS, 1),              # ["route","run_id","error_category"]
        (REQUEST_INFER_ERRORS, 2),    # ["model","error_type","run_id"]
        (TRAJECTORY_RECORD_COUNT, 1), # ["route","run_id"]
        (TRAJECTORY_RESPONSE_SIZE_BYTES, 1),  # ["route","run_id"]
        (CACHE_LOOKUPS, 1),           # ["model","run_id","result"]
    ])


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

    # 注册 LRU 淘汰清理钩子，当 run_id 被淘汰时自动清理对应的子指标
    from traj_proxy.observability.label_guards import register_cleanup_hook
    register_cleanup_hook(cleanup_evicted_run_id)

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
    subscribe(EVENT_API_ERROR, _on_api_error)
    subscribe(EVENT_STREAM_CLIENT_DISCONNECT, _on_stream_client_disconnect)
    subscribe(EVENT_TRAJECTORY_QUERY_COMPLETED, _on_trajectory_query_completed)
