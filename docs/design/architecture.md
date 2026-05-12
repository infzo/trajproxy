# TrajProxy 架构文档

> **导航**: [文档中心](../README.md) | [API 参考](../develop/api_reference.md) | [开发指南](../develop/development.md)

## 概述

TrajProxy 是一个 LLM 请求代理服务，支持两种请求处理模式：

1. **Token-in-Token-out 模式**：完整的请求处理流程，包括 prompt 构建、token 编码/解码、前缀匹配缓存
2. **直接转发模式**：轻量级代理，直接转发 OpenAI 格式请求到推理服务

---

## 一、核心架构概览

### 1.1 Pipeline 架构

TrajProxy 使用 **Pipeline 模式** 处理请求，Processor 作为统一入口，根据配置选择不同的 Pipeline：

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Processor                                       │
│                    (统一请求处理器，选择 Pipeline)                             │
│                                                                              │
│   ┌──────────────────────────┐      ┌──────────────────────────┐            │
│   │      DirectPipeline      │      │      TokenPipeline       │            │
│   │    (直接转发管道)         │      │    (Token 模式管道)      │            │
│   │                          │      │                          │            │
│   │  raw_request → 推理服务  │      │  Message → Text → Token  │            │
│   │  → raw_response          │      │  → 推理 → Token → Text   │            │
│   │                          │      │  → Response              │            │
│   └──────────────────────────┘      └──────────────────────────┘            │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 核心组件

| 组件 | 职责 | 文件路径 |
|------|------|----------|
| **Processor** | 统一请求处理器，选择 Pipeline | `proxy_core/processor.py` |
| **ProcessorManager** | 多模型处理器管理器 | `proxy_core/processor_manager.py` |
| **BasePipeline** | 处理管道抽象基类 | `proxy_core/pipeline/base.py` |
| **DirectPipeline** | 直接转发管道 | `proxy_core/pipeline/direct_pipeline.py` |
| **TokenPipeline** | Token 模式管道 | `proxy_core/pipeline/token_pipeline.py` |
| **InferClient** | 推理服务客户端 | `proxy_core/infer_client.py` |
| **ParserManager** | 工具调用/推理内容解析器 | `proxy_core/parsers/parser_manager.py` |
| **TrajectoryProvider** | 轨迹查询业务逻辑 | `proxy_core/provider.py` |
| **ModelSynchronizer** | 跨 Worker 模型同步 | `store/model_synchronizer.py` |

---

## 二、两种处理模式详解

### 2.1 Token-in-Token-out 模式

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
┌─────────────────────┐
│  MessageConverter   │  messages → prompt_text
│ (apply_chat_template)
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│   TokenConverter    │  prompt_text → token_ids
│   (encode + 缓存)   │  前缀匹配缓存优化
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│    InferClient      │  /v1/completions
│   (token_ids)       │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│   TokenConverter    │  token_ids → response_text
│     (decode)        │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ ResponseBuilder     │  构建 OpenAI Response
│ + Parser 解析       │  解析 tool_calls, reasoning
└──────────┬──────────┘
           │
           ▼
OpenAI Response
```

### 2.2 直接转发模式

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
┌─────────────────────┐
│    InferClient      │  直接转发
│ /v1/chat/completions│
└──────────┬──────────┘
           │
           ▼
OpenAI Response (原格式返回)
```

---

## 三、Pipeline 架构详解

### 3.1 BasePipeline（抽象基类）

**文件：** `traj_proxy/proxy_core/pipeline/base.py`

**职责：**
- 定义所有 Pipeline 的通用接口
- 提供共享工具方法

**核心接口：**

```python
class BasePipeline(ABC):
    @abstractmethod
    async def process(self, messages: list, context: ProcessContext) -> ProcessContext:
        """处理非流式请求"""
        pass

    @abstractmethod
    async def process_stream(self, messages: list, context: ProcessContext) -> AsyncIterator[Dict[str, Any]]:
        """处理流式请求"""
        pass
```

**共享方法：**
- `_create_context()`: 创建处理上下文
- `_store_trajectory()`: 存储轨迹到数据库
- `_update_timing()`: 更新时间统计
- `_handle_error()`: 处理错误

