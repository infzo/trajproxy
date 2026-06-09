# TrajProxy 测试用例目录

**文档版本**: v2.0  
**创建日期**: 2026-01-05  
**最后更新**: 2026-06-08  
**适用版本**: 当前主分支

---

## 1. 分类体系

### 1.1 编号规则

编号格式：**字母前缀 + 百位领域码 + 序号**（如 P301、N102）

| 组成部分 | 含义 | 说明 |
|----------|------|------|
| **前缀字母** | 执行层 | 唯一对应一个 layer 目录（N=nginx, P=proxy, A=archive, C=comparison, T=performance） |
| **百位数字** | 功能领域 | 同一领域占一个 xx 范围（1xx=模型管理, 2xx=请求处理, 3xx=轨迹存储, 4xx=多轮缓存） |
| **后两位** | 序号 | 组内连续编号，无跳跃 |

### 1.2 测试系列定义

| 系列 | 类型 | 执行层 | 端口 | 职责 | 数量 |
|------|------|--------|------|------|------|
| **N1xx** | Nginx 层冒烟 | Nginx | 12345 | 验证 nginx 代理层的基础请求与轨迹存储 | 2 |
| **P1xx** | 模型管理 | Proxy | 12300 | 验证模型 CRUD、格式兼容、错误处理、Processor 管理 | 11 |
| **P2xx** | 请求处理 | Proxy | 12300 | 验证参数透传/过滤、自定义 Parser、TITO 冒烟 | 8 |
| **P3xx** | 轨迹存储 | Proxy | 12300 | 验证存储的轨迹字段格式与请求/响应内容一致性 | 9 |
| **P4xx** | 多轮缓存 | Proxy | 12300 | 验证 TokenPipeline 多轮对话的 PrefixMatchCache 命中行为 | 11 |
| **C1xx** | OpenAI 格式一致性 | Comparison | — | 验证 OpenAI 兼容接口与 vLLM 响应的逐字段一致性 | 7 |
| **C2xx** | Claude 格式一致性 | Comparison | — | 验证 Claude 接口适配与 vLLM 响应的逐字段一致性 | 7 |
| **A1xx** | 归档测试 | Archive | — | 独立归档进程的存储与恢复 | 4 |
| **T1xx** | 性能测试 | Performance | — | 长稳、并发、流式压测 | 3 |
| **总计** | | | | | **62** |

### 1.3 矩阵标签说明

**Pipeline 类型**:
- `Direct`: 直接转发模式（token_in_token_out=false），无 Parser 参与
- `TITO`: Token 进出模式（token_in_token_out=true），可有 Parser 配置

**响应模式**:
- `ns`: 非流式（nonstreaming）
- `s`: 流式（streaming）
- `(ns+s)`: 同一用例覆盖两种模式

**Parser 配置**（仅 TITO 模式可用）:
- `无`: 无 Parser
- `Tool`: 配置 Tool Parser（默认 qwen3_coder）
- `Reasoning`: 配置 Reasoning Parser（默认 qwen3）
- `T+R`: 同时配置 Tool + Reasoning Parser（默认 qwen3_coder + qwen3）

**轮次**:
- `单轮`: 单次请求
- `多轮(n轮)`: n 轮连续对话，涉及前缀缓存

**参数变体**（融入多轮或作为多轮用例的子场景）:
- `kwargs`: chat_template_kwargs 变体（preserve_thinking=true 为默认，其他值在多轮中验证）
- `tool_choice`: 工具选择模式（auto/required/named），融入多轮 Tool 用例

**重要默认值**:
- `preserve_thinking=true`（所有用例默认）
- `tool_choice=auto`（无特殊标注时）

### 1.4 来源标注

| 标注 | 含义 |
|------|------|
| `保留` | 现有用例，保持原编号 |
| `迁移` | 从其他系列迁移过来，重新编号 |
| `新增` | 全新补充的用例 |
| `删除` | 冗余移除 |
| `合并` | 与其他用例合并 |
| `降级` | 功能验证降级为冒烟测试 |

---

## 2. N1xx: Nginx 层冒烟

**验证目标**: 验证 nginx 代理层（端口 12345）的基础请求与轨迹存储。

**说明**: nginx 层仅做代理转发，不涉及 Token 编解码逻辑，因此只做轻量冒烟验证，确认请求可达且轨迹存储存在。

| 新编号 | 名称 | 说明 | 原编号(迁移前) | 来源 | 矩阵标签 |
|--------|------|------|----------------|------|----------|
| **N101** | 基础 chat 轨迹冒烟（非流式） | nginx 层非流式请求 → 轨迹字段存在+非空（轻量冒烟） | F203 | 降级 | `Direct×ns×单轮×冒烟` |
| **N102** | 流式 chat 轨迹冒烟 | nginx 层流式请求 → 轨迹存储（轻量冒烟） | F204 | 降级 | `Direct×s×单轮×冒烟` |

