# 性能测试层

> 返回主文档：[E2E 测试用例总览](e2e-case-desc.md)

> 本文档覆盖性能测试层的 3 个场景（T1xx）：长稳、并发、流式并发压测。

---

## 概览

| 层 | 端口 | 场景数 | 说明 |
|---|---|---|---|
| **性能测试层** | 12345 | 3 | 稳定性、并发、流式并发 |

### 调用链路

```
Nginx(:12345) → LiteLLM → trajproxy(:12300) → vLLM(:8080)
```

性能层通过 `layers/performance/config.sh` 将 `BASE_URL` 重写为 `NGINX_URL`（端口 12345），请求经 Nginx 入口转发至 trajproxy，再路由到后端 vLLM 推理服务。

### 全局配置

以下参数由 `tests/e2e/config.sh` 定义，性能层继承后按需覆盖。

| 配置项 | 默认值 | 环境变量覆盖 | 说明 |
|---|---|---|---|
| `BASE_URL` | `http://127.0.0.1:12345` | `NGINX_URL` | 性能层重写为 Nginx 入口地址 |
| `DEFAULT_MODEL_NAME` | `Qwen3.6-27B` | `DEFAULT_MODEL_NAME` | 测试用推理模型名 |
| `BACKEND_MODEL_URL` | `http://host.docker.internal:8080/v1` | `BACKEND_MODEL_URL` | 后端 vLLM 推理服务地址 |
| `CHAT_API_KEY` | `sk-1234` | — | 推理请求认证 Key |
| `TEST_MODEL_API_KEY` | `test-api-key-12345` | — | 模型注册时使用的 API Key |

### 场景级参数

| 场景 | 总请求数 | 最大并发 | 流式 | 独有参数 |
|---|---|---|---|---|
| T101 | `STABILITY_TEST_REQUEST_COUNT=100` | 1（串行） | `false` | — |
| T102 | `CONCURRENT_TEST_TOTAL_REQUESTS=100` | `CONCURRENT_TEST_MAX_CONCURRENT=10` | `false` | — |
| T103 | `CONCURRENT_TEST_TOTAL_REQUESTS=100` | `CONCURRENT_TEST_MAX_CONCURRENT=100` | `true` | FD 限制检查（`并发数 × 2 + 200`） |

> **注意**: 三个场景均注册 **Direct 模式**模型（`token_in_token_out=false`），不涉及 Token 编解码逻辑。

---

## T101 — 长稳测试

**目的**: 验证系统在持续串行请求下的稳定性和延迟统计。

**前置条件**: Nginx 服务在端口 12345 上运行；vLLM 后端服务可用。

**参数**:

| 参数 | 值 | 说明 |
|---|---|---|
| `STABILITY_TEST_REQUEST_COUNT` | 100 | 串行发送的总请求数 |
| `stream` | `false` | 非流式请求 |
| 并发模型 | 串行（1 个请求完成后再发下一个） | 无并发控制 |
| `STABILITY_TEST_RUN_ID` | `run-t101` | 注册模型的 run_id |
| `STABILITY_TEST_SESSION_ID` | `session-t101-{8位随机哈希}` | 随机生成的 session_id |

| 步骤 | 操作 | 接口 |
|---|---|---|
| 1 | 清理残留模型（忽略错误） | `DELETE /models?model_name={model}&run_id={run_id}` |
| 2 | 注册模型（`run_id=run-t101`, `token_in_token_out=false`） | `POST /models/register` |
| 3 | 串行发送 100 个非流式 Chat 请求（`"Hello {i}"`） | `POST /s/{run_id}/{session_id}/v1/chat/completions` |
| 4 | 删除模型 | `DELETE /models?model_name={model}&run_id={run_id}` |
| 5 | 计算并输出性能统计数据 | — |

**验收标准**:

| # | 断言 | 期望值 |
|---|---|---|
| 1 | 注册 HTTP 状态 | 200 |
| 2 | 注册 `status` | `"success"` |
| 3 | 全部 100 个请求 HTTP 状态 | 200 |
| 4 | 成功率 | 100% |
| 5 | 删除 HTTP 状态 | 200 |
| 6 | 删除 `status` | `"success"` |

