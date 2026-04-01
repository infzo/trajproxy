# TrajProxy 架构文档

## 概述

TrajProxy 是一个 LLM 请求代理服务，支持两种请求处理模式：

1. **Token-in-Token-out 模式**：完整的请求处理流程，包括 prompt 构建、token 编码/解码、前缀匹配缓存
2. **直接转发模式**：轻量级代理，直接转发 OpenAI 格式请求到推理服务

---

## 一、两种处理模式

### 1.1 Token-in-Token-out 模式

**适用场景：**
- 需要 token 级别的精确控制
- 需要前缀匹配缓存（减少重复计算）
- 需要 prompt_text 用于调试或分析
- 模型返回 token ids 而非文本

**必需参数：**
- `tokenizer_path`: Tokenizer 路径（本地路径或 HuggingFace 模型名称）

**处理流程：**
```
OpenAI Request
     │
     ▼
┌─────────────────┐
│  PromptBuilder  │  messages → prompt_text
│ (apply_chat_template)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  TokenBuilder   │  prompt_text → token_ids
│   (encode)      │  前缀匹配缓存
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   InferClient   │  /v1/completions
│   (token_ids)   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  TokenBuilder   │  token_ids → response_text
│   (decode)      │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  PromptBuilder  │  构建 OpenAI Response
│ (parse tool_calls, reasoning)
└────────┬────────┘
         │
         ▼
OpenAI Response
```

### 1.2 直接转发模式

**适用场景：**
- 推理服务已支持 `/v1/chat/completions` 接口
- 不需要 token 级别的控制
- 简单的请求代理和轨迹记录
- 快速集成，无需配置 tokenizer

**必需参数：**
- 无需 `tokenizer_path`

**处理流程：**
```
OpenAI Request
     │
     ▼
┌─────────────────┐
│   InferClient   │  直接转发
│ /v1/chat/completions
└────────┬────────┘
         │
         ▼
OpenAI Response (原格式返回)
```

---

## 二、关键组件

### 2.1 Processor（非流式处理）

**文件：** `traj_proxy/proxy_core/processor.py`

**职责：**
- 协调非流式请求的完整处理流程
- 根据模式选择处理路径

**核心方法：**
```python
async def process_request(
    self,
    messages: list,
    request_id: str,
    session_id: Optional[str] = None,
    **request_params
) -> ProcessContext:
    """处理非流式请求"""
    if not self.token_in_token_out:
        # 直接转发模式
        return await self._process_direct_forward(...)
    # Token-in-Token-out 模式
    # 完整处理流程...
```

### 2.2 StreamingProcessor（流式处理）

**文件：** `traj_proxy/proxy_core/streaming_processor.py`

**职责：**
- 协调流式请求的处理流程
- 管理流式状态

**核心方法：**
```python
async def process_stream(
    self,
    messages: list,
    request_id: str,
    session_id: Optional[str] = None,
    context_holder: Optional[dict] = None,
    **request_params
) -> AsyncIterator[Dict[str, Any]]:
    """流式处理请求"""
    if not self.token_in_token_out:
        # 直接转发模式
        async for chunk in self._process_stream_direct(...):
            yield chunk
        return
    # Token-in-Token-out 模式
    # 完整处理流程...
```

### 2.3 InferClient（推理服务客户端）

**文件：** `traj_proxy/proxy_core/infer_client.py`

**职责：**
- 与推理服务通信
- 支持两种 API 格式

**核心方法：**
```python
# Token-in-Token-out 模式使用
async def send_completion(prompt, model, **kwargs)
async def send_completion_stream(prompt, model, **kwargs)

# 直接转发模式使用
async def send_chat_completion(messages, model, **kwargs)
async def send_chat_completion_stream(messages, model, **kwargs)
```

### 2.4 ProcessorManager（处理器管理器）

**文件：** `traj_proxy/proxy_core/processor_manager.py`

**职责：**
- 管理多个 Processor 实例
- 动态注册/删除模型
- 从数据库同步模型配置

**注册模型示例：**
```python
# 直接转发模式（无需 tokenizer_path）
await processor_manager.register_dynamic_processor(
    model_name="my-model",
    url="http://infer-service:8000",
    api_key="xxx",
    token_in_token_out=False
)

# Token-in-Token-out 模式（需要 tokenizer_path）
await processor_manager.register_dynamic_processor(
    model_name="my-model",
    url="http://infer-service:8000",
    api_key="xxx",
    tokenizer_path="/path/to/tokenizer",
    token_in_token_out=True
)
```

---

## 三、数据流对比

### 3.1 请求转换

| 阶段 | Token-in-Token-out 模式 | 直接转发模式 |
|------|------------------------|--------------|
| 输入 | OpenAI messages | OpenAI messages |
| 转换 | messages → prompt_text → token_ids | 无转换 |
| 发送 | `/v1/completions` + token_ids | `/v1/chat/completions` + messages |

### 3.2 响应处理