---

## 3. P1xx: 模型管理

**验证目标**: 验证模型管理、格式兼容、错误处理、Processor 管理等 Proxy 层基础功能。

**组织顺序**: 模型 CRUD(5) → 错误处理(2) → Processor 管理(4)，编号连续。

### 3.1 模型 CRUD (P101-P105)

| 新编号 | 名称 | 说明 | 原编号(迁移前) | 来源 | 矩阵标签 |
|--------|------|------|----------------|------|----------|
| **P101** | 模型 CRUD | 注册→列表→删除→列表 | F101 | 保留 | `基础API×模型注册` |
| **P102** | PANGU 格式 | `{model_name},{run_id}` 兼容 | F102 | 保留 | `基础API×PANGU兼容` |
| **P103** | 重复注册模型 | 注册同一 (run_id, model_name) → 409 或覆盖 | F103 | 新增 | `基础API×模型管理×幂等` |
| **P104** | 删除预置模型 | DELETE 预置模型 → 拒绝/返回 false | F104 | 新增 | `基础API×模型管理×保护` |
| **P105** | 模型未注册 | 请求未注册模型 → 404 | F105 | 新增 | `基础API×模型管理×查询` |

### 3.2 错误处理 (P106-P107)

| 新编号 | 名称 | 说明 | 原编号(迁移前) | 来源 | 矩阵标签 |
|--------|------|------|----------------|------|----------|
| **P106** | 无效 model 参数 | model="" → 422, model=null → 422 | F106 | 新增 | `基础API×参数校验×422` |
| **P107** | 并发限流 | 超出 max_concurrent_requests → 429 | F107 | 新增 | `系统级×并发控制×429` |

### 3.3 Processor 管理 (P108-P111)

| 新编号 | 名称 | 说明 | 原编号(迁移前) | 来源 | 矩阵标签 |
|--------|------|------|----------------|------|----------|
| **P108** | Processor 懒加载 | 首次请求懒加载 + 缓存命中 | F112 | 保留 | `系统级×Processor×懒加载` |
| **P109** | Processor LRU 命中 | 多模型交替请求 | F113 | 保留 | `系统级×Processor×LRU` |
| **P110** | 预置模型懒加载 | config.yaml 预置模型 | F114 | 保留 | `系统级×Processor×预置` |
| **P111** | LRU 淘汰重加载 | 35模型超32上限 | F115 | 保留 | `系统级×Processor×LRU淘汰` |

---

## 4. P2xx: 请求处理

**验证目标**: 验证参数透传/过滤、自定义 Parser 发现、TITO 基础冒烟等 Proxy 层请求处理功能。

**组织顺序**: 参数透传(4) → 自定义 Parser(2) → TITO 冒烟(2)，编号连续。

### 4.1 参数透传与过滤 (P201-P204)

| 新编号 | 名称 | 说明 | 原编号(迁移前) | 来源 | 矩阵标签 |
|--------|------|------|----------------|------|----------|
| **P201** | 参数透传 (Direct) | Mock 验证 header/参数透传 | F108 | 保留 | `基础API×参数透传×Direct` |
| **P202** | 参数过滤 (TITO) | TITO 模式黑名单过滤 | F109 | 保留 | `基础API×参数过滤×TITO` |
| **P203** | logprobs/token_ids 强制覆盖 | Proxy 强制覆盖 + 客户端过滤 | F110 | 保留 | `基础API×参数强制覆盖×TITO` |
| **P204** | chat_template_kwargs 透传与消费 | Direct 透传 vs TITO 消费 | F111 | 保留 | `基础API×kwargs×Direct/TITO` |

### 4.2 自定义 Parser 发现 (P205-P206)

| 新编号 | 名称 | 说明 | 原编号(迁移前) | 来源 | 矩阵标签 |
|--------|------|------|----------------|------|----------|
| **P205** | 自定义 ToolParser 发现 | 热加载 parser | F116 | 保留 | `系统级×Parser×热加载` |
| **P206** | 自定义 ReasoningParser 发现 | 热加载 parser | F117 | 保留 | `系统级×Parser×热加载` |

### 4.3 TITO 基础冒烟 (P207-P208)

| 新编号 | 名称 | 说明 | 原编号(迁移前) | 来源 | 矩阵标签 |
|--------|------|------|----------------|------|----------|
| **P207** | TITO Token 模式基础（非流式） | TITO 基础请求 + response_ids 验证 | F118 | 保留 | `TITO×ns×单轮×冒烟` |
| **P208** | TITO Token 流式基础 | TITO 流式基础请求 | F119 | 保留 | `TITO×s×单轮×冒烟` |