### 3.2 DirectPipeline（直接转发管道）

**文件：** `traj_proxy/proxy_core/pipeline/direct_pipeline.py`

**职责：**
- 直接转发请求到推理服务
- 处理流式响应的字段累积
- 构建 OpenAI 格式响应

**核心特性：**
- 完全透传请求/响应
- 支持流式 tool_calls 合并
- 支持 vLLM 扩展字段（reasoning, stop_reason, token_ids）

**流式字段累积：**
- `stream_buffer_text`: 累积响应文本
- `stream_reasoning`: 累积推理内容
- `stream_tool_calls`: 累积工具调用
- `stream_function_call`: 旧版函数调用兼容

### 3.3 TokenPipeline（Token 模式管道）

**文件：** `traj_proxy/proxy_core/pipeline/token_pipeline.py`

**职责：**
- 执行完整的 Message → Text → Token → Infer → Token → Text → Response 流程
- 支持前缀匹配缓存优化
- 支持工具调用和推理内容解析

**核心组件：**

| 组件 | 职责 |
|------|------|
| MessageConverter | 将 OpenAI messages 转换为 prompt_text |
| TokenConverter | Text ↔ TokenIds 转换，支持缓存策略 |
| PrefixMatchCache | 前缀匹配缓存策略 |
| OpenAIResponseBuilder | 构建非流式 OpenAI 响应 |
| StreamChunkBuilder | 构建流式响应块 |
| Parser | 解析工具调用和推理内容 |

**处理阶段：**

```python
# 非流式处理
async def process(messages, context):
    # 阶段1: Message → PromptText
    context = await self._transform_messages(messages, context)
    
    # 阶段2: PromptText → TokenIds (带缓存)
    context = await self._encode_text(context)
    
    # 阶段3: 推理
    context = await self._inference(context)
    
    # 阶段4: TokenIds → ResponseText
    context = await self._decode_response(context)
    
    # 阶段5: 构建响应
    context = self._build_response(context)
    
    # 存储轨迹
    await self._store_trajectory(context, self.tokenizer_path)
```

---

## 四、核心组件详解

### 4.1 Processor（统一请求处理器）

**文件：** `traj_proxy/proxy_core/processor.py`

**职责：**
- 根据 `token_in_token_out` 配置选择 Pipeline
- 提供统一的非流式和流式处理接口
- 管理 Pipeline 依赖组件

**初始化逻辑：**

```python
class Processor:
    def __init__(self, model, tokenizer_path, config, ...):
        self.token_in_token_out = config.get("token_in_token_out", False)
        
        if self.token_in_token_out:
            self._pipeline = self._create_token_pipeline()
        else:
            self._pipeline = self._create_direct_pipeline()
```

### 4.2 InferClient（推理服务客户端）

**文件：** `traj_proxy/proxy_core/infer_client.py`

**职责：**
- 与推理服务通信
- 支持两种 API 格式

**核心方法：**

```python
# Token-in-Token-out 模式
async def send_completion(prompt: Union[str, List[int]], model, **kwargs)
async def send_completion_stream(prompt: Union[str, List[int]], model, **kwargs)

# 直接转发模式
async def send_chat_completion(messages: list, model, **kwargs)
async def send_chat_completion_stream(messages: list, model, **kwargs)
```

**特性：**
- 使用 requests + ThreadPoolExecutor 实现异步兼容
- 内置重试机制（502/503/504 自动重试）
- 连接池管理

### 4.3 ProcessorManager（处理器管理器）

**文件：** `traj_proxy/proxy_core/processor_manager.py`

**职责：**
- 管理预置模型和动态模型
- 创建和缓存 Processor 实例
- 提供 Processor 查询接口

**存储结构：**

```python
# 预置模型（不存数据库）
config_processors: Dict[Tuple[run_id, model_name], Processor]

# 动态模型（从数据库同步）
dynamic_processors: Dict[Tuple[run_id, model_name], Processor]
```

**核心方法：**