**性能指标输出**（脚本统计但不做硬阈值断言）:

| 指标 | 说明 |
|---|---|
| 总请求数 | 100 |
| 成功次数 / 失败次数 | 计数 |
| 成功率 | 百分比 |
| 平均响应时间 | `TOTAL_RESPONSE_TIME / TOTAL_REQUESTS`（ms） |
| 最小响应时间 | 所有请求中的最小值（ms） |
| 最大响应时间 | 所有请求中的最大值（ms） |

> **注意**: 脚本未计算 P95/P99 延迟，亦未硬编码延迟阈值。延迟合理性需根据实际运行环境人工判定。

---

## T102 — 并发测试

**目的**: 验证系统在 10 并发下处理 100 个请求的吞吐能力和稳定性。

**前置条件**: Nginx 服务在端口 12345 上运行；vLLM 后端服务可用。

**参数**:

| 参数 | 值 | 说明 |
|---|---|---|
| `CONCURRENT_TEST_TOTAL_REQUESTS` | 100 | 总请求数 |
| `CONCURRENT_TEST_MAX_CONCURRENT` | 10 | 最大并发数 |
| `stream` | `false` | 非流式请求 |
| `CONCURRENT_TEST_RUN_ID` | `run-t102` | 注册模型的 run_id |
| `CONCURRENT_TEST_SESSION_ID` | `session-t102-{8位随机哈希}` | 随机生成的 session_id |

| 步骤 | 操作 | 接口 |
|---|---|---|
| 1 | 注册模型（`run_id=run-t102`, `token_in_token_out=false`） | `POST /models/register` |
| 2 | 并发发送 100 个非流式 Chat 请求（维持最多 10 个并发；任一完成立即补充新请求） | `POST /s/{run_id}/{session_id}/v1/chat/completions` |
| 3 | 等待所有请求完成（`wait`） | — |
| 4 | 删除模型 | `DELETE /models?model_name={model}&run_id={run_id}` |
| 5 | 读取所有子进程结果文件并汇总统计 | — |

**验收标准**:

| # | 断言 | 期望值 |
|---|---|---|
| 1 | 注册 HTTP 状态 | 200 |
| 2 | 注册 `status` | `"success"` |
| 3 | 全部 100 个请求 HTTP 状态 | 200 |
| 4 | 成功率 | 100% |
| 5 | 删除 HTTP 状态 | 200 |
| 6 | 删除 `status` | `"success"` |
| 7 | 无连接错误 | 0 个非 200 响应 |

**性能指标输出**（脚本统计但不做硬阈值断言）:

| 指标 | 说明 |
|---|---|
| 总请求数 | 100 |
| 成功次数 / 失败次数 | 计数 |
| 成功率 | 百分比 |
| 最大并发数 | 10 |
| 平均响应时间 | `TOTAL_RESPONSE_TIME / TOTAL_REQUESTS`（ms） |
| 最小响应时间 | 所有请求中的最小值（ms） |
| 最大响应时间 | 所有请求中的最大值（ms） |

**并发控制机制**: 脚本使用后台子进程（`send_request &`）模拟并发；主进程轮询存活 PID，当运行数 < 最大并发数时立即补充新请求，实现滑动窗口式并发控制。

---

## T103 — 流式并发压测

**目的**: 验证系统在 100 并发流式请求下的稳定性、SSE 完整性和吞吐能力。

**前置条件**: Nginx 服务在端口 12345 上运行；vLLM 后端服务可用；系统文件描述符限制满足最低要求。

**参数**:

| 参数 | 值 | 说明 |
|---|---|---|
| `CONCURRENT_TEST_TOTAL_REQUESTS` | 100 | 总请求数 |
| `CONCURRENT_TEST_MAX_CONCURRENT` | 100 | 最大并发数 |
| `stream` | `true` | 流式请求 |
| `CONCURRENT_TEST_RUN_ID` | `run-t103` | 注册模型的 run_id |
| `CONCURRENT_TEST_SESSION_ID` | `session-t103-{8位随机哈希}` | 随机生成的 session_id |
| FD 最低要求 | `并发数 × 2 + 200 = 400` | 脚本启动时检查 `ulimit -Sn` |