---

## 5. P3xx: 轨迹存储

**验证目标**: 验证存储的轨迹字段格式正确，且与实际请求/响应内容一致。

**组织顺序**: 基础轨迹(2) → 存储一致性(2) → 特殊模式(2) → 查询API(2) → 交叉验证(1)，编号连续。

### 5.1 基础轨迹字段 (P301-P302)

| 新编号 | 名称 | 说明 | 原编号(迁移前) | 来源 | 矩阵标签 |
|--------|------|------|----------------|------|----------|
| **P301** | 轨迹捕获基础 | 请求后查询 /trajectory 确认记录存在 | F201 | 保留 | `TITO×ns×单轮×轨迹` |
| **P302** | EOS 一致性 | full_conv_text 与 full_conv_token_ids 的 EOS 一致 | F202 | 保留 | `TITO×ns×单轮×EOS校验` |

### 5.2 流式/非流式存储一致性 (P303-P304)

| 新编号 | 名称 | 说明 | 原编号(迁移前) | 来源 | 矩阵标签 |
|--------|------|------|----------------|------|----------|
| **P303** | TITO 流式/非流式轨迹一致性 | 两种模式的存储字段比较 | F205 | 保留 | `TITO×(ns+s)×单轮×存储对比` |
| **P304** | Direct 流式/非流式轨迹一致性 | Direct 模式两种存储比较 | F206 | 保留 | `Direct×(ns+s)×单轮×存储对比` |

### 5.3 特殊模式轨迹 (P305-P306)

| 新编号 | 名称 | 说明 | 原编号(迁移前) | 来源 | 矩阵标签 |
|--------|------|------|----------------|------|----------|
| **P305** | PANGU + 轨迹存储 | PANGU 格式的轨迹完整性 | F207 | 保留 | `PANGU×ns×单轮×轨迹` |
| **P306** | Tool 场景轨迹存储 | tool_calls 和 assistant 含 tool_calls 的完整记录 | F208 | 新增 | `TITO×Tool×单轮×轨迹` |

### 5.4 轨迹查询 API (P307-P308)

| 新编号 | 名称 | 说明 | 原编号(迁移前) | 来源 | 矩阵标签 |
|--------|------|------|----------------|------|----------|
| **P307** | Trajectories API | GET /trajectories?run_id=（合并两层） | F209 | 合并 | `轨迹API×查询` |
| **P308** | trajectory fields 过滤 | ?fields= 包含/排除语法 | F210 | 保留 | `轨迹API×fields过滤` |

### 5.5 轨迹字段交叉验证 (P309)

| 新编号 | 名称 | 说明 | 原编号(迁移前) | 来源 | 矩阵标签 |
|--------|------|------|----------------|------|----------|
| **P309** | 轨迹字段与请求内容交叉验证 | 存储的 messages/token_ids 与实际请求一致 | F211 | 新增 | `TITO×ns×单轮×交叉校验` |

---

## 6. P4xx: 多轮缓存

**验证目标**: 验证 TokenPipeline 多轮对话的 PrefixMatchCache 命中行为。

**组织顺序**: 正常缓存命中(4) → 扩展 Parser 组合(2) → 混合模式(2) → 异常/边界条件(3)，编号连续。

**核心校验公式**:
- **校验1**: `cache_hit_tokens[R_n]` == `len(full_conversation_token_ids[R_{n-1}])`
- **校验2**: `len(full_conversation_token_ids)` == `len(token_ids)` + `len(response_ids)`

### 6.1 正常缓存命中路径 (P401-P404)

| 新编号 | 名称 | 说明 | 原编号(迁移前) | 来源 | 矩阵标签 |
|--------|------|------|----------------|------|----------|
| **P401** | 基线缓存：纯多轮（非流式） | 纯文本 3 轮递增匹配基线 | F301 | 保留 | `TITO×ns×无×3轮×session=有` |
| **P402** | 多轮 Tool 缓存（非流式） | tool_calls 回传后模板渲染匹配 | F302 | 保留 | `TITO×ns×Tool×2轮×session=有` |
| **P403** | 多轮 T+R 非流式缓存 | reasoning+tool 组合回传匹配 | F303 | 保留 | `TITO×ns×T+R×3轮×session=有` |
| **P404** | 多轮 T+R 流式缓存 | 流式推理+工具组合回传匹配 | F304 | 保留 | `TITO×s×T+R×3轮×session=有` |

