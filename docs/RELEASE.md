# TrajProxy 变更日志

> 本文档记录 TrajProxy 项目的版本发布历史。

---

## [0.2.4] - 2026-05-28

**类型**: 重构 + 功能增强

### 重构
- **InferClient 改用 httpx 异步 HTTP**: 从 `requests` + `ThreadPoolExecutor` 重构为 `httpx.AsyncClient` 纯协程实现，移除线程池开销，直接在事件循环中处理 TCP I/O。手动实现 502/503/504 指数退避重试，流式请求使用 `client.stream()` + `aiter_lines()` 原生异步迭代
- **ProcessorManager 异步化资源释放**: `_create_processor`、`_evict_one`、`_build_processor` 全部改为 `async`，淘汰/注销时异步关闭 InferClient 连接和释放 Tokenizer 引用。移除同步方法 `get_processor`、`get_processor_or_raise`、`_sync_load_processor`
- **TokenizerCache 异步加载**: `get_or_load` 改为 `async`，首次加载使用 `asyncio.to_thread` 避免阻塞事件循环，新增 `asyncio.Lock` 防止并发加载同一 tokenizer（双重检查）

### 新增功能
- **Processor 空闲淘汰**: 后台定期扫描，超时未访问的 Processor 自动淘汰并释放资源（`processor_idle_timeout` 可配置，默认 300 秒，0 = 禁用）
- **信号量获取超时可配置**: 信号量获取超时从硬编码改为 `semaphore_acquire_timeout` 配置项（默认 5.0 秒），适用于 chat/completions 和轨迹查询端点
- **ProcessorManager 优雅关闭**: `shutdown` 时停止空闲淘汰任务并清理 LRU 缓存

### 配置更新
- `proxy_workers.max_concurrent_requests`: 128 → 256
- `proxy_workers.semaphore_acquire_timeout`: 新增，信号量获取超时（默认 5.0 秒）
- `proxy_workers.processor_idle_timeout`: 新增，Processor 空闲淘汰超时（默认 300 秒）
- `infer_client.connect_timeout`: 60 → 10 秒
- `infer_client.max_connections`: 1000 → 0（不限制，由后端推理服务控制并发）
- `database.pool.min_size`: 10 → 2，`max_size`: 30 → 4（优化连接池资源占用）

### 依赖变更
- 新增 `httpx` 依赖（替代 `requests`）
- 移除 `requests`、`urllib3` 直接依赖

### 影响范围
- `traj_proxy/proxy_core/infer_client.py` - httpx 异步重构
- `traj_proxy/proxy_core/processor_manager.py` - 异步化 + 空闲淘汰
- `traj_proxy/proxy_core/tokenizer_cache.py` - 异步加载
- `traj_proxy/serve/routes.py` - 信号量超时可配置
- `traj_proxy/utils/config.py` - 新增配置函数
- `traj_proxy/workers/worker.py` - 启动/关闭空闲淘汰
- `configs/config.yaml` - 配置参数调整

---

## [0.2.3] - 2026-05-26

**类型**: 重构 + Bug 修复

### 重构
- **归档改为 Ray Worker 并发架构**: 生产者-消费者 asyncio Queue 模式替换为 协调器 + Ray Actor Worker 架构。协调器只做查询和 cleanup，Worker 独立进程完成 读 DB → 写文件 → 上传 全流程
- **动态分发**: Worker 完成一个 session 后立即领下一个，避免大 session 排队
- **Worker 独立 DB 连接**: 每次 `archive()` 新建 `psycopg.connect()`，不共享连接池，互不干扰
- **配置重命名**: `upload_concurrency` + `upload_queue_size` → `num_workers` (Ray Worker 进程数)

