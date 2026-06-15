# TrajProxy 变更日志

> 本文档记录 TrajProxy 项目的版本发布历史。
> 本文件中引用的场景 ID 为当时使用的编号（F1xx/F2xx/F3xx/P100 等旧前缀）。
> 2026-06-08 编号体系重构后（N=Nginx, P=Proxy, T=Performance），请参考 docs/testing/test-case-catalog.md 获取当前编号。

---

## [0.3.3] - 2026-06-15

**类型**: 可观测性增强 + Grafana 优化 + 文档

### 新增功能
- **管道模式指标**: 为 `trajproxy_requests_total` 新增 `pipeline_mode` 标签（`direct`/`tito`），自动标记请求经过的管道类型
- **缓存命中率计数器**: 新增 `trajproxy_cache_lookups_total` 指标，追踪前缀缓存命中/未命中次数
- **推理重试计数**: 新增 `trajproxy_infer_retries_total` 指标（5min 面板），追踪推理内部重试次数
- **ProcessContext pipeline_mode 字段**: `ProcessContext` 新增 `pipeline_mode` 字段，`DirectPipeline`/`TokenPipeline` 创建上下文时自动标记

### Grafana 可视化优化
- **计数面板取整**: 所有 `increase()` 计数面板包裹 `round()`，统一设置 `decimals: 0`，消除浮点显示
- **TITO 缓存命中率**: 从累计统计改为 1h 滚动窗口（`increase(...[1h])`），进程重启不再导致命中率归零
- **错误分类面板统一**: 推理/轨迹错误细分面板统一中文前缀（推理-/内部-/DB-/客户端-），请求错误细分改为按 `outcome` 分类
- **限流上报补全**: 轨迹请求错误细分新增 `rate_limit` 分类，信号量拒绝时补全 `rate_limit` 错误上报
- **推理行布局调整**: 面板宽度改为 4+4+4 三列布局
- **DIRECT vs TITO 占比面板**: 新增按 `pipeline_mode` 分组的请求占比面板
- **时间序列样式**: 改为非堆叠线条样式，优化布局宽度

### Bug 修复
- **推理重试次数硬编码**: 修复 `observe_inference_stream` 装饰器 `retry_count` 硬编码为 0 的问题，改为从 `InferClient._last_retry_count` 读取实际重试次数
- **模型生命周期面板噪音**: 移除模型生命周期面板中不相关的轨迹存储错误指标

### 文档更新
- **可观测性指南**: 新增 `docs/guide/observability.md`，详细说明指标体系、Grafana 面板配置与告警规则使用
- **Grafana Dashboard 使用指南**: 新增 `docs/guide/grafana-dashboard.md`，面向运维和使用者的面板说明文档，介绍两个 Dashboard 的面板结构、各图表作用、颜色含义、告警规则和常见排查场景
- **可观测性设计文档精简**: `docs/design/observability.md` 大幅精简，移除已实现的详细设计，保留架构决策和未来演进方向
- **部署指南更新**: `docs/guide/deployment.md` 补充可观测性部署说明
- **推理流式/非流式面板说明**: 补充时间范围说明

### 影响范围
- `traj_proxy/observability/metrics_collector.py` - 新增 cache_lookups / infer_retries / pipeline_mode 指标定义
- `traj_proxy/observability/decorators.py` - 修复流式重试次数读取
- `traj_proxy/observability/events.py` - 事件参数补全
- `traj_proxy/proxy_core/context.py` - ProcessContext 新增 pipeline_mode 字段
- `traj_proxy/proxy_core/pipeline/direct_pipeline.py` - 自动标记 pipeline_mode=direct
- `traj_proxy/proxy_core/pipeline/token_pipeline.py` - 自动标记 pipeline_mode=tito
- `traj_proxy/serve/routes.py` - 信号量拒绝补全 rate_limit 上报
- `dockers/observability/configs/grafana/dashboard-src/` - Dashboard 分片全面更新
- `docs/guide/observability.md` - 新增可观测性使用指南
- `docs/guide/grafana-dashboard.md` - 新增 Grafana Dashboard 使用指南
- `docs/design/observability.md` - 设计文档精简
- `docs/guide/deployment.md` - 部署指南更新