> **注意**: P403 和 P404 的测试文件名已更新：P403 使用 `cache_tr_3turn_ns`（原 `cache_tr_3turn`），P404 使用 `cache_tr_3turn_stream`（原 `cache_tr_3turn`），以明确区分非流式与流式。

### 6.2 扩展 Parser 组合 (P405-P406)

| 新编号 | 名称 | 说明 | 原编号(迁移前) | 来源 | 矩阵标签 |
|--------|------|------|----------------|------|----------|
| **P405** | 多轮纯 Reasoning 缓存（非流式） | reasoning_content 回传匹配 | F305 | 新增 | `TITO×ns×Reasoning×3轮×session=有` |
| **P406** | 流式存储缓存 | 流式存储的轨迹可被下轮命中 | F306 | 新增 | `TITO×s×无×3轮×session=有` |

### 6.3 混合模式 (P407-P408)

| 新编号 | 名称 | 说明 | 原编号(迁移前) | 来源 | 矩阵标签 |
|--------|------|------|----------------|------|----------|
| **P407** | 流式+非流式交替缓存 | 跨存储模式缓存兼容 | F307 | 新增 | `TITO×交替(s→ns→s)×T+R×3轮×session=有` |
| **P408** | Reasoning+Tool 冒烟 | 从功能验证降级为单轮冒烟（不验缓存），覆盖流式+非流式 | F308 | 降级 | `TITO×(ns+s)×T+R×单轮×冒烟` |

### 6.4 异常/边界条件 (P409-P411)

| 新编号 | 名称 | 说明 | 原编号(迁移前) | 来源 | 矩阵标签 |
|--------|------|------|----------------|------|----------|
| **P409** | 无 session 缓存跳过 | cache_hit_tokens 恒为 0 | F309 | 新增 | `TITO×ns×Tool×2轮×session=无` |
| **P410** | kwargs 变化缓存失效 | kwargs 改变→缓存不匹配 | F310 | 新增 | `TITO×ns×T+R×2轮×session=有×kwargs=thinking=false变` |
| **P411** | tools 变更缓存失效 | tools 定义改变→缓存不匹配 | F311 | 新增 | `TITO×ns×Tool×2轮×session=有×tools=不同` |

---

## 7. C1xx: OpenAI 格式一致性

**验证目标**: 使用 `compare.py` 对比 trajproxy 与 vLLM 的 OpenAI 兼容接口响应，确保逐字段一致性、禁止特殊 token 泄漏。

**矩阵维度**: Pipeline × Parser × 轮次（Direct 无 Parser，TITO 按 Parser 组合展开；所有用例统一 `(ns+s)` 覆盖两种模式）

**设计原则**:
- 单轮用例（C101-C105）验证各 Parser 组合的基础格式一致性
- 多轮用例（C106-C107）同时验证多轮格式一致性 + 前缀缓存递增，**内嵌参数变体**（kwargs、tool_choice）作为多轮场景的子验证
- `preserve_thinking=true` 作为全局默认值，不再单列反向用例

| 新编号 | 名称 | 说明 | 原编号(迁移前) | 来源 | 矩阵标签 |
|--------|------|------|----------------|------|----------|
| **C101** | Direct T+R Parser 一致性 | Direct 模式下 Reasoning+Tool Parser 全字段对比 | 原C300 | 保留→升级 | `Direct×(ns+s)×T+R×单轮×OpenAI` |
| **C102** | TITO 纯文本一致性 | TITO 基线：Token 编解码正确性 | 原C301 | 保留 | `TITO×(ns+s)×无×单轮×OpenAI` |
| **C103** | TITO Tool 一致性 | tool_calls 结构 + arguments 禁止 token | 原C302 | 保留 | `TITO×(ns+s)×Tool×单轮×OpenAI`（默认 auto） |
| **C104** | TITO Reasoning 一致性 | reasoning 字段一致性 + content 剥离 `</think>` | 原C303 | 保留 | `TITO×(ns+s)×Reasoning×单轮×OpenAI`（默认 preserve=true） |
| **C105** | TITO Reasoning+Tool 一致性 | 全字段：reasoning + tool_calls 逐项对比 | 原C304 | 保留 | `TITO×(ns+s)×T+R×单轮×OpenAI`（默认 preserve=true, auto） |
| **C106** | 多轮 T+R 非流式一致性 | 每轮对比 vLLM；同时验证缓存递增。子场景：(a) tool_choice=required, (b) tool_choice=named, (c) enable_thinking=false | — | 新增 | `TITO×ns×T+R×3轮×OpenAI`（含 kwargs/tool_choice 变体子场景） |
| **C107** | 多轮 T+R 流式一致性 | 每轮流式重建后对比；同时验证流式缓存递增。子场景同 C106 | — | 新增 | `TITO×s×T+R×3轮×OpenAI`（含 kwargs/tool_choice 变体子场景） |

