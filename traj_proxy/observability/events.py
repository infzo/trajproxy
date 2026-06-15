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
# kwargs: model: str, error_type: str, error_message: str, run_id: str
EVENT_TRAJECTORY_STORE_ERROR = "trajectory.store_error"

# ── API 请求错误（轨迹/模型接口的分类异常上报）──
# kwargs: route: str, run_id: str, error_category: str
# error_category 枚举:
#   - database           : 数据库异常（业务层上报）
#   - timeout            : DB 查询超时（业务层上报）
#   - serialize_timeout  : JSON 序列化超时（业务层上报）
#   - rate_limit         : 并发限流 429（业务层上报）
#   - other              : 其他未知错误（业务层兜底）
#   - validation         : 参数校验失败 422（中间件兜底上报）
#   - client_error       : 其他 4xx 客户端错误（中间件兜底上报）
#   - server_error       : 其他 5xx 服务端错误（中间件兜底上报）
EVENT_API_ERROR = "api.error"

# ── 流式客户端断连 ──
# kwargs: model: str, chunk_count: int, duration_ms: float
EVENT_STREAM_CLIENT_DISCONNECT = "stream.client_disconnect"

# ── 轨迹查询完成 ──
# kwargs: route: str, run_id: str, record_count: int, response_size_bytes: int
EVENT_TRAJECTORY_QUERY_COMPLETED = "trajectory.query_completed"