---

## [0.3.2] - 2026-06-12

**类型**: 功能增强 + 重构

### 新增功能
- **可观测性系统 (V3)**: 全新 `traj_proxy/observability/` 模块，基于 EventBus 事件总线架构，实现端到端可观测性
  - **Prometheus 指标**: 25+ 自定义指标 + 6 进程级指标，涵盖请求核心（总量/耗时/活跃数/并发利用率）、流式（TTFT/完成度）、分阶段耗时、Token 统计、下游依赖（推理/DB/存储）、HTTP API 层等维度
  - **EventBus 事件驱动**: 请求生命周期、并发拒绝、信号量获取、推理完成/错误、流式断连、模型生命周期、轨迹存储异常等关键事件统一发布/订阅
  - **7 状态 Outcome 推导**: success / stream_partial / error_client / error_server / error_infer / error_store / timeout，自动从 ProcessContext 推导请求结果
  - **推理错误 6 类细分**: `classify_infer_error` 基于 `__cause__` 精确分类（connect_timeout / read_timeout / connection / rate_limited / http_error / unknown）
  - **HTTP API 层监控**: `api_metrics_middleware` 中间件记录路由级请求数与耗时，`classify_route` 自动识别 API 端点类型
  - **轨迹查询指标**: 新增记录数、响应体积、查询耗时等轨迹查询维度指标
  - **`/metrics` 端点**: Prometheus 拉取端点，自动暴露全部指标
  - **结构化日志**: 支持 `LOG_FORMAT=json` 环境变量切换 JSON 格式输出
  - **健康检查**: `health_checker` 模块提供应用级健康状态检测

### Grafana 可视化
- **Dashboard 分片构建系统**: `dashboard-src/` 目录按 row 碎片化维护（`row-0-overview.json` ~ `row-4-system.json`），`build_dashboard.py` 组装最终 dashboard，`make dashboard` 一键构建
- **5 行 30+ 面板 Dashboard**: Overview（请求总览/错误率/并发度）、Traffic（流量分布/趋势）、Inference（推理耗时/错误归因/模型混合）、Trajectory（存储耗时/记录量）、System（CPU/内存/进程 RSS）
- **Model-Mix Dashboard**: 独立的模型使用分布面板
- **推理错误中文标签**: 6 种 error_type 添加中文友好 displayName，图例展示描述性标签替代技术标识符
- **进程内存监控**: System 面板补充 `process_resident_memory_bytes` 查询，各实例内存占用可观测

### AlertManager 告警
- **告警规则**: 新增 `alert_rules.yml`，覆盖请求错误率、推理错误率、并发利用率、DB 连接池使用率、轨迹存储错误等关键告警
- **告警通知**: `alertmanager.yml` 配置告警路由与接收端

### 重构
- **可观测性配置统一**: 将 `docker/observability` 和 `dockers/compose/configs` 下的分散配置统一迁移至 `dockers/observability/configs/`，消除重复配置
- **独立部署解耦**: 从 `dockers/compose/docker-compose.yml` 移除 observability 服务定义，改为 `dockers/observability/docker-compose.yml` 独立部署
- **管理脚本合并**: 合并 `add_node_observability.sh` / `remove_node_observability.sh` 为统一的 `start_docker_observability.sh`，支持 start / stop / restart / add-node / remove-node / sync 子命令
- **遗留归档清理**: 移除 `traj_proxy/archive/` 遗留归档代码，统一使用独立的 `traj_archiver` 包；删除废弃的 `export_database.py` 脚本
- **Dashboard 碎片化构建**: 从单体 JSON 改为分 row 维护 + `build_dashboard.py` 组装，降低大 JSON 维护成本

