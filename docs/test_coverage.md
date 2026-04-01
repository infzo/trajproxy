# TrajProxy 测试用例文档

本文档详细描述了 TrajProxy 项目的测试覆盖范围和测试用例说明。

## 测试概览

### 测试文件结构

```
tests/
└── e2e/                              # 端到端测试目录
    ├── config.py                     # 测试配置
    ├── conftest.py                   # pytest fixtures
    ├── README.md                     # 测试说明文档
    ├── run_e2e.py                    # 测试运行脚本
    ├── test_health.py                # 健康检查测试
    ├── test_http_request_formats.py  # HTTP 请求格式测试
    ├── test_model_management.py      # 模型管理测试
    ├── test_parsers.py               # Parser 单元测试
    ├── test_session_id.py            # Session ID 测试
    ├── test_token_mode.py            # Token-in-Token-out 模式测试
    └── test_trajectory.py            # 轨迹记录测试
```

### 测试统计

| 测试文件 | 测试类数量 | 测试用例数量 | 主要覆盖内容 |
|---------|----------|------------|-------------|
| test_health.py | 1 | 1 | 健康检查 API |
| test_http_request_formats.py | 5 | 22 | HTTP 请求/响应格式、Tool Calling |
| test_model_management.py | 3 | 9 | 模型注册/删除/列表 |
| test_parsers.py | 12 | 35 | Tool/Reasoning Parser 解析逻辑 |
| test_session_id.py | 3 | 10 | Session ID 传递和路由 |
| test_token_mode.py | 1 | 7 | Token-in-Token-out 模式 |
| test_trajectory.py | 1 | 2 | 轨迹记录查询 |
| **总计** | **26** | **86** | - |

---

## 测试详细说明

### 1. 健康检查测试 (test_health.py)

#### TestHealthCheck

| 测试用例 | 描述 | 验证点 |
|---------|------|--------|
| test_health_check | 测试健康检查接口 | 返回状态码 200，响应体包含 {"status": "ok"}，响应时间 < 1 秒 |

---

### 2. HTTP 请求格式测试 (test_http_request_formats.py)

#### TestOpenAIFormat - OpenAI 格式请求测试

| 测试用例 | 描述 | 验证点 |
|---------|------|--------|
| test_non_stream_response_format | 非流式请求响应格式 | 返回状态码 200，响应格式符合 OpenAI API 规范，包含 id/choices/usage 字段 |
| test_stream_response_format | 流式请求响应格式 | 返回状态码 200，响应格式为 SSE，流式数据块格式正确，最后收到 [DONE] 标记 |
| test_stream_options_include_usage | stream_options.include_usage 参数 | 最后一个数据块包含 usage 字段，usage 包含完整 token 统计 |
| test_max_completion_tokens_parameter | max_completion_tokens 参数 | 参数正确转发，请求成功返回 |
| test_tool_calling_non_stream | Tool Calling 非流式请求 | 响应格式正确，tool_calls 结构正确（如果模型返回） |
| test_tool_calling_stream | Tool Calling 流式请求 | 流式请求成功，tool_calls 参数增量传输正确 |

#### TestClaudeFormat - Claude 格式请求测试

| 测试用例 | 描述 | 验证点 |
|---------|------|--------|
| test_non_stream_response_format | 非流式请求响应格式 | 响应格式符合 Anthropic Messages API 规范，包含 id/type/content/role 字段 |
| test_stream_response_format | 流式请求响应格式 | 响应格式为 SSE，事件类型序列正确（message_start, message_stop 等） |
| test_tool_use_response_format | tool_use 响应格式 | tool_use content block 结构正确，包含 id/type/name/input 字段 |

#### TestToolCalling - Tool Calling 功能测试