### 新增功能
- **CSB 专用存储后端**: 替代 boto3 对接华为云 CSB 网关，支持 token 认证 + 显式 AK/SK 配置
- **Tokenizer 共享缓存**: 新增 `TokenizerCache`，相同 `tokenizer_path` 只加载一次，通过引用计数管理生命周期，Processor 淘汰/注销时自动释放
- **GZIP 压缩可配置**: `archive.compress` 配置项控制归档文件是否 gzip 压缩
- **S3 上传可用性验证**: 启动时验证 S3 上传可用性，确保上传成功后才删除 DB 记录
- **S3 SSL 配置**: `verify_ssl` 配置项支持关闭 SSL 证书校验
- **Worker 进度心跳**: Ray `wait` 每 120 秒检查一次，防止 Worker 卡死；Worker 导出阶段每 60 秒打印进度
- **Worker 分阶段耗时日志**: 每个 session 输出 导出/上传/总计 三阶段耗时 + 文件大小

### Bug 修复
- **CSB 网关 multipart 500**: 改用 `put_object` 替代 `upload_file`，避免 multipart 上传 500 错误
- **CSB 网关 bucket 检查**: 跳过 bucket 存在性检查，避免网关不支持该 API 报错
- **归档 OOM**: 改为流式读取 + 进度日志，防止大表归档内存溢出
- **事件循环阻塞**: 上传阶段改用 `asyncio.to_thread` 不阻塞事件循环
- **归档清理失效**: 改为 run 级批量操作，修复事务未提交导致清理失效
- **archive_location 路径**: 存储完整绝对路径（`s3://bucket/prefix/run_id/`），E2E 测试适配文件夹路径格式
- **Tokenizer 重复加载**: Processor 不再内部加载 tokenizer，改为由 `ProcessorManager` 通过 `TokenizerCache` 注入
- **归档消费者死锁**: 上传失败时存下异常继续消费，避免 producer 因队列满阻塞死锁
- **NULL/空 session_id 崩溃**: SQL 层面过滤 `run_id` 和 `session_id` 为 NULL 或空字符串的记录，不归档
- **归档临时文件兜底清理**: finally 块确保 run 临时目录无论成功失败都被删除
- **归档测试 compose 网络不存在**: docker-compose-test.yml 的 `traj-proxy-network` 从 `external: true` 改为 `driver: bridge`，独立启动时自动创建网络，不再依赖主 compose 先启动

### 优化改进
- **归档模块精简**: 去掉无意义抽象和死代码
- **归档配置清理**: 移除独立的 stop 脚本
- **归档架构简化**: 移除 null-run 归档路径，run_id/session_id 为空不归档；`_columns` 提升为模块级常量
- **协调器连接隔离**: 查询过期 run、清理 DB、清理分区各用独立连接，互不影响

### 配置更新
- `archive.num_workers`: Ray Worker 进程数（替代 `upload_concurrency` + `upload_queue_size`）
- `archive.compress`: 控制是否 gzip 压缩（默认 true）
- `storage.verify_ssl`: S3 SSL 证书校验开关
- `storage.csb_*`: CSB 网关相关配置

### 测试
- 归档 E2E 测试适配 `archive_location` 文件夹路径格式，新增 `build_archive_file_path` 工具函数
- 分区创建增加边界检查，已存在但边界不对时自动重建
- E2E 测试 `retention_days` 和分区检查补充 PASS/FAIL 计数，`retention_days` 提取只保留数字

### 影响范围
- `traj_proxy/proxy_core/tokenizer_cache.py` - 新增 Tokenizer 共享缓存
- `traj_proxy/proxy_core/processor.py` - tokenizer 改为外部注入
- `traj_proxy/proxy_core/processor_manager.py` - 集成 TokenizerCache，淘汰/注销时释放引用
- `traj_archiver/__main__.py` - Ray 初始化 + Worker 创建 + shutdown
- `traj_archiver/session_worker.py` - 新增 Ray Actor Worker（读 DB → 写文件 → 上传）
- `traj_archiver/archiver.py` - 重构为协调器模式（动态分发 Worker）
- `traj_archiver/scheduler.py` - 适配 Worker 参数
- `traj_archiver/storage/` - CSB 存储后端
- `configs/archiver.yaml` - `num_workers` 替代 `upload_concurrency` + `upload_queue_size`
- `tests/e2e/layers/archive/` - E2E 测试适配
- `dockers/archiver/docker-compose-test.yml` - 网络定义改为自动创建