```python
# 注册动态模型（持久化到数据库）
async def register_dynamic_processor(model_name, url, api_key, ...)

# 注册预置模型（仅内存）
def register_static_processor(model_name, url, api_key, ...)

# 获取 Processor（精确匹配）
def get_processor(run_id, model_name) -> Optional[Processor]
```

---

## 五、Converter 组件

### 5.1 MessageConverter（消息转换器）

**文件：** `traj_proxy/proxy_core/converters/message_converter.py`

**职责：**
- 将 OpenAI Messages 转换为模型特定的 PromptText
- 使用 `tokenizer.apply_chat_template()` 方法

**支持参数：**
- `tools`: 工具定义列表
- `documents`: RAG 文档
- `tool_choice`: 工具选择策略

### 5.2 TokenConverter（Token 转换器）

**文件：** `traj_proxy/proxy_core/converters/token_converter.py`

**职责：**
- Text ↔ TokenIds 转换
- 支持缓存策略优化编码

**核心方法：**

```python
async def encode(text, context) -> List[int]  # 文本 → Token IDs
async def decode(token_ids, context) -> str   # Token IDs → 文本
def decode_streaming(token_ids, context) -> str  # 流式增量解码
```

---

## 六、Cache 组件

### 6.1 PrefixMatchCache（前缀匹配缓存）

**文件：** `traj_proxy/proxy_core/cache/prefix_cache.py`

**职责：**
- 通过匹配历史对话的前缀来优化 Token 编码

**优化策略：**

```
1. 根据 session_id 查询数据库获取该会话的所有历史请求
2. 用当前完整对话文本（请求+响应）与历史完整对话文本进行前缀匹配
3. 匹配部分使用缓存的完整对话 token_ids
4. 未匹配部分使用 tokenizer 编码
5. 拼接得到完整的 token_ids
```

---

## 七、Builder 组件

### 7.1 OpenAIResponseBuilder（响应构建器）

**文件：** `traj_proxy/proxy_core/builders/openai_builder.py`

**职责：**
- 构建非流式 OpenAI 格式响应
- 解析工具调用和推理内容

### 7.2 StreamChunkBuilder（流式块构建器）

**文件：** `traj_proxy/proxy_core/builders/stream_builder.py`

**职责：**
- 构建流式响应块
- 处理增量内容、工具调用、推理内容

---

## 八、ProcessContext 数据结构

**文件：** `traj_proxy/proxy_core/context.py`

ProcessContext 贯穿整个请求处理流程，包含输入数据、中间处理数据和输出结果。

> **详细设计**：参见 [ID 设计规范](identifier_design.md)

```python
@dataclass
class ProcessContext:
    # ========== 基础标识 ==========
    request_id: str
    model: str
    session_id: Optional[str] = None  # 原始会话ID，不再自动补充前缀
    run_id: Optional[str] = None      # 运行ID，独立存储
    unique_id: Optional[str] = None   # 格式: {session_id},{request_id}

    # ========== 阶段1: OpenAI Chat 格式 ==========
    messages: List[Dict[str, Any]] = field(default_factory=list)
    request_params: Dict[str, Any] = field(default_factory=dict)
    raw_request: Optional[Dict[str, Any]] = None   # 完整 OpenAI 请求
    raw_response: Optional[Dict[str, Any]] = None  # 完整 OpenAI 响应

    # ========== 阶段2: 文本推理格式（Token模式）==========
    text_request: Optional[Dict[str, Any]] = None
    text_response: Optional[Dict[str, Any]] = None

    # ========== 阶段3: Token 推理格式（Token模式）==========
    prompt_text: Optional[str] = None
    token_ids: Optional[List[int]] = None
    cached_token_ids: Optional[List[int]] = None
    uncached_text: Optional[str] = None
    uncached_token_ids: Optional[List[int]] = None
    token_request: Optional[Dict[str, Any]] = None
    token_response: Optional[Dict[str, Any]] = None

    # ========== 输出数据 ==========
    response_text: Optional[str] = None
    response_ids: Optional[List[int]] = None

    # ========== 完整对话（用于前缀匹配）==========
    full_conversation_text: Optional[str] = None
    full_conversation_token_ids: Optional[List[int]] = None

    # ========== 元数据 ==========
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    processing_duration_ms: Optional[float] = None

    # ========== 统计信息 ==========
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    cache_hit_tokens: Optional[int] = None

    # ========== 错误信息 ==========
    error: Optional[str] = None
    error_traceback: Optional[str] = None

    # ========== 流式处理状态 ==========
    is_stream: bool = False
    stream_buffer_text: str = ""
    stream_buffer_ids: List[int] = field(default_factory=list)
    stream_chunk_count: int = 0
    stream_finished: bool = False

    # 流式累积字段
    stream_role: Optional[str] = None          # delta.role
    stream_reasoning: str = ""                 # 累积推理内容（vLLM 扩展）
    stream_tool_calls: Optional[List[Dict]] = None    # 累积工具调用
    stream_function_call: Optional[Dict] = None       # 旧版函数调用
    stream_finish_reason: Optional[str] = None        # 结束原因
    stream_logprobs: Optional[Dict] = None            # logprobs
    stream_stop_reason: Optional[Any] = None          # vLLM 扩展
    stream_token_ids: Optional[List[int]] = None      # vLLM 扩展
```