---

## 8. C2xx: Claude 格式一致性

**验证目标**: 验证 Claude `/v1/messages` 端点的 LiteLLM 适配正确性，使用 `compare.py` 对比一致性。

**对等原则**: C2 与 C1 在核心维度（Pipeline × Parser × 轮次）保持严格对等。Claude 不支持 `tool_choice` 的 required/named 变体，因此 C2 多轮用例不包含 kwargs/tool_choice 子场景（由 C1 多轮覆盖）。

| 新编号 | 名称 | 说明 | 原编号(迁移前) | 来源 | 矩阵标签 |
|--------|------|------|----------------|------|----------|
| **C201** | Claude Direct T+R Parser 一致性 | Direct 模式下 Reasoning+Tool Parser 全字段对比（对等 C101） | 原F101/F109 | 迁移+升级→升级 | `Direct×(ns+s)×T+R×单轮×Claude` |
| **C202** | Claude TITO 纯文本一致性 | 对等 C102 | — | 新增 | `TITO×(ns+s)×无×单轮×Claude` |
| **C203** | Claude TITO Tool 一致性 | 对等 C103 | — | 新增 | `TITO×(ns+s)×Tool×单轮×Claude` |
| **C204** | Claude TITO Reasoning 一致性 | 对等 C104 | — | 新增 | `TITO×(ns+s)×Reasoning×单轮×Claude` |
| **C205** | Claude TITO Reasoning+Tool 一致性 | 合并原 F110(ns)+F111(s)，升级为 compare.py 对比。对等 C105 | 原F110/F111 | 迁移+升级 | `TITO×(ns+s)×T+R×单轮×Claude` |
| **C206** | Claude 多轮 T+R 非流式一致性 | 对等 C106（不含 kwargs/tool_choice 子场景） | — | 新增 | `TITO×ns×T+R×3轮×Claude` |
| **C207** | Claude 多轮 T+R 流式一致性 | 对等 C107（不含 kwargs/tool_choice 子场景） | — | 新增 | `TITO×s×T+R×3轮×Claude` |

---

## 9. A1xx: 归档测试

**验证目标**: 独立归档进程的存储与恢复（保持不变）。

| 新编号 | 名称 | 说明 | 原编号(迁移前) | 来源 | 矩阵标签 |
|--------|------|------|----------------|------|----------|
| **A100** | 归档进程配置 | 验证独立归档进程正确启动 | A100 | 保留 | `归档×配置验证` |
| **A101** | 手动归档 | 按 run_id 粒度归档 | A101 | 保留 | `归档×手动触发×run_id粒度` |
| **A102** | 定时归档 | 验证 run 内有活跃记录时保留 | A102 | 保留 | `归档×定时×未过期保留` |
| **A103** | 归档数据恢复 | 验证归档文件可读、JSONL 格式正确 | A103 | 保留 | `归档×数据恢复×完整性` |

---

## 10. T1xx: 性能测试

**验证目标**: 长稳、并发、流式压测。

| 新编号 | 名称 | 说明 | 原编号(迁移前) | 来源 | 矩阵标签 |
|--------|------|------|----------------|------|----------|
| **T101** | 长稳测试 | 持续发送 100 个请求 | P100 | 迁移 | `性能×100请求×延迟统计` |
| **T102** | 并发测试 | 维持 10 个并发发送 100 个请求 | P101 | 迁移 | `性能×10并发×100请求` |
| **T103** | 流式并发压测 | 100 并发流式请求 | P102 | 迁移 | `性能×100并发×流式` |

---

## 11. 删除与合并清单

| 原编号 | 处理方式 | 去向 | 原因 |
|--------|---------|------|------|
| **原F100** | 降级 | **N101** | 功能验证被 C101 覆盖，降级为轨迹冒烟 |
| **原F102** | 降级 | **N102** | 功能验证被 C101 流式覆盖，降级为轨迹冒烟 |
| **原F101** | 合并+迁移 | C201 | 与 F109 合并为 Claude Direct 一致性对比 |
| **原F109** | 合并+迁移 | C201 | 与 F101 合并为 Claude Direct 一致性对比 |
| **原F110** | 合并+迁移 | C205 | 与 F111 合并为 Claude TITO T+R 一致性对比 |
| **原F111** | 合并+迁移 | C205 | 与 F110 合并为 Claude TITO T+R 一致性对比 |
| **原F107** | 降级 | **P408** | 与 F108 合并，降级为单轮冒烟 |
| **原F108** | **删除** | 并入 P408 | 流式部分并入 P408，与 F107 合并 |
| **原F112** | 合并 | **P307** | 与 F209 合并为统一的 Trajectories API 测试 |
| **原F209** | 合并 | **P307** | 与 F112 合并 |
| **原F202** | **删除** | C103 覆盖 | Proxy层 Tool 单轮功能被 C103 完全覆盖 |
| **原F203** | **删除** | C104 覆盖 | Proxy层 Reasoning 单轮功能被 C104 完全覆盖 |
| **原F204** | **删除** | C103 流式覆盖 | Proxy层 Tool 流式功能被 C103 流式部分完全覆盖 |
| **原F205** | **删除** | C104 流式覆盖 | Proxy层 Reasoning 流式功能被 C104 流式部分完全覆盖 |

