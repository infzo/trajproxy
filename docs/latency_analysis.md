# TrajProxy 耗时分析报告

> 测试日期: 2026-04-09
> 测试环境: LMStudio Qwen3.5-2B-6bit (MLX, macOS) + Docker TrajProxy
> 模型配置: `token_in_token_out=true`, `tool_parser=qwen3_coder`, `reasoning_parser=qwen3`

---

## 一、调用链路总览

**Token-in-Token-out + Tool Parser + 流式模式**的完整调用链：

```
客户端请求
  |
  v
routes.py: chat_completions()          <- route_receive
  |  解析请求体、校验参数、获取 processor
  v
Processor.process_stream()
  |  创建 ProcessContext               <- context_create
  v
TokenPipeline.process_stream()
  |
  +-- 1. _transform_messages()          <- message_convert
  |     MessageConverter.convert()
  |       +-- _preprocess_messages()       [deepcopy + JSON 解析]
  |       +-- tokenizer.apply_chat_template()  [Jinja2 模板渲染]
  |
  +-- 2. _encode_text()                 <- token_encode
  |     TokenConverter.encode()
  |       +-- PrefixMatchCache.encode_with_cache()  <- cache_lookup
  |           +-- DB 查询历史 session       [PostgreSQL JOIN 查询]
  |           +-- _find_longest_prefix_match() [字符串前缀匹配 O(n*m)]
  |           +-- tokenizer.encode()        [未缓存部分的编码]
  |
  +-- 3. infer_client.send_completion_stream()  <- infer_connect
  |     |  线程池中执行 requests.post(stream=True)
  |     |  HTTP 连接建立 + 等待推理服务首 token
  |     v
  |  +-------------------- 每个 stream chunk 循环 --------------------+
  |  |                                                               |
  |  |  a. InferResponseParser.parse_stream_chunk()                  |
  |  |     解析 SSE 行 -> JSON -> 提取 token_ids/text                |
  |  |                                                               |
  |  |  b. TokenConverter.decode_streaming()              <- decode  |
  |  |     tokenizer.decode(buffer_ids)                             |
  |  |     每次解码整个缓冲区（非增量）                                 |
  |  |                                                               |
  |  |  c. Parser.extract_reasoning_streaming()          <- parse   |
  |  |     vLLM reasoning parser                                    |
  |  |                                                               |
  |  |  d. Parser.extract_tool_calls_streaming()                    |
  |  |     vLLM tool parser (e.g. qwen3_coder)                      |
  |  |     +-- _build_request() 每次构造 ChatCompletionRequest       |
  |  |     +-- tool_parser.extract_tool_calls_streaming()            |
  |  |                                                               |
  |  |  e. StreamChunkBuilder.build_chunk()              <- build   |
  |  |     构建 OpenAI 格式 chunk dict                                |
  |  |                                                               |
  |  +---------------------------------------------------------------+
  |
  +-- 4. _finalize_stream()             <- finalize_stream
  |     +-- 构建 full_conversation
  |     +-- 补全 token_response 结构
  |     +-- ResponseBuilder.build()      <- response_build
  |     |    +-- tool parser 解析完整输出
  |     |    +-- 构建 OpenAI ChatCompletion 响应
  |     +-- _store_trajectory()          <- db_store
  |          +-- RequestRepository.insert()
  |               +-- INSERT request_metadata
  |               +-- INSERT request_details_active
  |                    [事务双写，含多个 Json 大字段]
  |
  v
routes.py: SSE 序列化 (json.dumps) + yield
  |
  v
客户端收到响应
```

---

## 二、关键计时埋点

| 文件 | 阶段标记 | 说明 |
|------|---------|------|
| `routes.py` | `route_t0` | 请求入口时间戳 |
| `routes.py` | 日志输出 | 路由层总耗时 |
| `token_pipeline.py` | `message_convert` | 消息转换耗时 |
| `token_pipeline.py` | `token_encode` | Token编码耗时 |
| `token_pipeline.py` | `infer_connect` | 推理连接+首token耗时 |
| `token_pipeline.py` | `first_token` (TTFB) | 首 token 时间 |
| `token_pipeline.py` | chunk级 decode/parse/build | 每个 chunk 的三步耗时 (抽样输出) |
| `token_pipeline.py` | `inference` | 非流式推理耗时 |
| `token_pipeline.py` | `token_decode` | 非流式解码耗时 |
| `token_pipeline.py` | `response_build` | 响应构建耗时 |
| `token_pipeline.py` | `finalize_stream` | 流式收尾耗时 |
| `token_pipeline.py` | `db_store` | DB存储耗时 |
| `token_converter.py` | `cache_lookup` | 缓存查找耗时 |
| `infer_client.py` | 日志输出 | HTTP 连接耗时 |
| `request_repository.py` | 日志输出 | DB 写入耗时 |