---

## [0.2.2] - 2026-05-21

**类型**: Bug 修复 + 功能增强

### Bug 修复
- **UTC 时区一致性**: 统一使用 UTC 时区感知时间 (`datetime.now(timezone.utc)`)，修复时间存储偏移问题。归档脚本、调度器、ProcessorManager、ModelRepository 全链路对齐 UTC，消除本地时区与 UTC 混用导致的 8 小时偏差
- **轨迹查看器时间显示**: 修复浏览器本地时区抵消问题。`formatTime` 强制 UTC 解析再 +8 显示，Run ID 下拉框时间统一走 `formatTime`，修正服务器时间解析中错误 +08:00 偏移的剥离逻辑
- **TokenPipeline chat_template_kwargs**: 修复 `MessageConverter` 重构时 `chat_template_kwargs` 参数丢失问题，恢复 `add_generation_prompt` / `tokenize` 透传
- **filterRunIdDropdown 未定义**: 移除多余闭合大括号，修复 JS 语法报错

### 新增功能
- **轨迹查询 fields 过滤**: `/trajectory` 和 `/trajectories/{session_id}` 接口新增 `fields` 查询参数，支持 `field_name`（包含）和 `-field_name`（排除），可按需过滤大字段以减少响应体积
- **轨迹查询超时保护**: 轨迹查询接口新增分段超时保护（DB 查询 30s、序列化 30s、信号量获取 5s），超时返回 504
- **轨迹查询分阶段耗时埋点**: 查询完成后单行日志输出 DB 查询、序列化、总耗时及响应大小，便于性能可观测
- **轨迹复制按钮**: 轨迹查看器新增「复制 JSON」按钮（记录卡片级 + 会话级），一键复制原始数据到剪贴板

### 优化改进
- **轨迹查看器性能**: 滚动修复与大数据性能优化，渲染逻辑重构，大轨迹数据滚动不再卡顿
- **归档脚本 UTC**: `scripts/archive_records.py` 和 `traj_proxy/archive/` 时间比较统一使用 UTC

### 文档更新
- **API 文档刷新**: 补充 `fields` 参数、并发限流 429、查询超时 504 说明

### 测试
- **F220**: 新增 `chat_template_kwargs` e2e 测试，覆盖 `add_generation_prompt` 和 `tokenize` 参数透传

### 影响范围
- `traj_proxy/serve/routes.py` - fields 过滤、超时保护、分阶段耗时埋点
- `traj_proxy/proxy_core/provider.py` - fields 参数透传
- `traj_proxy/proxy_core/pipeline/base.py` - UTC 时区
- `traj_proxy/proxy_core/processor_manager.py` - UTC 时区
- `traj_proxy/proxy_core/cache/prefix_cache.py` - 适配字段过滤
- `traj_proxy/proxy_core/converters/message_converter.py` - chat_template_kwargs 修复
- `traj_proxy/archive/archiver.py` - UTC 时区
- `traj_proxy/archive/scheduler.py` - UTC 时区
- `traj_proxy/store/request_repository.py` - fields 过滤查询
- `traj_proxy/store/model_repository.py` - UTC 时区
- `traj_proxy/utils/__init__.py` - 新增 `utc_now()` 工具函数
- `scripts/archive_records.py` - UTC 时区
- `scripts/replay_trajectory_viewer.html` - 时间修复、复制按钮、性能优化
- `docs/develop/api_proxy.md` - API 文档刷新
- `tests/e2e/layers/proxy/scenarios/F220_chat_template_kwargs.sh` - 新增测试

---

## [0.2.1] - 2026-05-16

**类型**: Bug 修复