### 配置更新
- `configs/config.yaml`: 新增 `observability.metrics_enabled` 等可观测性配置项
- `dockers/observability/.env.example`: 环境变量模板（Prometheus/Grafana/AlertManager 参数）
- `dockers/observability/configs/prometheus/prometheus.yml`: Prometheus 采集配置
- `dockers/observability/configs/prometheus/alert_rules.yml`: 告警规则定义
- `dockers/observability/configs/alertmanager/alertmanager.yml`: 告警路由配置
- `dockers/observability/configs/grafana/`: Grafana provisioning 配置（datasource + dashboard）

### 依赖变更
- 新增 `prometheus-client>=0.20.0`（Prometheus 指标暴露）

### 影响范围
- `traj_proxy/observability/` - 全新可观测性模块（EventBus / decorators / metrics_collector / health_checker / json_formatter / label_guards / outcome / request_context / request_summary）
- `traj_proxy/serve/routes.py` - API 层指标中间件 + 轨迹查询事件发射
- `traj_proxy/workers/worker.py` - classify_route + api_metrics_middleware 集成
- `traj_proxy/workers/route_registrar.py` - 路由注册增强
- `traj_proxy/proxy_core/pipeline/base.py` - ProcessContext 新增 store_duration_ms
- `traj_proxy/proxy_core/pipeline/direct_pipeline.py` - 阶段耗时 + 推理错误事件
- `traj_proxy/proxy_core/pipeline/token_pipeline.py` - 阶段耗时 + 推理错误事件
- `traj_proxy/proxy_core/processor.py` - 可观测性集成点
- `traj_proxy/proxy_core/processor_manager.py` - 模型生命周期事件
- `traj_proxy/proxy_core/infer_client.py` - 推理错误分类事件
- `traj_proxy/proxy_core/context.py` - ProcessContext 字段扩展
- `traj_proxy/store/database_manager.py` - DB 连接池 Prometheus 指标上报
- `traj_proxy/utils/logger.py` - JSON 格式化器支持
- `dockers/observability/` - 全新独立可观测性部署目录（docker-compose + configs + data）
- `configs/config.yaml` - 可观测性配置项
- `scripts/start_docker_observability.sh` - 统一管理脚本
- `docs/design/observability.md` - 可观测性设计文档升级至 V3

---

## [0.3.1] - 2026-06-11

**类型**: 重构 + 功能增强 + Bug 修复

### 重构
- **E2E 测试体系重构**: 按功能分层重编号全部测试场景（N=nginx, P=proxy, T=performance, A=archive, C=comparison），场景文件以层级前缀命名，内部 ID 同步更新
- **新增 Comparison 对比层 C1xx/C2xx/C3xx**: C1xx（OpenAI Direct × 5 组合 + 多轮 2 场景）、C2xx（Claude 格式同构）、C3xx（Responses API 4 场景），共 18 个对比场景，覆盖非流式/流式/多轮全矩阵
- **E2E 框架增强**: 提取并发执行框架到公共 `utils.sh`，支持 `--jobs N` 并行执行和日志落盘；新增 `--only/--skip` CLI 过滤标志和失败汇总报告
- **共享 HTTP client 重构**: E2E 对比层提取共享 HTTP client 工具函数，减少重复代码
- **解析器兼容层重构**: 对齐 vLLM 解析顺序和 `tool_choice` 分支逻辑，提升 Direct Pipeline 流式一致性