> 注: 除上述处理外，F1xx/F2xx/F3xx 内部的编号在 v1.1-v1.3 中**全部重排**以保证连续无跳跃，v2.0 进一步按执行层+功能领域重新编号，原编号保留在表格"原编号(迁移前)"列供追溯。

---

## 12. 编号迁移对照表（v1.x → v2.0）

以下为完整的旧编号 → 新编号映射，供自动化迁移脚本使用。

### N 系列（Nginx 层）

| v1.x 编号 | v2.0 编号 | 名称 | 文件名(slug) |
|-----------|-----------|------|-------------|
| F203 | N101 | 基础 chat 轨迹冒烟（非流式） | basic_chat_trajectory_ns |
| F204 | N102 | 流式 chat 轨迹冒烟 | stream_chat_trajectory |

### P1xx（模型管理）

| v1.x 编号 | v2.0 编号 | 名称 | 文件名(slug) |
|-----------|-----------|------|-------------|
| F101 | P101 | 模型 CRUD | model_crud |
| F102 | P102 | PANGU 格式 | pangu_format |
| F103 | P103 | 重复注册模型 | duplicate_register |
| F104 | P104 | 删除预置模型 | delete_preset_model |
| F105 | P105 | 模型未注册 | model_not_registered |
| F106 | P106 | 无效 model 参数 | invalid_model_param |
| F107 | P107 | 并发限流 | concurrent_limit |
| F112 | P108 | Processor 懒加载 | processor_lazy_load |
| F113 | P109 | Processor LRU 命中 | lru_cache_hit |
| F114 | P110 | 预置模型懒加载 | preset_model_lazy_load |
| F115 | P111 | LRU 淘汰重加载 | lru_eviction |

### P2xx（请求处理）

| v1.x 编号 | v2.0 编号 | 名称 | 文件名(slug) |
|-----------|-----------|------|-------------|
| F108 | P201 | 参数透传 (Direct) | params_passthrough_direct |
| F109 | P202 | 参数过滤 (TITO) | params_filter_tito |
| F110 | P203 | logprobs/token_ids 强制覆盖 | logprobs_force_override |
| F111 | P204 | chat_template_kwargs 透传与消费 | chat_template_kwargs |
| F116 | P205 | 自定义 ToolParser 发现 | custom_tool_parser |
| F117 | P206 | 自定义 ReasoningParser 发现 | custom_reasoning_parser |
| F118 | P207 | TITO Token 模式基础（非流式） | tito_token_ns |
| F119 | P208 | TITO Token 流式基础 | tito_token_s |

### P3xx（轨迹存储）

| v1.x 编号 | v2.0 编号 | 名称 | 文件名(slug) |
|-----------|-----------|------|-------------|
| F201 | P301 | 轨迹捕获基础 | trajectory_basic |
| F202 | P302 | EOS 一致性 | eos_consistency |
| F205 | P303 | TITO 流式/非流式轨迹一致性 | tito_stream_ns_trajectory |
| F206 | P304 | Direct 流式/非流式轨迹一致性 | direct_stream_ns_consistency |
| F207 | P305 | PANGU + 轨迹存储 | pangu_trajectory |
| F208 | P306 | Tool 场景轨迹存储 | tito_tool_trajectory |
| F209 | P307 | Trajectories API | trajectories_api |
| F210 | P308 | trajectory fields 过滤 | trajectory_fields |
| F211 | P309 | 轨迹字段与请求内容交叉验证 | trajectory_field_crosscheck |

### P4xx（多轮缓存）