### Bug 修复
- **EOS Token 一致性**: 修复 `full_conversation_text` 与 `full_conversation_token_ids` 不一致导致前缀匹配出现双重 EOS 的问题。响应处理完成后 re-decode `response_ids`（`skip_special_tokens=False`），使 `response_text` 含 EOS 与 `response_ids` 一致，`full_conversation_text = prompt_text + response_text` 自然含 EOS
- **Tokenizer 路径持久化**: 修复 tokenizer 路径在模型注册时解析为绝对路径并持久化到 DB，避免 Worker 重启后路径不一致

### 优化改进
- **run_id 兜底**: `_extract_run_id` 空 `run_id` 不再返回 `None`，统一兜底为 `DEFAULT`
- **连接池监控增强**: 连接池监控 INFO 日志新增 `queued`/`waiting`/`errors` 累计指标

### 测试
- **F114**: 新增 EOS Token 一致性 e2e 测试（跨轮对比 EOS + finish_reason 验证）
- **F215**: 新增 Processor 懒加载 e2e 测试
- **F216**: 新增 LRU 缓存命中 e2e 测试
- **F217**: 新增预置模型懒加载 e2e 测试
- **F218**: 新增 LRU 淘汰 e2e 测试
- **F219**: 新增轨迹查询 fields 参数 e2e 测试

### 影响范围
- `traj_proxy/proxy_core/pipeline/token_pipeline.py` - EOS 一致性修复
- `traj_proxy/proxy_core/processor_manager.py` - tokenizer 路径持久化
- `traj_proxy/serve/routes.py` - run_id 兜底
- `traj_proxy/store/database_manager.py` - 连接池监控增强
- `tests/e2e/layers/nginx/scenarios/F114_eos_consistency.sh` - EOS 一致性测试
- `tests/e2e/layers/proxy/scenarios/F215-F219` - LRU 相关及 fields 参数测试

---

## [0.2.0] - 2026-05-12

### 优化改进
- **Worker 并行启动**: 多个 ProxyWorker 从串行 `await` 改为 `asyncio.gather` 并行初始化，启动耗时从 N 倍降至与单 Worker 相当
- **Processor 懒加载**: Processor 从 Worker 启动时全量预加载改为首次请求时按需加载，消除启动阶段的 tokenizer 加载和 pipeline 构建耗时
- **Processor LRU 缓存**: 引入 `OrderedDict` 实现 LRU 淘汰策略，每进程最多常驻 N 个 Processor（默认 32，可配置），低频模型自动淘汰后下次请求时重新加载
- **并发加载保护**: `asyncio.Lock` + 双重检查防止同一未加载模型的并发请求重复创建 Processor

### 配置更新
- **processor_cache_max_size**: 新增 `processor_manager.processor_cache_max_size` 配置项，控制单进程 LRU 缓存上限（默认 32）

### 影响范围
- `traj_proxy/workers/manager.py` - Worker 并行启动
- `traj_proxy/proxy_core/processor_manager.py` - LRU 缓存、懒加载、并发保护
- `traj_proxy/serve/routes.py` - 适配 `get_processor_async` 和 `ModelConfig` 返回值
- `traj_proxy/workers/worker.py` - 适配新属性
- `traj_proxy/utils/config.py` - 新增 `get_processor_cache_max_size()`
- `configs/config.yaml` - 新增配置项
- `dockers/allinone/configs/config.yaml` - 同步配置
- `dockers/compose/configs/config.yaml` - 同步配置

---

## [0.1.9] - 2026-05-12

### Bug 修复
- **大轨迹查询连接重置**: 修复查询 5-40MB 轨迹数据时 `ClientPayloadError` 报错（根因：`json.dumps()` 同步阻塞 uvicorn 事件循环，导致 HTTP 连接在序列化期间超时断开）。改为线程池序列化 + orjson 加速