---

## 九、数据流对比

### 9.1 请求转换

| 阶段 | Token-in-Token-out 模式 | 直接转发模式 |
|------|------------------------|--------------|
| 输入 | OpenAI messages | OpenAI messages |
| 转换 | messages → prompt_text → token_ids | 无转换 |
| 发送 | `/v1/completions` + token_ids | `/v1/chat/completions` + messages |

### 9.2 响应处理

| 阶段 | Token-in-Token-out 模式 | 直接转发模式 |
|------|------------------------|--------------|
| 接收 | token_ids 或 text | OpenAI Response |
| 转换 | token_ids → text → OpenAI Response | 无转换 |
| 解析 | tool_calls, reasoning | 无解析 |

### 9.3 数据库存储

请求轨迹采用元数据+详情分离架构：

- **request_metadata**：长期保留统计信息（run_id, session_id, model, tokens, duration 等）
- **request_details_active**：近期详情大字段（messages, raw_request/response 等），按月分区
- **外部存储**：过期详情归档为 JSONL+GZIP 文件

> **注意**：`session_id` 存储原始值，不再自动补充 run_id 前缀；`run_id` 独立存储。

| 字段 | Token-in-Token-out 模式 | 直接转发模式 | 存储位置 |
|------|------------------------|--------------|----------|
| run_id | 填充 | 填充 | metadata |
| session_id | 原始值 | 原始值 | metadata |
| tokenizer_path | 必填 | 空 | details |
| prompt_text | 填充 | 空 | details |
| token_ids | 填充 | 空 | details |
| response | OpenAI Response | OpenAI Response | details |
| messages | 原始消息 | 原始消息 | details |
| prompt_tokens | 填充 | 填充 | metadata |
| cache_hit_tokens | 填充 | 填充 | metadata |

---

## 十、API 接口

### 10.1 注册模型

**POST /models/register**

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

### 10.2 发送请求

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

**Header:** `x-session-id: your-session-id`

---

## 十一、配置决策指南

### 11.1 选择处理模式

```
推理服务是否支持 /v1/chat/completions？
├── 是 → 是否需要前缀匹配缓存？
│         ├── 是 → 使用 Token-in-Token-out 模式
│         └── 否 → 使用直接转发模式
└── 否 → 使用 Token-in-Token-out 模式
         （需要推理服务支持 /v1/completions）
```

### 11.2 参数配置表

| 模式 | tokenizer_path | tool_parser | reasoning_parser |
|------|----------------|-------------|------------------|
| 直接转发 | 不需要 | 不需要 | 不需要 |
| Token-in-Token-out | 必需 | 可选 | 可选 |

---

## 十二、关键文件路径