### 新增功能
- **Nginx Responses API 路由**: nginx 配置新增 `/v1/responses` 路由，支持 OpenAI Responses API 请求透传
- **LiteLLM custom_openai provider**: 将 `openai/*` 改为 `custom_openai/*`，避免 LiteLLM 原生 Responses API 路由 404（`{api_base}/responses` 不存在）
- **vLLM token_ids 轨迹提取**: 从 vLLM 响应中提取 `token_ids` 字段用于轨迹记录，提升轨迹数据完整性
- **vLLM 响应字段补全**: 补全 vLLM 响应可选字段默认值（如 `prompt_tokens_details` 等），确保 C 系列对比测试字段对齐
- **vLLM 扩展参数兼容**: E2E utils 新增 `_wrap_vllm_extensions_for_litellm`，LiteLLM proxy 路径自动将 `chat_template_kwargs`/`documents` 包装到 `extra_body`
- **SSE 事件名推断**: `parse_claude_sse` 和 `parse_responses_sse` 支持无 `event:` 行的 SSE 流（如 LiteLLM 转换），自动从 data JSON 的 `type` 字段推断事件名
- **上游截断检测**: Claude 流式对比检测 `message_start` 有无 `message_stop`，Responses 流式检测 `response.created` 有无 `response.completed`，截断时跳过对比
- **Comparison proxy missing content 容错**: proxy 缺失 message content 时降级为 INFO（转换层差异，可接受），避免误报

### 轨迹查看器增强
- **下载按钮**: 支持选择本地文件夹，按 `run_id` 创建子目录，逐 session 下载 JSON（含进度条）
- **差异对比**: 轨迹分析模态框新增差异对比标签页，分层显示（默认折叠差异消息，点击展开详细内容）
- **DAG 树形布局**: 子节点优先与父同行、多子节点分行（行数=叶子数），连线改为右→左水平锚点，杜绝穿越节点

### Bug 修复
- **tool_call 解析逻辑**: 修复 Hermes 工具解析器在特定场景下的 `tool_calls` 字段缺失问题
- **proxy 内部字段泄露**: 转发流式响应前自动剥离 proxy 内部注入字段（如内部追踪标识），避免污染客户端响应
- **logprobs/token_ids 默认值**: 从 `_ensure_response_defaults` 中移除 `logprobs`/`token_ids` 默认值注入，防止非请求场景意外返回这些字段
- **Claude 对比降级**: Claude 格式对比中转换层差异降级为 INFO 级别，流式追加独立 `usage` chunk 避免字段缺失
- **reasoning/tool_calls 字段**: 对齐 vLLM 解析顺序和 `tool_choice` 分支，修复 Direct Pipeline 流式响应中 `reasoning_content` 和 `tool_calls` 字段缺失
- **重复注册 409**: 修复模型重复注册时未正确返回 409 状态码的问题
- **proxy 缓存场景**: 修复缓存场景测试参数不完整和 reasoning 回传缺失
- **raw_request stream 字段**: 补充 `stream` 字段以完整记录原始请求

### 测试
- **C1xx**: OpenAI 格式 Direct/TITO × plain/tool/reasoning/reasoning+tool + 多轮推理工具（非流式/流式）
- **C2xx**: Claude 格式同构（7 个场景）
- **C3xx**: Responses API Direct/TITO × reasoning+tool + 多轮（4 个场景）
- **E2E 文档拆分**: 测试文档拆分为子系列（e2e-framework / e2e-case-desc / e2e-proxy-p1xx-p2xx / e2e-proxy-p3xx / e2e-proxy-p4xx / e2e-comparison / e2e-performance / e2e-nginx / e2e-archive）

### 文档更新
- **可观测性设计文档**: 新增 `docs/design/observability.md`，分析当前可观测性能力与痛点，提出 Metrics/Tracing/健康检查增强方案
- **E2E 测试文档迁移**: 测试文档从 `docs/` 迁移至 `docs/testing/` 子目录