### 优化改进
- **PrefixMatchCache 查询优化**: 前缀缓存查询改为仅选取 `full_conversation_text` 和 `full_conversation_token_ids` 字段，单次查询数据量从 5-40MB 降至 KB 级
- **PrefixMatchCache 匹配算法**: 候选记录按 `token_ids` 长度降序排列，首个匹配即为最长前缀，消除逐一遍历
- **并发限流**: `chat/completions` 端点新增 `asyncio.Semaphore` 并发控制（可配置，默认 128），超限返回 HTTP 429，实现服务端背压
- **连接池监控**: `DatabaseManager` 新增后台监控任务，每 30s 采样连接池使用量，追踪峰值，使用率超过 80% 时告警
- **JSON 序列化**: 引入 `orjson`（2-5x 加速），优先使用 `orjson.dumps()` 返回 bytes，减少大响应序列化耗时
- **Uvicorn Keep-Alive**: `timeout_keep_alive` 从默认 5s 提升至 65s，减少长时间处理场景下的连接断开

### 配置更新
- **max_concurrent_requests**: 新增 `proxy_workers.max_concurrent_requests` 配置项，控制单 Worker 最大并发请求数
- **orjson**: 新增 `orjson>=3.9.0` 依赖

### 影响范围
- `traj_proxy/serve/routes.py` - 并发限流、线程池序列化、orjson 优先
- `traj_proxy/proxy_core/cache/prefix_cache.py` - 前缀匹配算法优化
- `traj_proxy/store/request_repository.py` - 新增 `get_prefix_candidates()` 方法
- `traj_proxy/store/database_manager.py` - 连接池使用量监控
- `traj_proxy/workers/worker.py` - Semaphore 创建与挂载
- `traj_proxy/workers/manager.py` - uvicorn keep-alive 配置
- `traj_proxy/utils/config.py` - 新增 `get_max_concurrent_requests()` 配置函数
- `configs/config.yaml` - 新增并发限流配置项
- `dockers/allinone/configs/config.yaml` - 同步配置
- `dockers/compose/configs/config.yaml` - 同步配置
- `requirements.txt` - 新增 orjson 依赖

---

## [0.1.8] - 2026-04-29

### 新增功能
- **自定义 Parser 按需发现**: 支持从 `custom_parsers` 目录按需发现和加载 parser，无需修改代码即可扩展解析能力
- **Hermes 工具解析器**: 新增 `hermes` parser，支持 Hermes 模型的工具调用格式
- **Qwen3XML 工具解析器**: 新增 `qwen3_xml` parser，支持 Qwen3 XML 格式

### 配置更新
- **models_dir**: 新增配置项，指定 tokenizer 加载路径（默认 `/app/models`）
- **custom_parsers_dir**: 新增配置项，指定自定义 parser 目录（默认 `/app/custom_parsers`）

### 测试
- **F212**: 新增自定义 Tool Parser 场景测试
- **F213**: 新增自定义 Reasoning Parser 场景测试

### 影响范围
- `traj_proxy/proxy_core/parsers/parser_manager.py` - Parser 管理器，新增按需发现机制
- `traj_proxy/proxy_core/parsers/vllm_compat/tool_parsers/` - 新增 hermes 和 qwen3xml 解析器
- `traj_proxy/utils/config.py` - 配置模块，新增路径配置函数
- `configs/config.yaml` - 配置文件，新增路径配置项
- `tests/e2e/layers/proxy/scenarios/` - E2E 测试场景

---

## [0.1.7] - 2026-04-24

### 新增功能
- **Header 透传**: 支持将客户端请求 header 透传到推理服务（黑名单模式，排除 HTTP 基础 header 和 TrajProxy 内部 header）
- **Chat→Completion 参数转换**: InferClient 新增 `_transform_chat_params_to_completion()` 方法，自动过滤不兼容参数并映射参数名
- **请求参数透传**: chat/completions 请求参数从白名单模式改为黑名单模式，新增参数自动透传

### 优化改进
- **InferClient 重构**: 提取统一异常处理（`_wrap_request_error`），新增异步上下文管理器支持，参数过滤逻辑定义为类常量
- **参数兼容性**: `response_format` 和 `bad_words` 从不兼容参数列表移除，允许透传到推理服务