| v1.x 编号 | v2.0 编号 | 名称 | 文件名(slug) |
|-----------|-----------|------|-------------|
| F301 | P401 | 基线缓存：纯多轮（非流式） | cache_ns_3turn |
| F302 | P402 | 多轮 Tool 缓存（非流式） | cache_ns_tool_2turn |
| F303 | P403 | 多轮 T+R 非流式缓存 | cache_tr_3turn_ns |
| F304 | P404 | 多轮 T+R 流式缓存 | cache_tr_3turn_stream |
| F305 | P405 | 多轮纯 Reasoning 缓存（非流式） | cache_ns_reasoning_3turn |
| F306 | P406 | 流式存储缓存 | cache_s_3turn |
| F307 | P407 | 流式+非流式交替缓存 | cache_mixed_3turn |
| F308 | P408 | Reasoning+Tool 冒烟 | cache_smoke_ns_s |
| F309 | P409 | 无 session 缓存跳过 | cache_nosession_2turn |
| F310 | P410 | kwargs 变化缓存失效 | cache_nothink_2turn |
| F311 | P411 | tools 变更缓存失效 | cache_diff_tools_2turn |

> **注意**: P403 和 P404 的 slug 与 v1.x 不同：v1.x 的 `F303/F304` 共用 `cache_tr_3turn`，v2.0 分拆为 `cache_tr_3turn_ns` 和 `cache_tr_3turn_stream`。

### T 系列（性能，原 P 前缀）

| v1.x 编号 | v2.0 编号 | 名称 | 文件名(slug) |
|-----------|-----------|------|-------------|
| P100 | T101 | 长稳测试 | stability |
| P101 | T102 | 并发测试 | concurrent |
| P102 | T103 | 流式并发压测 | streaming_concurrent |

### C 和 A 系列 — 无变化

C101-C107、C201-C207、A100-A103 编号与内容均不变，无需迁移。

---

## 13. 矩阵覆盖分析

### 13.1 C1/C2 对等矩阵

| 核心维度 | C1 (OpenAI) | C2 (Claude) | 对等? |
|------|------------|------------|-------|
| Direct T+R（ns+s） | C101 ✅ | C201 ✅ | ✅ |
| TITO 纯文本（ns+s） | C102 ✅ | C202 ✅ | ✅ |
| TITO Tool（ns+s） | C103 ✅ | C203 ✅ | ✅ |
| TITO Reasoning（ns+s） | C104 ✅ | C204 ✅ | ✅ |
| TITO T+R（ns+s） | C105 ✅ | C205 ✅ | ✅ |
| TITO 多轮 T+R 非流式 | C106 ✅ | C206 ✅ | ✅ |
| TITO 多轮 T+R 流式 | C107 ✅ | C207 ✅ | ✅ |

**C1 多轮独有子场景**（Claude 不支持，不算破坏对等）:
- tool_choice=required 强制调用（qwen3_coder 专属路径） → C106-a
- tool_choice=named 指定工具（qwen3_xml 专属路径） → C106-b
- enable_thinking=false 推理关闭（qwen3 专属 kwargs） → C106-c

### 13.2 P3xx 轨迹存储矩阵覆盖

| 维度 | Pipeline | 流式 | Parser | 存储一致性 | API验证 | 覆盖 |
|------|---------|------|--------|----------|--------|------|
| P301 | TITO | ns | 无 | — | — | ✅ |
| P302 | TITO | ns | 无 | EOS 校验 | — | ✅ |
| P303 | TITO | (ns+s)对比 | 无 | ✅ 跨模式 | — | ✅ |
| P304 | Direct | (ns+s)对比 | 无 | ✅ 跨模式 | — | ✅ |
| P305 | PANGU | ns | 无 | — | — | ✅ |
| P306 | TITO | ns | Tool | — | — | 🆕 |
| P307 | — | — | — | — | GET /trajectories | ✅ |
| P308 | — | — | — | — | fields= 过滤 | ✅ |
| P309 | TITO | ns | 无 | 字段交叉验证 | — | 🆕 |

**P3xx 覆盖率**: 9/9 = 100%

### 13.3 P4xx 缓存矩阵覆盖

| 编号 | 流式 | Parser | 轮次 | session | kwargs变体 | tools变更 | 覆盖 |
|------|------|--------|------|---------|-----------|----------|------|
| P401 | ns | 无 | 3轮 | 有 | — | — | ✅ |
| P402 | ns | Tool | 2轮 | 有 | — | 同tools | ✅ |
| P403 | ns | T+R | 3轮 | 有 | — | 同tools | ✅ |
| P404 | s | T+R | 3轮 | 有 | — | 同tools | ✅ |
| P405 | ns | Reasoning | 3轮 | 有 | — | — | 🆕 |
| P406 | s | 无 | 3轮 | 有 | — | — | 🆕 |
| P407 | 混合(s→ns→s) | T+R | 3轮 | 有 | — | 同tools | 🆕 |
| P408 | **(ns+s)** | T+R | 单轮 | 有 | — | — | ✅ (降级冒烟) |
| P409 | ns | Tool | 2轮 | **无** | — | — | 🆕 |
| P410 | ns | T+R | 2轮 | 有 | **thinking=false 变** | — | 🆕 |
| P411 | ns | Tool | 2轮 | 有 | — | **tools 不同** | 🆕 |
| C106/7 | ns/s | T+R | 3轮 | 有 | **required/named/thinking=false** | 同tools | 🆕 |