### 影响范围
- `configs/nginx.conf.example`, `dockers/*/configs/nginx.conf` - Responses API 路由
- `dockers/*/configs/litellm.yaml` - custom_openai provider
- `traj_proxy/proxy_core/pipeline/direct_pipeline.py` - token_ids 提取 + 内部字段剥离
- `traj_proxy/proxy_core/pipeline/token_pipeline.py` - vLLM 解析顺序对齐
- `traj_proxy/proxy_core/parsers/parser_manager.py` - tool_choice 分支修复
- `traj_proxy/proxy_core/builders/openai_builder.py` - 响应字段默认值补全
- `traj_proxy/proxy_core/builders/stream_builder.py` - usage chunk 独立追加
- `tests/e2e/` - E2E 测试体系全面重构
- `tests/e2e/layers/comparison/compare.py` - SSE 解析增强 + 截断检测
- `tests/e2e/layers/comparison/utils.sh` - vLLM 扩展参数兼容
- `docs/design/observability.md` - 可观测性设计文档（新增）
- `docs/testing/` - E2E 测试文档子系列（新增/迁移）
- `scripts/replay_trajectory_viewer.html` - 下载按钮 + 差异对比 + DAG 布局

---

## [0.3.0] - 2026-06-02

**类型**: 重构 + Bug 修复 + 功能增强

### 重构
- **流式解析三阶段状态机**: 引入 `StreamState` 数据类和 `Parser.parse_delta()` 方法，统一流式解析为 reasoning→tool_call→content 三阶段状态机，对齐 vLLM 原生解析流程。状态管理从 `TokenPipeline` 移至 `Parser.parse_delta()`，职责更清晰
- **Hermes 工具解析器重写**: 移除脆弱的 `partial_json_parser` 依赖，状态跟踪改为基于完整 `current_text` 的重新解析。多工具调用的增量变更使用基于差异的逻辑，比旧增量 JSON 解析更可靠。`_extract_tool_calls_streaming` 严格对齐 vLLM 接口（`_build_request` 转换 + 参数透传）
- **Docker 网络隔离重构**: compose 网络重命名为 `traj-proxy-compose-network`，archiver 测试 compose 采用双网络设计（内部 `traj-proxy-archiver-network` + 外部 `traj-proxy-compose-network`），隔离 MinIO 和数据库流量
- **E2E 测试参数化重构**: 模型名、tokenizer 路径、parser 配置统一通过环境变量控制（`DEFAULT_MODEL_NAME`、`DEFAULT_TOKENIZER_PATH`、`DEFAULT_TOOL_PARSER`、`DEFAULT_REASONING_PARSER`），测试场景不再硬编码 Qwen 特定配置
- **E2E 测试改用自然语言 Prompt**: 所有测试场景的 prompt 从强制格式注入（如 `toral<function=...>`）改为自然语言描述，使测试更贴近真实使用场景
- **test(e2e): Comparison 对比层**: 新增第 4 层对比测试（C300-C304），发送相同请求到 vLLM（参考实现）和 trajproxy，递归对比响应字段。覆盖 direct/TITO × plain/tool/reasoning/reasoning+tool 5 种组合，支持非流式和流式两种模式
- **文档目录重构**: 按 design（Why）/api（What）/guide（How）/tools 四象限重组织，新增 `quickstart.md` 和 `consistency-report.md`，清理过时经验文档
- **build_image.sh 通用化**: 支持 `--type compose|allinone` 参数，单一脚本构建两种镜像，移除重复代码

### 新增功能
- **DeepSeekR1ReasoningParser**: 新增基于 `<think>`/`</think>` 标记的推理解析器，兼容 DeepSeek R1 和 Qwen3 系列。对齐 parser 配置（`deepseek_r1`）
- **TestToolParser**: 新增 `qwen3_coder` XML 格式工具解析器（`<function=name>...</function>`），支持非流式提取和增量流式 diff
- **流式 `prompt_token_ids` 支持**: 流式模式支持 vLLM `prompt_token_ids` 顶级字段扩展
- **流式 raw_response 后端元数据**: DirectPipeline 流式模式使用后端真实元数据（`id`、`model`、`created`、`system_fingerprint`）替代合成值
- **前缀缓存诊断增强**: 缓存未命中时输出详细诊断日志，包含候选匹配度和 EOS/think-token 模式检测
- **`/dev/shm` 空间检查**: 容器启动时检查共享内存是否 ≥1.7GB，不足时警告 Ray 性能降级风险并提示 `--shm-size=2g`
- **Docker 启动前自动清理**: `start_docker_compose.sh` 新增 stale network 和运行中容器清理，确保干净部署
- **LiteLLM Prisma 基线迁移标记**: 全新数据库自动标记 baseline migration，修复 LiteLLM 启动失败问题（P3005）
- **E2E 测试加速**: 新增 `CacheInferServer` 缓存推理响应，相同 prompt 命中缓存跳过推理（E2E 耗时大幅降低）。测试场景 sleep 系统性缩减（3s→1s, 2s→0.5s, 1s→0.3s）