### Bug 修复
- **Header 未透传**: 修复请求 header 未转发到推理服务的问题
- **Tokenizer 加载**: 修复 tokenizer 无法加载的问题，添加 `trust_remote_code=True`

### 测试
- **E2E 参数透传**: 新增 F210（direct 模式）和 F211（TITO 模式）参数透传测试场景
- **Mock 推理服务**: 新增 mock_infer_server.py 用于验证参数和 header 透传

### 影响范围
- `traj_proxy/proxy_core/infer_client.py` - 推理客户端
- `traj_proxy/proxy_core/context.py` - 处理上下文
- `traj_proxy/proxy_core/pipeline/` - 流水线模块
- `traj_proxy/proxy_core/processor.py` - 请求处理器
- `traj_proxy/serve/routes.py` - API 路由
- `tests/e2e/layers/proxy/` - E2E 测试

---

## [0.1.6] - 2026-04-22

### 优化改进
- **TrajectoryViewer UI**: 更新界面标签名称（Server → SERVER_ADDRESS，Run ID → MY_JOB_NAME(Run ID)），默认服务器地址从 `http://localhost:12300` 改为 `http://localhost:80`

### 文档更新
- **文档命名**: 变更日志文件名改为大写 `RELEASE.md`

### 影响范围
- `scripts/replay_trajectory_viewer.html` - 轨迹回放查看器
- `docs/RELEASE.md` - 变更日志

---

## [0.1.5] - 2026-04-16

### 新增功能
- **归档功能**: 新增轨迹查询 API，支持历史数据归档与查询
- **All-in-One 部署模式**: 支持 TITO 架构，提供一体化部署方案
- **Jinja 模板支持**: TITO 模式支持自定义 Jinja 模板
- **Docker 脚本增强**: 启动脚本支持 start/stop/restart 参数，提升部署灵活性
- **Trajectory Viewer**: 新增轨迹回放查看器，支持可视化的历史轨迹重放
- **CORS 支持**: 添加跨域资源共享支持，便于前端集成
- **InferClient 配置**: 新增推理客户端配置项

### 优化改进
- **API 设计**: 优化 trajectories 接口为符合 RESTful 设计规范
- **配置管理**: 新增配置示例文件（config.yaml.example、litellm.yaml.example、nginx.conf.example）
- **部署文档**: 更新部署文档，反映启动脚本的新参数支持
- **超时配置**: 优化超时配置逻辑
- **日志输出**: 调整日志级别为 DEBUG，便于调试
- **归档测试**: 优化归档测试脚本的日志检查机制

### Bug 修复
- **Prisma 引擎**: 构建时预生成 Prisma 引擎，支持离线部署环境
- **Mermaid 图表**: 修复设计文档中 Mermaid 图表语法错误
- **流式解析**: 清理流式 tool 解析冗余输出问题
- **前缀测试**: 修复前缀测试用例
- **推理异常**: 修复推理异常时后续相同 session-id 受影响的问题
- **容器镜像**: 优化容器镜像命名
- **allinone**: 修复 allinone 运行失败问题
- **启动部署**: 优化启动部署流程

### 文档更新
- **API 文档**: 刷新 API 文档，修正 trajectories 接口路径
- **数据库归档**: 新增数据库归档机制设计文档
- **TITO 架构**: 新增 TITO 架构设计文档并合并 v1/v2 为统一版本
- **文档一致性**: 刷新文档与代码一致性

### 其他
- **环境变量**: 添加 DATABASE_URL 环境变量配置
- **目录结构**: 调整脚本目录结构
- **测试用例**: 新增 Reasoning + Tool 组合场景测试用例 (F107, F108)

### 影响范围
- `traj_proxy/archive/` - 归档功能模块
- `traj_proxy/serve/routes.py` - API 路由与 CORS 支持
- `scripts/start_docker_*.sh` - Docker 启动脚本
- `scripts/replay_trajectory_viewer.html` - 轨迹回放查看器
- `docs/design/tito.md` - TITO 架构文档
- `configs/*.example` - 配置示例文件

