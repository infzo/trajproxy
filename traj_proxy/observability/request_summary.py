"""
请求摘要日志（EventBus 订阅者）

订阅 EVENT_REQUEST_COMPLETED，每次请求完成后输出结构化摘要。
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)
_worker_id: str = "unknown"


def set_worker_id(worker_id: str) -> None:
    """设置当前 Worker ID（在 Worker 初始化时调用）"""
    global _worker_id
    _worker_id = worker_id


def _on_request_completed(
    context: Any, exception: Optional[Exception]
) -> None:
    from traj_proxy.observability.outcome import determine_outcome
    from traj_proxy.observability.label_guards import safe_model_label

    outcome = determine_outcome(context, exception)
    model_raw = getattr(context, "model", "unknown") or "unknown"
    model_known = safe_model_label(model_raw) != "unknown"

    summary: dict[str, Any] = {
        "request_id": getattr(context, "request_id", ""),
        "worker_id": _worker_id,
        "model": model_raw,
        "model_known": model_known,
        "stream": getattr(context, "is_stream", False),
        "outcome": outcome,
        "duration_ms": round(
            getattr(context, "processing_duration_ms", 0) or 0, 1
        ),
    }
    optional_fields = {
        "run_id": getattr(context, "run_id", None),
        "session_id": getattr(context, "session_id", None),
        "prompt_tokens": getattr(context, "prompt_tokens", None),
        "completion_tokens": getattr(context, "completion_tokens", None),
        "cache_hit_tokens": getattr(context, "cache_hit_tokens", None),
        "ttft_ms": getattr(context, "ttft_ms", None),
        "inference_ms": getattr(context, "inference_duration_ms", None),
        "transform_ms": getattr(context, "transform_duration_ms", None),
        "encode_ms": getattr(context, "encode_duration_ms", None),
        "decode_ms": getattr(context, "decode_duration_ms", None),
    }
    summary.update({k: v for k, v in optional_fields.items() if v is not None})
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