| 测试用例 | 描述 | 验证点 |
|---------|------|--------|
| test_tool_choice_none | tool_choice="none" 请求 | 即使有工具定义，tool_choice="none" 也不应触发工具调用 |
| test_parallel_tool_calls | 并行工具调用 | 模型能同时返回多个 tool_calls，每个 tool_call 有唯一 id |
| test_tool_result_continuation | 工具结果提交后继续对话 | 完整流程：用户请求 -> 模型返回 tool_calls -> 提交工具结果 -> 模型继续回复 |

#### TestErrorHandling - 错误处理测试

| 测试用例 | 描述 | 验证点 |
|---------|------|--------|
| test_invalid_model_returns_404 | 无效模型请求 | 返回状态码 404，错误信息提示模型未注册 |
| test_tool_call_with_invalid_tool_choice | 无效 tool_choice 参数 | 返回适当的状态码（200/400/422） |
| test_tool_result_without_tool_call_id | 缺少 tool_call_id 的工具结果 | 返回适当的状态码（200/400/422） |
| test_tools_exceed_limit | 工具数量过多 | 能处理大量工具定义或返回适当错误 |

#### TestFullPipeline - 完整链路测试 (Nginx -> LiteLLM -> TrajProxy)

| 测试用例 | 描述 | 验证点 |
|---------|------|--------|
| test_openai_pipeline_non_stream | OpenAI 非流式请求完整链路 | 请求经过 Nginx -> LiteLLM -> TrajProxy，返回正确 |
| test_openai_pipeline_stream | OpenAI 流式请求完整链路 | 流式请求成功，[DONE] 标记正确 |
| test_claude_pipeline_non_stream | Claude 非流式请求完整链路 | 响应格式符合 Anthropic API 规范 |
| test_litellm_openai_routing | OpenAI 推理请求通过 litellm 转发 | 请求正确路由，响应正确 |
| test_litellm_claude_routing | Claude 推理请求通过 litellm 转发 | 请求正确路由，响应正确 |
| test_nginx_load_balancing | nginx 负载均衡测试 | 多次请求被分发到不同 worker，所有请求正常响应 |

---

### 3. 模型管理测试 (test_model_management.py)

#### TestModelRegistration - 模型注册测试

| 测试用例 | 描述 | 验证点 |
|---------|------|--------|
| test_register_and_delete_model | 注册和删除模型 | 注册成功返回 200，注册后能查询到，删除后无法查询 |
| test_register_model_with_tool_parser | 带 tool_parser 的模型注册 | 响应包含 tool_parser 字段 |
| test_register_model_with_reasoning_parser | 带 reasoning_parser 的模型注册 | 响应包含 reasoning_parser 字段 |
| test_register_model_with_run_id | 带 run_id 的模型注册 | 响应包含 run_id 字段，模型列表显示 run_id/model_name 格式 |

#### TestModelDeletion - 模型删除测试

| 测试用例 | 描述 | 验证点 |
|---------|------|--------|
| test_register_duplicate_model | 注册重复模型 | 返回状态码 400/409，错误信息提示模型已存在 |
| test_delete_nonexistent_model | 删除不存在的模型 | 返回状态码 404，错误信息提示模型不存在 |

#### TestModelListing - 模型列表测试

| 测试用例 | 描述 | 验证点 |
|---------|------|--------|
| test_list_models | 列出模型接口 | OpenAI 格式 (/v1/models) 和管理格式 (/models/) 都正确响应 |

---

### 4. Parser 单元测试 (test_parsers.py)

#### TestDeepSeekV3ToolParser - DeepSeek V3 Tool Parser 测试

| 测试用例 | 描述 | 验证点 |
|---------|------|--------|
| test_extract_single_tool_call | 提取单个工具调用 | 正确提取工具名称和参数 JSON，tools_called 为 True |
| test_extract_multiple_tool_calls | 提取多个工具调用 | 正确提取多个工具调用，每个有唯一 ID |
| test_extract_with_content_before | 工具调用前有文本内容 | 正确提取工具调用前的内容，工具调用正常解析 |
| test_no_tool_call | 没有工具调用的情况 | tools_called 为 False，content 为原始输出 |
| test_ascii_format | ASCII 格式的标记符 | 支持 Unicode 和 ASCII 两种标记符格式 |
| test_streaming_state_reset | 流式状态重置 | reset_streaming_state 清除所有状态 |