---

## [0.1.3] - 2026-04-13

### 新增功能
- **归档功能**: 新增轨迹查询 API，支持历史数据归档与查询
- **All-in-One 部署模式**: 支持 TITO 架构，提供一体化部署方案
- **Jinja 模板支持**: TITO 模式支持自定义 Jinja 模板
- **InferClient 配置**: 新增推理客户端配置项
- **配置示例**: 添加配置示例文件，便于快速上手

### 优化改进
- **超时配置**: 优化超时配置逻辑
- **日志输出**: 调整日志级别为 DEBUG，便于调试
- **归档测试**: 优化归档测试脚本的日志检查机制

### Bug 修复
- **Mermaid 图表**: 修复 Mermaid 图表语法错误
- **流式解析**: 清理流式 tool 解析冗余输出问题
- **前缀测试**: 修复前缀测试用例
- **推理异常**: 修复推理异常时后续相同 session-id 受影响的问题
- **容器镜像**: 优化容器镜像命名
- **allinone**: 修复 allinone 运行失败问题
- **启动部署**: 优化启动部署流程

### 文档更新
- **数据库归档**: 新增数据库归档机制设计文档
- **TITO 架构**: 新增 TITO 架构设计文档
- **文档一致性**: 刷新文档与代码一致性

### 其他
- **环境变量**: 添加 DATABASE_URL 环境变量配置
- **目录结构**: 调整脚本目录结构
- **测试用例**: 新增 Reasoning + Tool 组合场景测试用例 (F107, F108)

---

## [0.1.2] - 2026-04-10

### 新增功能
- **All-in-One 部署模式**: 新增 All-in-One 部署模式及 TITO 架构设计文档
- **Jinja 模板**: 支持 TITO 模式自定义 Jinja 模板

### Bug 修复
- **Tokenizer 解码**: 对齐 vllm tokenizer 解码行为
- **流式解析**: 清理流式 tool 解析冗余输出问题
- **前缀测试**: 修复前缀测试用例

### 重构优化
- **数据库重构**: 重构数据库架构
- **Core 模块**: 重构 Core 模块
- **Parser 重构**: 重构 Parser 模块

---

## [0.1.1] - 2026-04-10

### 新增功能
- **项目初始化**: 完成 TrajProxy 项目基础架构搭建
- **基础功能**: 实现代理服务核心功能
- **Provider 支持**: 添加多 Provider 支持
- **Parser 框架**: 实现 Parser 解析器框架
- **Claude 兼容**: 实现 Claude 兼容性支持
- **Qwen 模型**: 添加配置、测试和 Qwen 模型支持

### 重构优化
- **目录结构**: 调整目录结构并合并 worker
- **Parser 架构**: 重构 Parser 架构并优化实现
- **路由配置**: 重构路由配置
- **数据库**: 统一数据库入口，优化初始化逻辑
- **系统架构**: 优化系统架构

### Bug 修复
- **流式响应**: 修复直接转发流式场景响应字段丢失问题
- **空值处理**: 增强流式响应字段的空值处理
- **run_id 解析**: 完善 run_id 解析和 model 参数格式支持
- **推理服务**: 提升推理服务访问稳定性及优化模型路径
- **Tool 流式解析**: 修复 Tool 流式解析异常
- **流式字段**: 修复流式字段缺失问题

### 文档更新
- **Parser 文档**: 添加 Parser 文档
- **架构文档**: 用 Mermaid 图重写架构文档，增加部署视图和请求流程图
- **API 文档**: API 文档迁移
- **数据库架构**: 添加数据库架构优化方案和镜像构建脚本
- **手动测试**: 添加手动测试文档和脚本

### 测试
- **Tool Calling**: 添加 tool calling 测试
- **Token 用例**: 增加 Token 用例
- **流式用例**: 新增 token 流式用例
- **测试整理**: 测试文件整理与重构