### Bug 修复
- **Jinja2 `tojson` 过滤器对齐**: 用 `sort_keys=False` 的 lambda 替换 Jinja2 默认 `tojson` 过滤器，对齐 transformers 库的行为。修复多轮对话模板渲染不一致导致的前缀缓存失效（此为缓存一致性根因）
- **流式推理阶段 content 泄露**: reasoning 阶段强制拦截 `content` 字段输出（防御性修复 × 2），防止推理未完成时内容泄露到客户端
- **`stream_finished` 缩进错误**: `if context.stream_finished: break` 移到 `if chunk:` 块外，修复 parser 过滤掉最后 delta 时流永不终止的 bug
- **TITO 流式推理 `previous_token_ids` 未更新**: `chunk` 被 parser 跳过时 `previous_token_ids` 保持更新，修复后续推理解析错误
- **空白内容规范化为 None**: 空/空白 content 统一规范化为 `None`（`content: null`），与 vLLM 行为一致，避免下游客户端解析异常
- **F108 streaming reasoning+tool 场景 tool_calls 缺失**: 提升 `max_tokens` 至 2048 并支持 `E2E_MAX_TOKENS` 环境变量控制，修复 token 不足导致工具调用被截断
- **Claude reasoning 验证修复**: 修复 `thinking_delta` 和 `reasoning_content` 双格式检测，修复 Qwen3 缓存一致性断言
- **`stream_builder._build_chunk`**: 增量 reasoning 追加时确保不追加多余的 "\\n"
- **`DirectPipeline` logprobs 增量追加**: logprobs 的 `content` 列表改为增量 `extend` 而非替换，修复每次新 chunk 到达时之前 logprobs 被丢弃的 bug
- **TITO 模式参数丢失**: 修复 `max_tokens`/`temperature` 参数在 TITO 模式下未透传的问题
- **Comparison 层修复**: 健康检查稳定性、session 路由适配、stdout 日志污染修复、C301 TITO 模式 3 个问题修复、C302 补全 `reasoning_parser` 参数、流式 delta 字段全局完整性校验
- **E2E 测试修复**: 修复 7 个失败用例，删除不可靠的 A104 定时轮询测试，F214 改用真实 vLLM 推理验证
- **容器日志噪音修复**: allinone 启动时多个日志噪音问题修复（`PYTHONWARNINGS` 抑制 psycopg_pool RuntimeWarning 和 pydantic UserWarning）
- **Supervisor archiver 集成**: 新增 `[program:traj_archiver]`，所有 Supervisor 进程组增加 `PYTHONWARNINGS` 环境变量控制

### 测试
- **F115**: 新增多轮 Reasoning+Tool 非流式测试（3 轮，含精确前缀缓存校验：`cache_hit_tokens == len(full_conversation_token_ids)`），修复 eval 注入风险改用 `printf -v`
- **F116**: 新增多轮 Reasoning+Tool 流式测试（3 轮，流式 SSE chunk 重建 + 精确缓存校验）
- **F106**: 前缀缓存校验从粗粒度 `cache_hit_tokens > 0` 升级为精确验证 `cache_hit_tokens` 和 `token_ids` 数组长度等价性
- **F214**: 从 mock 改为真实 vLLM 推理验证 logprobs/token_ids 强制覆盖 + 返回剥离（非流式 7 步 + 流式 5 步），覆盖不传/传 true/传数值/组合参数等场景
- **F113**: 从 3 轮简化为 2 轮，集成 reasoning 检测逻辑
- **C300-C304**: 新增 comparison 对比层 5 个测试场景（direct/TITO × plain/tool/reasoning/reasoning+tool）
- **F202-F205, F212-F213**: 适配参数化配置，改用自然语言 prompt
- **F216/F218**: LRU 缓存命中/淘汰测试适配单一模型名 + 多 `run_id` 模式
- **F107-F111**: 适配 parser 参数化配置和 Claude 格式回退验证

