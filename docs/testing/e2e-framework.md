# E2E 测试框架概述

> 返回主文档：[E2E 测试用例总览](e2e-case-desc.md)

## 测试框架概述

### 1.1 测试层架构

| 层 | 端口 | 场景数 | 说明 |
|---|---|---|---|
| **Nginx 入口层** | 12345 | 2 | 经 Nginx 入口的基本 chat/轨迹冒烟测试 |
| **Proxy 直连层** | 12300 | 39 | 模型 CRUD、参数透传、轨迹捕获、缓存命中、解析器等 |
| **归档调度层** | — | 4 | 归档配置、手动/定时归档、归档恢复 |
| **对比测试层** | 12300/12345 | 14 | vLLM 直连 vs Proxy 响应对比（OpenAI + Claude 格式；12300 模型注册，12345 请求入口） |
| **性能测试层** | 12345 | 3 | 稳定性、并发、流式并发 |

### 1.2 场景命名规范

| 前缀 | 含义 |
|---|---|
| `N1xx` | Nginx 入口层测试 |
| `P1xx` | 模型管理测试 |
| `P2xx` | 请求处理测试 |
| `P3xx` | 轨迹与数据一致性测试 |
| `P4xx` | 缓存命中验证测试 |
| `A1xx` | 归档测试 |
| `C1xx` | OpenAI 格式对比测试 |
| `C2xx` | Claude 格式对比测试 |
| `T1xx` | 性能测试 |

### 1.3 通用断言工具

| 函数 | 作用 |
|---|---|
| `assert_eq` | 相等性断言 |
| `assert_contains` / `assert_not_contains` | 包含/不包含断言 |
| `assert_http_status` | HTTP 状态码断言 |
| `json_get` / `json_get_number` / `json_get_bool` | JSON 字段提取 |
| `verify_cache_incremental(session_id, expected_rounds, mode)` | 缓存递增验证（4 种模式：`incremental`/`nosession`/`kwargs_change`/`tools_change`） |

### 1.4 缓存递增验证规则

- **校验1**: `cache_hit_tokens[R_n] == len(full_conversation_token_ids[R_{n-1}])`（递增模式）
- **校验2**: `len(full_conversation_token_ids) == len(token_ids) + len(response_ids)` 

### 1.5 全局配置（各层共享）

以下参数定义在 `tests/e2e/config.sh` 中，所有测试层统一继承。

| 配置项 | 默认值 | 环境变量覆盖 | 说明 |
|---|---|---|---|
| `DEFAULT_MODEL_NAME` | `Qwen3.6-27B` | `DEFAULT_MODEL_NAME` | 测试用推理模型名 |
| `DEFAULT_TOKENIZER_PATH` | `Qwen/Qwen3.6-27B` | `DEFAULT_TOKENIZER_PATH` | Tokenizer 路径（TITO 模式使用） |
| `DEFAULT_TOOL_PARSER` | `qwen3_coder` | `DEFAULT_TOOL_PARSER` | 默认 tool 解析器 |
| `DEFAULT_REASONING_PARSER` | `qwen3` | `DEFAULT_REASONING_PARSER` | 默认 reasoning 解析器 |
| `CHAT_API_KEY` | `sk-1234` | — | 推理请求认证 Key |
| `BACKEND_MODEL_URL` | `http://host.docker.internal:8080/v1` | `BACKEND_MODEL_URL` | 后端 vLLM 推理服务地址 |
| 采样参数 | `temperature: 0.0, seed: 42` | — | 确定性采样，消除推理随机性 |

### 1.6 服务地址配置（按层覆盖）

`BASE_URL` **不是全局统一值**，而是每个测试层根据自身目标各自覆盖。所有层先 `source` 全局 `config.sh`（默认 `BASE_URL` 指向 `:12300`），然后按层重写。

| 层 | 配置文件 | `BASE_URL` 实际值 | 环境变量 | 目标服务 |
|---|---|---|---|---|
| **全局默认** | `tests/e2e/config.sh` | `http://127.0.0.1:12300` | `TRAJ_PROXY_URL` | trajproxy 直连 |
| **Proxy 层** | `layers/proxy/config.sh` | `http://127.0.0.1:12300` | `TRAJ_PROXY_URL` | trajproxy 直连 |
| **Nginx 层** | `layers/nginx/config.sh` | `http://127.0.0.1:12345` | `NGINX_URL` | Nginx 入口 → LiteLLM → trajproxy |
| **Performance 层** | `layers/performance/config.sh` | `http://127.0.0.1:12345` | `NGINX_URL` | Nginx 入口 |
| **归档层** | `layers/archive/config.sh` | — （直连 DB，不使用 BASE_URL） | — | PostgreSQL + Docker |
| **对比层** | `layers/comparison/config.sh` | — （不使用 BASE_URL，定义独立变量） | — | 见下表 |

**对比层独立服务地址**（`layers/comparison/config.sh`）：

| 变量 | 默认值 | 环境变量 | 用途 |
|---|---|---|---|
| `PROXY_URL` | `http://127.0.0.1:12300` | `TRAJ_PROXY_URL` | C1xx OpenAI 格式对比：模型注册用 |
| `NGINX_URL` | `http://127.0.0.1:12345` | `NGINX_URL` | C1xx/C2xx Proxy 请求目标 |
| `VLLM_URL` | `http://127.0.0.1:8080` | `VLLM_URL` | vLLM 直连基线（两者共用） |

**调用链路**：

```
Nginx(:12345) → LiteLLM → trajproxy(:12300) → vLLM(:8080)     # Nginx/Performance 层
trajproxy(:12300) → vLLM(:8080)                                 # Proxy 直连层
vLLM(:8080)                                                     # 对比层基线
```

### 1.7 Mock 推理服务端口

部分 Proxy 层测试（P201-P202 等）需要启动 Mock 推理服务器（`mock_infer_server.py`）拦截请求参数进行验证，而非连接真实 vLLM 服务。端口分配规则：

| 场景 | 端口 | 用途 |
|---|---|---|
| **P201** 参数透传 | 19990 | Mock vLLM，验证 Direct 模式参数透传 |
| **P202** 参数过滤 | 19991 | Mock vLLM，验证 TITO 模式参数过滤 |

> **新增场景时**，按顺序递增端口号（如 19992、19993...），避免与已有 Mock 冲突。