---

## 三、实测数据

### 测试1: 简单对话 (无tools, 26 tokens输出)

| 阶段 | 耗时 | 占比 |
|------|------|------|
| message_convert | 6.26ms | 1.3% |
| token_encode | 9.59ms | 2.0% |
| infer_connect | 357.69ms | **74.5%** |
| finalize_stream | 20.03ms | 4.2% |
| db_store | 17.91ms | 3.7% |
| **已记录总计** | **420.60ms** | |
| 路由层总耗时 | 988.13ms | (含流式传输时间) |
| 首 token(TTFB) | 376.88ms | |
| 总 chunks | 26 | |

### 测试2: 长输出请求 (237 tokens输出)

| 阶段 | 耗时 | 占比 |
|------|------|------|
| message_convert | 0.56ms | 0.3% |
| token_encode | 2.54ms | 1.2% |
| infer_connect | 192.13ms | **86.9%** |
| finalize_stream | - | |
| db_store | 10.85ms | 4.9% |
| **已记录总计** | **220.96ms** | |
| 路由层总耗时 | **5773.50ms** | |
| 首 token(TTFB) | 197.05ms | |

chunk 级别 decode 耗时增长趋势:
```
chunk#0:   decode=0.06ms
chunk#10:  decode=0.10ms  (+0.04ms)
chunk#50:  decode=0.08ms  (持平)
chunk#100: decode=0.15ms  (+0.09ms)
chunk#200: decode=0.30ms  (+0.15ms)
```

### 测试3: 带 tools 请求 (tool call 触发, 54 tokens输出)

| 阶段 | 耗时 | 占比 |
|------|------|------|
| message_convert | 0.65ms | 0.1% |
| token_encode | 4.49ms | 0.7% |
| cache_lookup | 4.06ms | 0.6% |
| infer_connect | 623.68ms | **94.4%** |
| finalize_stream | 15.25ms | 2.3% |
| db_store | 12.51ms | 1.9% |
| **已记录总计** | **660.65ms** | |
| 路由层总耗时 | 1915.15ms | (大量 chunk 被 parser 吞掉) |
| 首 token(TTFB) | 633.21ms | |
| 总 chunks(输出) | 10 | |

### 测试4: 直连模式对比 (267 tokens输出)

| 指标 | 耗时 |
|------|------|
| 路由层总耗时 | 6846.69ms |
| 内部处理 | 6835.48ms |
| 总 chunks | 267 |

### 测试5: 多轮对话 (同一 session, 2轮)

| 轮次 | prompt_tokens | message_convert | token_encode | infer_connect | TTFB | 总耗时 |
|------|-------------|----------------|-------------|--------------|------|-------|
| 第1轮 | 21 | 1.65ms | 8.19ms | 440.70ms | 452.82ms | 502.17ms |
| 第2轮 | 41 | 1.25ms | 9.97ms | 173.36ms | 186.18ms | 225.53ms |

> 注意: 第2轮 infer_connect 显著降低 (440 -> 173ms), 这是推理引擎自身的 KV cache 命中, 非 TrajProxy 前缀缓存。
> 两轮 `cache_hit_tokens=None`, 前缀缓存未生效。

---

## 四、关键发现

### 1. infer_connect 是绝对瓶颈 (占 74-94% 代理层耗时)

`infer_connect` 包含 HTTP 连接建立 + 等待推理服务返回首 token。其中 HTTP 连接本身仅 5-23ms (`stream_connected`), **绝大部分是推理服务本身的 prefill + 首个 token 生成时间**。这不是 TrajProxy 的问题，而是推理引擎的固有延迟。