| 阶段 | Token-in-Token-out 模式 | 直接转发模式 |
|------|------------------------|--------------|
| 接收 | token_ids 或 text | OpenAI Response |
| 转换 | token_ids → text → OpenAI Response | 无转换 |
| 解析 | tool_calls, reasoning | 无解析 |

### 3.3 数据库存储

| 字段 | Token-in-Token-out 模式 | 直接转发模式 |
|------|------------------------|--------------|
| tokenizer_path | 必填 | 空 |
| prompt_text | 填充 | 空 |
| token_ids | 填充 | 空 |
| response | OpenAI Response | OpenAI Response |
| messages | 原始消息 | 原始消息 |

---

## 四、API 接口

### 4.1 注册模型

**POST /model/register**

```json
// 直接转发模式
{
    "model_name": "my-model",
    "url": "http://infer-service:8000",
    "api_key": "xxx",
    "token_in_token_out": false
}

// Token-in-Token-out 模式
{
    "model_name": "my-model",
    "url": "http://infer-service:8000",
    "api_key": "xxx",
    "tokenizer_path": "/path/to/tokenizer",
    "token_in_token_out": true,
    "tool_parser": "deepseek_v3",
    "reasoning_parser": "deepseek_r1"
}
```

### 4.2 发送请求

**POST /v1/chat/completions**

```json
{
    "model": "my-model",
    "messages": [
        {"role": "user", "content": "Hello"}
    ],
    "stream": false
}
```

**Header:** `x-session-id: run_id,sample_id,task_id`

---

## 五、配置决策指南

### 5.1 选择处理模式

```
推理服务是否支持 /v1/chat/completions？
├── 是 → 是否需要前缀匹配缓存？
│         ├── 是 → 使用 Token-in-Token-out 模式
│         └── 否 → 使用直接转发模式
└── 否 → 使用 Token-in-Token-out 模式
         （需要推理服务支持 /v1/completions）
```

### 5.2 参数配置表

| 模式 | tokenizer_path | tool_parser | reasoning_parser |
|------|----------------|-------------|------------------|
| 直接转发 | 不需要 | 不需要 | 不需要 |
| Token-in-Token-out | 必需 | 可选 | 可选 |

---

## 六、关键文件路径

| 组件 | 路径 |
|------|------|
| 路由层 | `traj_proxy/proxy_core/routes.py` |
| 非流式处理器 | `traj_proxy/proxy_core/processor.py` |
| 流式处理器 | `traj_proxy/proxy_core/streaming_processor.py` |
| 推理客户端 | `traj_proxy/proxy_core/infer_client.py` |
| 处理器管理器 | `traj_proxy/proxy_core/processor_manager.py` |
| Prompt 构建器 | `traj_proxy/proxy_core/prompt_builder.py` |
| Token 构建器 | `traj_proxy/proxy_core/token_builder.py` |
| 上下文数据 | `traj_proxy/proxy_core/context.py` |
| 数据模型 | `traj_proxy/store/models.py` |
| 模型仓库 | `traj_proxy/store/model_repository.py` |
| 请求仓库 | `traj_proxy/store/request_repository.py` |
| 通知监听器 | `traj_proxy/store/notification_listener.py` |

---

## 七、Worker 架构

### 7.1 架构概览

```
┌───────────────────────────────────────────────────────────────────────┐
│                           WorkerManager                                │
│  (主进程，管理 Ray 集群)                                               │
│                                                                        │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐        │
│  │   ProxyWorker   │  │   ProxyWorker   │  │   ProxyWorker   │  ...   │
│  │     Actor 0     │  │     Actor 1     │  │     Actor 2     │        │
│  │   Port: 12300   │  │   Port: 12301   │  │   Port: 12302   │        │
│  │                 │  │                 │  │                 │        │
│  │ ┌─────────────┐ │  │ ┌─────────────┐ │  │ ┌─────────────┐ │        │
│  │ │ FastAPI App │ │  │ │ FastAPI App │ │  │ │ FastAPI App │ │        │
│  │ │   Uvicorn   │ │  │ │   Uvicorn   │ │  │ │   Uvicorn   │ │        │
│  │ └─────────────┘ │  │ └─────────────┘ │  │ └─────────────┘ │        │
│  │                 │  │                 │  │                 │        │
│  │ ┌─────────────┐ │  │ ┌─────────────┐ │  │ ┌─────────────┐ │        │
│  │ │Processor    │ │  │ │Processor    │ │  │ │Processor    │ │        │
│  │ │Manager      │ │  │ │Manager      │ │  │ │Manager      │ │        │
│  │ └─────────────┘ │  │ └─────────────┘ │  │ └─────────────┘ │        │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘        │
└───────────┼────────────────────┼────────────────────┼─────────────────┘
            │                    │                    │
            └────────────────────┼────────────────────┘
                                 │
                    ┌────────────┴────────────┐
                    ▼                         ▼
            ┌───────────────┐         ┌───────────────┐
            │  PostgreSQL   │         │   Infer 服务   │
            │   (数据库)    │         │   (LLM 推理)   │
            └───────────────┘         └───────────────┘
```

### 7.2 WorkerManager