#### TestQwen3CoderToolParser - Qwen3 Coder Tool Parser 测试

| 测试用例 | 描述 | 验证点 |
|---------|------|--------|
| test_extract_single_tool_call | 提取单个工具调用 | 正确提取函数名和参数，参数类型转换正确 |
| test_extract_multiple_tool_calls | 提取多个工具调用 | 正确提取多个工具调用 |
| test_parameter_type_conversion | 参数类型转换 | 整数/布尔/浮点数参数正确转换 |
| test_nested_json_parameter | 嵌套 JSON 参数 | 嵌套 JSON 参数正确解析 |

#### TestQwenXMLToolParser - Qwen XML Tool Parser 测试

| 测试用例 | 描述 | 验证点 |
|---------|------|--------|
| test_extract_tool_call | 提取工具调用 | 验证 XML 格式工具调用解析 |

#### TestDeepSeekV31ToolParser - DeepSeek V3.1 Tool Parser 测试

| 测试用例 | 描述 | 验证点 |
|---------|------|--------|
| test_extract_single_tool_call | 提取单个工具调用 | V3.1 格式（无 function 类型前缀）正确解析 |
| test_extract_multiple_tool_calls | 提取多个工具调用 | 多个工具调用正确提取 |
| test_ascii_format | ASCII 格式标记符 | 支持 ASCII 格式 |

#### TestLlama3JSONToolParser - Llama 3 JSON Tool Parser 测试

| 测试用例 | 描述 | 验证点 |
|---------|------|--------|
| test_extract_with_python_tag | 带 python_tag 的工具调用 | 正确提取工具名称和参数 |
| test_extract_direct_json | 直接 JSON 格式 | 直接 JSON 格式正确解析 |
| test_extract_array_format | 数组格式多个工具调用 | 数组格式正确解析 |
| test_no_tool_call | 没有工具调用的情况 | tools_called 为 False |

#### TestDeepSeekR1ReasoningParser - DeepSeek R1 Reasoning Parser 测试

| 测试用例 | 描述 | 验证点 |
|---------|------|--------|
| test_extract_reasoning_with_content | 提取推理内容和回复内容 | 正确分离推理内容和回复内容 |
| test_extract_reasoning_only | 只有推理内容 | 正确处理只有推理内容的情况 |
| test_extract_no_end_token | 推理内容没有结束标记 | 剩余内容作为推理处理 |
| test_only_end_token_without_start | 只有 end_token，没有 start_token | end_token 之前的内容作为 reasoning |

#### TestQwen3ReasoningParser - Qwen3 Reasoning Parser 测试

| 测试用例 | 描述 | 验证点 |
|---------|------|--------|
| test_extract_reasoning | Qwen3 格式推理内容提取 | 验证 Qwen3 格式解析 |

#### TestDeepSeekV3ReasoningParser - DeepSeek V3 Reasoning Parser 测试

| 测试用例 | 描述 | 验证点 |
|---------|------|--------|
| test_extract_reasoning | DeepSeek V3 格式推理内容提取 | 验证 DeepSeek V3 格式解析 |

#### TestReasoningWithToolCalls - Reasoning + Tool Calls 组合测试

| 测试用例 | 描述 | 验证点 |
|---------|------|--------|
| test_combined_reasoning_then_tool_call | 先 reasoning 后 tool_calls 的组合 | 正确分离 reasoning 和 tool_calls，两种 parser 协同工作 |
| test_combined_response_structure | 组合响应的完整结构 | 模拟完整响应构建流程，验证最终响应格式符合 OpenAI 规范 |

#### TestToolParserManager - Tool Parser Manager 测试