| 组件 | 路径 |
|------|------|
| 统一处理器 | `traj_proxy/proxy_core/processor.py` |
| 处理器管理器 | `traj_proxy/proxy_core/processor_manager.py` |
| Pipeline 基类 | `traj_proxy/proxy_core/pipeline/base.py` |
| 直接转发管道 | `traj_proxy/proxy_core/pipeline/direct_pipeline.py` |
| Token 模式管道 | `traj_proxy/proxy_core/pipeline/token_pipeline.py` |
| 推理客户端 | `traj_proxy/proxy_core/infer_client.py` |
| 推理响应解析 | `traj_proxy/proxy_core/infer_response_parser.py` |
| 消息转换器 | `traj_proxy/proxy_core/converters/message_converter.py` |
| Token 转换器 | `traj_proxy/proxy_core/converters/token_converter.py` |
| 前缀匹配缓存 | `traj_proxy/proxy_core/cache/prefix_cache.py` |
| 响应构建器 | `traj_proxy/proxy_core/builders/openai_builder.py` |
| 流式块构建器 | `traj_proxy/proxy_core/builders/stream_builder.py` |
| 上下文数据 | `traj_proxy/proxy_core/context.py` |
| Parser 管理器 | `traj_proxy/proxy_core/parsers/parser_manager.py` |
| 数据模型 | `traj_proxy/store/models.py` |
| 模型仓库 | `traj_proxy/store/model_repository.py` |
| 请求仓库 | `traj_proxy/store/request_repository.py` |
| 模型同步器 | `traj_proxy/store/model_synchronizer.py` |
| 通知监听器 | `traj_proxy/store/notification_listener.py` |
| 轨迹查询 | `traj_proxy/proxy_core/provider.py` |
| 路由层 | `traj_proxy/serve/routes.py` |
| 数据模型（API） | `traj_proxy/serve/schemas.py` |
| 依赖注入 | `traj_proxy/serve/dependencies.py` |
| 错误处理 | `traj_proxy/serve/error_handler.py` |
| Worker | `traj_proxy/workers/worker.py` |
| Worker 管理 | `traj_proxy/workers/manager.py` |
| 路由注册 | `traj_proxy/workers/route_registrar.py` |
| 归档器 | `traj_proxy/archive/archiver.py` |
| 归档调度 | `traj_proxy/archive/scheduler.py` |

---

## 十三、Worker 架构

### 13.1 架构概览

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

### 13.2 WorkerManager

**文件**: `traj_proxy/workers/manager.py`

**职责**:
- 初始化 Ray 集群
- 创建并管理多个 ProxyWorker Actor
- 协调 Worker 的启动和关闭

### 13.3 ProxyWorker

**文件**: `traj_proxy/workers/worker.py`

**职责**:
- 独立的 FastAPI 应用实例
- 处理 HTTP 请求
- 管理 ProcessorManager、TrajectoryProvider 和 ModelSynchronizer

---

## 十四、模型同步机制

### 14.1 同步架构

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

### 14.2 ModelSynchronizer

**文件**: `traj_proxy/store/model_synchronizer.py`

**职责**:
- 封装 LISTEN/NOTIFY 监听和兜底全量同步
- 通过回调通知 ProcessorManager 进行注册/删除
- 管理同步任务的生命周期

**初始化流程**:
1. 首次启动执行全量同步（从数据库加载所有动态模型）
2. 启动 LISTEN/NOTIFY 监听器（实时增量同步）
3. 启动兜底定期全量同步（防止通知丢失）

### 14.3 NotificationListener

**文件**: `traj_proxy/store/notification_listener.py`

**特点**:
- 维护一个专用的数据库连接（LISTEN 需要独占连接）
- 自动重连机制（指数退避，最大延迟 60 秒）
- 连接失败时无限重试

### 14.4 Payload 格式

通知通道: `model_registry_changes`

```json
{
    "action": "register",      // 或 "unregister"
    "run_id": "",
    "model_name": "gpt-4",
    "timestamp": 1712345678.9
}
```

### 14.5 同步配置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `sync_fallback_interval` | 300 | 兜底全量同步间隔（秒） |
| `sync_max_retries` | 3 | 同步失败最大重试次数 |
| `sync_retry_delay` | 5 | 重试初始延迟（秒） |
| `listen_reconnect_delay` | 5 | LISTEN 重连初始延迟（秒） |
| `listen_max_reconnect_delay` | 60 | LISTEN 重连最大延迟（秒） |