**文件**: `traj_proxy/workers/manager.py`

**职责**:
- 初始化 Ray 集群
- 创建并管理多个 ProxyWorker Actor
- 协调 Worker 的启动和关闭

**核心流程**:

```python
# 1. 初始化 Ray
ray.init(
    num_cpus=4,
    runtime_env={
        "working_dir": "/app",
        "env_vars": {"PYTHONPATH": "/app"}
    }
)

# 2. 创建 Worker Actors
for i in range(worker_count):
    worker = RemoteWorker.remote(ProxyWorker, i, base_port + i, db_url)
    await worker.initialize.remote()

# 3. 每个 Worker 独立运行 Uvicorn 服务
```

### 7.3 ProxyWorker

**文件**: `traj_proxy/workers/worker.py`

**职责**:
- 独立的 FastAPI 应用实例
- 处理 HTTP 请求
- 管理 ProcessorManager 和 TranscriptProvider

**初始化流程**:

```python
class ProxyWorker:
    def __init__(self, worker_id, port, db_url):
        # 创建 FastAPI 应用
        self.app = FastAPI(title=f"ProxyWorker-{worker_id}")

        # 设置中间件和路由
        self._setup_middleware()
        self._setup_routes()

    async def initialize(self):
        # 初始化数据库连接池
        self.db_manager = DatabaseManager(db_url, pool_config)

        # 创建 ProcessorManager
        self.processor_manager = ProcessorManager(self.db_manager)

        # 启动模型同步
        await self.processor_manager.start_sync()
```

### 7.4 配置示例

```yaml
proxy_workers:
  count: 2           # Worker 数量
  base_port: 12300   # 起始端口（12300, 12301）

ray:
  num_cpus: 4        # Ray 分配的 CPU 核心数
  working_dir: "/app"
  pythonpath: "/app"
```

---

## 八、模型同步机制

### 8.1 同步架构

TrajProxy 使用 **LISTEN/NOTIFY** 实现跨 Worker 的模型配置实时同步。

```
┌─────────────────┐                      ┌─────────────────┐
│   Worker A      │                      │   PostgreSQL    │
│                 │                      │                 │
│  注册模型请求    │─── INSERT ──────────►│  model_registry │
│                 │                      │                 │
│                 │─── NOTIFY ──────────►│  (广播通知)      │
└─────────────────┘                      └────────┬────────┘
                                                  │
                         ┌────────────────────────┼────────────────────────┐
                         │                        │                        │
                    LISTEN                   LISTEN                   LISTEN
                         │                        │                        │
                         ▼                        ▼                        ▼
                 ┌───────────────┐        ┌───────────────┐        ┌───────────────┐
                 │   Worker A    │        │   Worker B    │        │   Worker C    │
                 │NotificationL- │        │NotificationL- │        │NotificationL- │
                 │  istener      │        │  istener      │        │  istener      │
                 └───────┬───────┘        └───────┬───────┘        └───────┬───────┘
                         │                        │                        │
                         ▼                        ▼                        ▼
                 ┌───────────────┐        ┌───────────────┐        ┌───────────────┐
                 │Processor      │        │Processor      │        │Processor      │
                 │Manager        │        │Manager        │        │Manager        │
                 │(增量更新)      │        │(增量更新)      │        │(增量更新)      │
                 └───────────────┘        └───────────────┘        └───────────────┘
```

### 8.2 NotificationListener

**文件**: `traj_proxy/store/notification_listener.py`

**特点**:
- 维护一个专用的数据库连接（LISTEN 需要独占连接）
- 自动重连机制（指数退避，最大延迟 60 秒）
- 连接失败时无限重试

**核心逻辑**:

```python
class NotificationListener:
    async def start(self):
        # 创建独立连接
        self._conn = await psycopg.AsyncConnection.connect(db_url, autocommit=True)

        # 监听通道
        await self._conn.execute("LISTEN model_registry_changes")

        # 循环接收通知
        async for notify in self._conn.notifies():
            payload = json.loads(notify.payload)
            await self._on_notification(payload)
```

### 8.3 Payload 格式

通知通道: `model_registry_changes`

```json
{
    "action": "register",      // 或 "unregister"
    "run_id": "",
    "model_name": "gpt-4",
    "timestamp": 1712345678.9
}
```

### 8.4 兜底同步

为防止 NOTIFY 丢失，定期执行全量同步：

```python
# 配置项
sync_fallback_interval: 300  # 秒，默认 5 分钟

# 流程
while running:
    await asyncio.sleep(sync_fallback_interval)
    models = await model_repository.get_all()
    await processor_manager.sync_all(models)
```

### 8.5 同步配置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `sync_fallback_interval` | 300 | 兜底全量同步间隔（秒） |
| `sync_max_retries` | 3 | 同步失败最大重试次数 |
| `sync_retry_delay` | 5 | 重试初始延迟（秒） |
| `listen_reconnect_delay` | 5 | LISTEN 重连初始延迟（秒） |
| `listen_max_reconnect_delay` | 60 | LISTEN 重连最大延迟（秒） |