| 测试用例 | 描述 | 验证点 |
|---------|------|--------|
| test_list_registered_parsers | 列出已注册的 parser | 验证所有预期的 parser 已注册 |
| test_get_parser_by_name | 按名称获取 parser | 能正确获取 parser 实例 |
| test_get_nonexistent_parser | 获取不存在的 parser | 抛出 KeyError 异常 |

#### TestReasoningParserManager - Reasoning Parser Manager 测试

| 测试用例 | 描述 | 验证点 |
|---------|------|--------|
| test_list_registered_parsers | 列出已注册的 parser | 验证所有预期的 parser 已注册 |
| test_get_parser_by_name | 按名称获取 parser | 能正确获取 parser 实例 |
| test_get_nonexistent_parser | 获取不存在的 parser | 抛出 KeyError 异常 |

#### TestParserEdgeCases - Parser 边界情况测试

| 测试用例 | 描述 | 验证点 |
|---------|------|--------|
| test_empty_input | 空输入 | 空输入不崩溃，返回合理结果 |
| test_malformed_tool_call | 格式错误的工具调用 | 格式不完整时不崩溃 |
| test_malformed_reasoning | 格式错误的 reasoning | 格式不完整时正确处理 |
| test_special_characters_in_arguments | 参数中的特殊字符 | 特殊字符正确处理 |

---

### 5. Session ID 测试 (test_session_id.py)

#### TestSessionIdDelivery - Session ID 传递方式测试

| 测试用例 | 描述 | 验证点 |
|---------|------|--------|
| test_delivery_via_header | 通过请求头传递 session_id | 返回状态码 200，请求正常处理 |
| test_delivery_via_path | 通过 URL 路径传递 session_id | URL 路径中的 session_id 正确传递 |
| test_delivery_via_model_at_format | 通过 model@session_id 格式传递 | model 参数中的 session_id 正确解析 |
| test_delivery_priority | session_id 传递方式的优先级 | 优先级：路径 > 请求头 > model@session_id |

#### TestSessionIdFormat - Session ID 格式验证测试

| 测试用例 | 描述 | 验证点 |
|---------|------|--------|
| test_empty_session_id_uses_model_name_as_run_id | session_id 为空时 | run_id 等于 model_name，能正常路由 |
| test_valid_session_id_with_comma | 有效 session_id（包含逗号） | 正确提取 run_id，不会返回 400 错误 |
| test_invalid_session_id_without_comma_returns_400 | session_id 不包含逗号 | 返回 400 错误，提示格式无效 |
| test_model_at_session_id_format | model@session_id 格式解析 | 正确分离 model 和 session_id |

#### TestRunIdRouting - Run ID 路由隔离测试

| 测试用例 | 描述 | 验证点 |
|---------|------|--------|
| test_run_id_isolation | 不同 run_id 的模型隔离 | 同一 model_name 可以注册不同 run_id 的多个模型 |
| test_no_fallback_to_global_model | 不存在回退到全局模型 | 特定 run_id 模型不存在时返回 404，不回退到全局模型 |

---

### 6. Token-in-Token-out 模式测试 (test_token_mode.py)

#### TestTokenInTokenOutMode

| 测试用例 | 描述 | 验证点 |
|---------|------|--------|
| test_non_streaming_token_mode_basic | 非流式模式基本功能 | 请求成功返回，响应格式正确，usage 统计包含 token 信息 |
| test_streaming_token_mode_basic | 流式模式基本功能 | 流式请求成功，数据块格式正确，[DONE] 标记正确 |
| test_prefix_cache_hit | 前缀匹配缓存命中 | 缓存命中的 token 数量 > 0 |
| test_token_mode_with_tool_calls | Token 模式下的工具调用 | 如果模型返回工具调用，格式正确 |
| test_trajectory_record_completeness | 轨迹记录完整性 | 轨迹记录包含 token_ids 和 full_conversation_token_ids |
| test_completion_tokens_estimation | completion_tokens 统计 | completion_tokens 应该大于 0 |

---

### 7. 轨迹记录测试 (test_trajectory.py)

