# TrajProxy 变更日志

> 本文档记录 TrajProxy 项目的版本发布历史。

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