| 步骤 | 操作 | 接口 |
|---|---|---|
| 1 | 检查文件描述符限制（`ulimit -Sn ≥ 400`）；不满足则退出 | — |
| 2 | 注册模型（`run_id=run-t103`, `token_in_token_out=false`） | `POST /models/register` |
| 3 | 并发发送 100 个流式 Chat 请求（`stream: true`，维持最多 100 个并发） | `POST /s/{run_id}/{session_id}/v1/chat/completions` |
| 4 | 等待所有请求完成（`wait`） | — |
| 5 | 删除模型 | `DELETE /models?model_name={model}&run_id={run_id}` |
| 6 | 读取所有子进程结果文件并汇总统计（含 chunk 数和流式完整性） | — |

**验收标准**:

| # | 断言 | 期望值 |
|---|---|---|
| 1 | 文件描述符限制（`ulimit -Sn`） | ≥ 400（否则脚本直接退出） |
| 2 | 注册 HTTP 状态 | 200 |
| 3 | 注册 `status` | `"success"` |
| 4 | 全部 100 个请求 HTTP 状态 | 200 |
| 5 | 成功率 | 100% |
| 6 | 每请求 chunk 数 | > 0（`grep -c "^data: {"`） |
| 7 | 每请求包含 `[DONE]` 终止符 | ✓ |
| 8 | 流式完整率 | 100%（`has_done=true` 且 `chunk_count > 0`） |
| 9 | 删除 HTTP 状态 | 200 |
| 10 | 删除 `status` | `"success"` |

**性能指标输出**（脚本统计但不做硬阈值断言）:

| 指标 | 说明 |
|---|---|
| 总请求数 | 100 |
| 成功次数 / 失败次数 | 计数 |
| 成功率 | 百分比 |
| 流式完整数 / 不完整数 | 计数 |
| 流式完整率 | 百分比 |
| 最大并发数 | 100 |
| 平均响应时间 | `TOTAL_RESPONSE_TIME / TOTAL_REQUESTS`（ms） |
| 最小 / 最大响应时间 | 极值（ms） |
| 平均 chunk 数 | `TOTAL_CHUNK_COUNT / TOTAL_REQUESTS` |
| 最小 / 最大 chunk 数 | 极值 |

**并发控制机制**: 同 T102 滑动窗口式并发控制，但最大并发数为 100。

**流式完整性判定**: 脚本对每个流式响应同时检查两项：
1. `chunk_count > 0`：响应体包含至少 1 个 `data: {` 行
2. `has_done=true`：响应体包含 `[DONE]` 标记

两项均满足才计为"流式完整"；任一不满足则计入 `STREAM_INCOMPLETE_COUNT`。

**FD 限制检查**: 脚本在启动并发请求前执行 `ulimit -Sn` 检查。每个并发请求至少需要 2 个文件描述符（请求 + 响应），加上 200 个系统开销预留。最低要求为 `CONCURRENT_TEST_MAX_CONCURRENT × 2 + 200 = 400`。不满足时脚本输出修复建议并退出。

---

## 性能层覆盖率矩阵

| 验证项 | T101 | T102 | T103 |
|---|:---:|:---:|:---:|
| 模型注册 + 删除 | ✅ | ✅ | ✅ |
| 串行持续请求 | ✅ | | |
| 并发请求（滑动窗口） | | ✅ | ✅ |
| 流式请求 | | | ✅ |
| SSE chunk 计数 | | | ✅ |
| `[DONE]` 完整性 | | | ✅ |
| FD 限制前置检查 | | | ✅ |
| 响应延迟统计（avg/min/max） | ✅ | ✅ | ✅ |
| 成功率统计 | ✅ | ✅ | ✅ |