#### TestTrajectoryAPI

| 测试用例 | 描述 | 验证点 |
|---------|------|--------|
| test_query_trajectory_after_chat | 发送聊天后查询轨迹记录 | 轨迹查询成功，包含正确的 session_id 和模型名称，记录字段完整 |
| test_query_nonexistent_trajectory | 查询不存在的轨迹记录 | 返回状态码 200，响应包含 session_id 和空的 records |

---

## 测试标记说明

项目使用 pytest 标记来分类测试：

| 标记 | 说明 | 示例 |
|-----|------|------|
| `@pytest.mark.integration` | 集成测试，需要推理服务支持 | Tool Calling 测试 |
| `@pytest.mark.slow` | 慢速测试，如流式响应测试 | 流式响应测试 |

运行带标记的测试：

```bash
# 只运行集成测试
pytest tests/e2e/ -m integration

# 跳过慢速测试
pytest tests/e2e/ -m "not slow"

# 组合过滤
pytest tests/e2e/ -m "integration and not slow"
```

---

## 测试覆盖范围总结

### API 接口覆盖

| API 端点 | 覆盖情况 | 说明 |
|---------|---------|------|
| `GET /health` | ✅ 完全覆盖 | 健康检查接口 |
| `POST /v1/chat/completions` | ✅ 完全覆盖 | OpenAI 格式聊天补全（流式/非流式） |
| `POST /v1/messages` | ✅ 完全覆盖 | Claude 格式消息（流式/非流式） |
| `GET /v1/models` | ✅ 完全覆盖 | OpenAI 格式模型列表 |
| `GET /models/` | ✅ 完全覆盖 | 管理格式模型列表 |
| `POST /models/register` | ✅ 完全覆盖 | 模型注册 |
| `DELETE /models` | ✅ 完全覆盖 | 模型删除 |
| `GET /trajectory` | ✅ 完全覆盖 | 轨迹记录查询 |

### 功能特性覆盖

| 功能特性 | 覆盖情况 | 说明 |
|---------|---------|------|
| OpenAI 格式请求 | ✅ 完全覆盖 | 流式/非流式、Tool Calling |
| Claude 格式请求 | ✅ 完全覆盖 | 流式/非流式、Tool Use |
| Tool Calling | ✅ 完全覆盖 | 单个/并行/流式 Tool Calling |
| Tool Parsers | ✅ 完全覆盖 | DeepSeek V3/V3.1/V3.2、Qwen3 Coder、Qwen XML、GLM、Llama3 JSON |
| Reasoning Parsers | ✅ 完全覆盖 | DeepSeek R1、DeepSeek V3、Qwen3、GLM |
| Session ID 传递 | ✅ 完全覆盖 | 请求头/URL路径/model@session_id 三种方式 |
| Run ID 路由隔离 | ✅ 完全覆盖 | 不同 run_id 模型隔离 |
| Token-in-Token-out 模式 | ✅ 完全覆盖 | 编码/解码、缓存命中、统计准确性 |
| 完整链路测试 | ✅ 完全覆盖 | Nginx -> LiteLLM -> TrajProxy |
| 错误处理 | ✅ 完全覆盖 | 无效模型、格式错误、参数错误 |

---

## 运行测试

### 前置条件

1. 启动推理服务（如 vLLM）
2. 配置 config.yaml 中的推理服务地址
3. 启动 TrajProxy 服务

### 运行命令

```bash
# 一键运行所有测试
cd tests/e2e
python run_e2e.py

# 运行特定模块
python run_e2e.py --module health models

# 详细输出
python run_e2e.py -v

# 跳过服务检查
python run_e2e.py --skip-service-check
```

### 使用 pytest 直接运行

```bash
# 运行所有测试
pytest tests/e2e/ -v

# 运行单个测试文件
pytest tests/e2e/test_health.py -v

# 生成 HTML 报告
pytest tests/e2e/ --html=report.html --self-contained-html
```