### 2. Token-in-Token-out 代理层总开销很小

| 请求 | 代理层总耗时 | 推理等待(infer_connect) | 纯代理开销 |
|------|------------|----------------------|-----------|
| 简单对话 | 420ms | 358ms | **~62ms** |
| 长输出 | 221ms | 192ms | **~29ms** |
| Tools请求 | 661ms | 624ms | **~37ms** |

纯代理开销 (message_convert + token_encode + cache_lookup + finalize + db_store) 仅 **29-62ms**, 非常轻量。

### 3. decode 重复全量解码问题在当前输出长度下不明显

从 chunk#0 到 chunk#200, decode 耗时从 0.06ms 增长到 0.30ms (5倍), 但绝对值仍很小。这在 Qwen3.5-2B 这种小 tokenizer 下不严重。但如果输出更长 (如 4000+ tokens) 或使用更大的 tokenizer, 增长会更显著。

### 4. Tool Parser 大量吞 chunk

Tools 请求收到 54 个 infer token 但只输出了 10 个 chunks 给客户端。大量中间 chunk 被 tool parser 返回 `None` 吞掉 (`content=None, tool_calls=1`), 这是预期行为 (parser 累积完整 tool call 后一次性输出), 但不影响延迟感知——因为 infer 服务端的 token 仍在持续产生。

### 5. 前缀缓存未命中

所有请求 `cache_hit_tokens=None`。多轮对话测试中第二轮也未命中。原因: `full_conversation_text` 为空或格式不匹配 (第一轮请求时 `full_conversation_text` 需要在 `_finalize_stream` 中才构建完成并写入 DB, 而 DB 写入是异步的, 第二轮请求时可能还未完成)。

---

## 五、延迟分布图

```
+--------------------------------------------------------------------+
|                     请求处理全链路耗时分布                            |
+--------------------------------------------------------------------+
|                                                                    |
|  T0 ---- T1 ---- T2 ---- T3 ======================== T4 -- T5 -- T6      |
|  |       |       |       |                           |     |     |       |
|  |  1ms  | ~5ms  | ~5ms  |      200-650ms            |15ms |12ms |       |
|  |       |       |       |    (推理服务计算)           |     |     |       |
|  v       v       v       v                           v     v     v       |
|  路由   消息    Token    推理连接                 收尾   响应  DB存储     |
|  解析   转换    编码     (HTTP+推理)               处理   构建            |
|         ~1ms   ~5ms                              ~15ms  ~0ms  ~12ms     |
|                                                                    |
|  ==== = 推理服务等待 (瓶颈)                                          |
|  ---- = TrajProxy 代理处理                                          |
|                                                                    |
|  首 token 延迟 (TTFB) = T0 -> T4 = infer_connect + 前处理           |
|  总延迟 = T0 -> T6 = 路由层总耗时                                    |
|  代理开销 = T0-T1 + T1-T2 + T2-T3 + T4-T5 + T5-T6 = ~30-60ms     |
+--------------------------------------------------------------------+
```

---

## 六、优化建议

### P0: 前缀缓存失效修复

- **实测**: `cache_hit_tokens=None`, 缓存完全未工作
- **根因**: 需要排查 `get_by_session` 查询时机或文本匹配逻辑
- **影响**: 多轮对话场景下, 每轮都重新 encode 全量 prompt, 本可省掉 80%+ 的 encode 开销

### P1: DB 存储异步化

- **实测**: 8-18ms
- **建议**: 流式结束后 DB 存储不阻塞客户端响应, 可改为 fire-and-forget
- **节省**: ~12ms 尾部延迟感知

### P2: decode 全量解码在长输出场景优化

- **实测**: 当前 237 tokens 输出仅 0.3ms/chunk, 不紧迫
- **预估**: 4000+ tokens 输出时可能到 5-10ms/chunk, 在大模型输出场景值得优化
- **方案**: 改为增量解码, 只解码新增 token_ids

### P3: Tool Parser 缓存 ChatCompletionRequest

- **实测**: tool parser 每个 chunk 都调用 `_build_request()` 构造 `ChatCompletionRequest`
- **影响**: 当前绝对开销很小 (~0.02-0.3ms), 但可优化
- **方案**: 在流式开始时一次性构造, 后续 chunk 复用