### 配置更新
- `proxy_workers.max_concurrent_requests`: 256 → 4096（适配高并发场景）
- Docker compose: `traj_proxy` 新增 `host.docker.internal:host-gateway` extra_hosts，`depends_on db` 从 `service_started` 升级为 `service_healthy`
- All-in-One 新增 `dockers/allinone/configs/archiver.yaml`（独立归档配置）
- 新增 `E2E_SAMPLING_PARAMS` 环境变量控制测试确定性采样

### 影响范围
- `traj_proxy/proxy_core/parsers/parser_manager.py` - 三阶段状态机
- `traj_proxy/proxy_core/parsers/vllm_compat/tool_parsers/hermes_tool_parser.py` - 解析器重写
- `traj_proxy/proxy_core/parsers/vllm_compat/reasoning_parsers/deepseek_r1_reasoning_parser.py` - 新增
- `custom_parsers/tool_parsers/test_tool_parser.py` - 新增 XML 格式解析器
- `traj_proxy/proxy_core/pipeline/token_pipeline.py` - 状态机适配 + Bug 修复
- `traj_proxy/proxy_core/pipeline/direct_pipeline.py` - 后端元数据 + logprobs 增量追加
- `traj_proxy/proxy_core/converters/message_converter.py` - Jinja2 tojson 对齐
- `traj_proxy/proxy_core/converters/token_converter.py` - skip_special_tokens 调整
- `traj_proxy/proxy_core/builders/openai_builder.py` - 内容规范化为 None
- `traj_proxy/proxy_core/cache/prefix_cache.py` - 诊断日志增强
- `traj_proxy/proxy_core/infer_client.py` - 参数映射优先级
- `dockers/compose/` - 网络隔离重构
- `dockers/allinone/` - archiver 集成 + /dev/shm 检查
- `scripts/` - 启动脚本增强
- `docs/` - 文档目录重构
- `tests/e2e/` - 参数化重构 + 新测试场景 + comparison 对比层
- `configs/config.yaml` - 并发上限调整

---

## [0.2.5] - 2026-05-28

**类型**: 功能增强

### 新增功能
- **logprobs/return_token_ids 强制覆盖**: 上游推理请求始终注入 `logprobs=1` 和 `return_token_ids=True`（用于数据库存储完整轨迹信息），无论客户端是否传递这两个参数
- **客户端响应自动剥离**: 响应存储到数据库后，自动从返回给客户端的响应中移除 `logprobs` 和 `token_ids` 字段（非流式原地修改，流式返回浅拷贝）

### 优化改进
- **并发上限调整**: `proxy_workers.max_concurrent_requests` 从 256 提升至 4096，适应高并发场景

### 测试
- **F214**: 重写 logprobs/token_ids 测试场景，从「返回过滤」改为验证「强制覆盖 + 返回剥离」双重视角（非流式 7 步 + 流式 5 步，覆盖不传/传 true/传数值/组合参数等场景），移除 TITO 模式测试依赖

### 影响范围
- `traj_proxy/proxy_core/infer_client.py` - 上游请求强制注入 logprobs=1 和 return_token_ids=True
- `traj_proxy/proxy_core/pipeline/direct_pipeline.py` - 响应剥离 logprobs/token_ids（非流式 + 流式）
- `dockers/compose/configs/config.yaml` - max_concurrent_requests 256 → 4096
- `tests/e2e/layers/proxy/scenarios/F214_logprobs_token_ids_filter.sh` - 测试重写

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