**P4xx+C1xx 多轮覆盖率**: 11/11 = 100%

### 13.4 P1xx/P2xx 基础API 覆盖

| 子组 | 编号范围 | 数量 | 覆盖状态 |
|------|---------|------|---------|
| 模型管理 | P101-P107 | 7 | ✅ 含 CRUD + 重复注册 + 删除预置 + 未注册 + 参数校验(422) + 限流(429) |
| Processor 管理 | P108-P111 | 4 | ✅ 懒加载 + LRU + 预置 + 淘汰 |
| 参数透传 | P201-P204 | 4 | ✅ Direct透传 + TITO过滤 + 强制覆盖 + kwargs |
| 自定义Parser | P205-P206 | 2 | ✅ Tool + Reasoning 热加载 |
| TITO 冒烟 | P207-P208 | 2 | ✅ ns + s 基础请求 |

---

## 14. 实施建议

### 14.1 优先级排序

**Phase 1: 编号迁移（高优先级，1 天）**
1. 执行旧编号 → 新编号的全量替换（文件名、测试代码引用、文档引用）
2. P403/P404 文件名分拆：`cache_tr_3turn` → `cache_tr_3turn_ns` / `cache_tr_3turn_stream`

**Phase 2: 基础迁移（高优先级，1-2 天）**
1. 迁移升级 C201（合并原 F101+F109）→ Claude Direct 对比
2. 迁移升级 C205（合并原 F110+F111）→ Claude TITO T+R 对比
3. 新增 C202/C203/C204（补齐 Claude TITO 基础）

**Phase 3: 多轮核心（高优先级，2-3 天）**
1. 新增 C106/C107（OpenAI 多轮 T+R 一致性 + 缓存）
2. 新增 C206/C207（Claude 多轮 T+R 一致性）
3. 新增 P406/P407/P409（缓存关键场景）

**Phase 4: 异常路径 + 系统加固（中优先级，2-3 天）**
1. 新增 P408/P410/P411（缓存异常路径）
2. 新增 P103-P107（基础API错误处理）
3. 新增 P309（轨迹存储交叉验证）

### 14.2 资源估算

- **新增用例**: 18 个（较原方案减少 6 个）
- **迁移升级**: 4 个
- **降级**: 3 个 (原F100/F102/F107)
- **合并**: 3 个 (原F101+F109→C201, 原F110+F111→C205, 原F112+F209→P307)
- **删除**: 5 个 (原F108/原F202/原F203/原F204/原F205)
- **编号迁移**: 62 个（全量编号替换）

预计工作量：
- Phase 1: 1 天
- Phase 2: 1-2 天
- Phase 3: 2-3 天
- Phase 4: 2-3 天

---

## 15. 变更记录

| 版本 | 日期 | 变更内容 |
|------|------|---------|
| v1.0 | 2026-01-05 | 初始版本，基于 48 个现有用例重构为 68 个分类用例 |
| v1.1 | 2026-01-05 | 修订 C1/C2 系列：移除伪对称（Direct 不展开 Parser 维度）；`preserve_thinking=true` 作为全局默认不再单列反向用例；`tool_choice` 变体融入多轮用例作为子场景；删除冗余的 C112；总数 68→62 |
| v1.2 | 2026-01-05 | **F 系列重排序**：按子组归类 + 组内由简到繁，编号连续无跳跃。F1xx 重分为 6 个子组；F2xx 重分为 5 个子组；F3xx 重分为 4 个子组。新增 §10.2 F2xx 覆盖分析表 |
| v1.3 | 2026-01-05 | **审计原用例去向**：补全 5 个遗漏删除项；F308 升级为 `(ns+s)`；新增 §9.A 完整的 48 个原用例追溯表 |
| **v2.0** | **2026-06-08** | **编号体系重构**：前缀字母 = 执行层（N/P/A/C/T），百位数字 = 功能领域；F203/F204 → N101/N102（Nginx层）；F1xx → P1xx/P2xx（Proxy层拆分模型管理+请求处理）；F2xx → P3xx（轨迹存储）；F3xx → P4xx（多轮缓存）；P100-P102 → T101-T103（性能）；P403/P404 文件名分拆。总数 62→62（编号体系变更，用例数量不变） |