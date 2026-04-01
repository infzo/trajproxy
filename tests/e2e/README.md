# TrajProxy 端到端测试

端到端测试框架，用于验证 TrajProxy 所有 API 功能是否正常。

## 前置条件

1. **启动推理服务**

   在运行测试前，需要先启动 Qwen3.5-2B 推理服务。推荐使用 vLLM：

   ```bash
   # 使用 vLLM 启动推理服务
   python -m vllm.entrypoints.openai.api_server \
       --model Qwen/Qwen3.5-2B \
       --port 8000 \
       --dtype auto
   ```

   或使用 Ollama：

   ```bash
   ollama run qwen2.5:2b
   ```

2. **配置推理服务地址**

   确保 `config.yaml` 中的推理服务地址正确：

   ```yaml
   proxy_workers:
     models:
       - model_name: qwen3.5-2b
         url: http://host.docker.internal:8000  # 推理服务地址
         api_key: sk-1234
         tokenizer_path: Qwen/Qwen3.5-2B
         token_in_token_out: true
   ```

3. **启动 TrajProxy**

   ```bash
   cd traj_proxy
   ./start_docker.sh
   ```

## 运行测试

### 一键运行所有测试

```bash
cd traj_proxy/tests/e2e
python run_e2e.py
```

### 运行特定模块测试

```bash
# 只测试健康检查
python run_e2e.py --module health

# 测试健康检查和聊天
python run_e2e.py --module health chat

# 测试模型管理
python run_e2e.py --module models

# 测试轨迹查询
python run_e2e.py --module trajectory
```

### 过滤测试类型

```bash
# 只运行集成测试（需要推理服务）
python run_e2e.py --marker integration

# 跳过慢速测试
python run_e2e.py --marker "not slow"

# 组合过滤
python run_e2e.py --marker "integration and not slow"
```

### 详细输出

```bash
python run_e2e.py -v
```

### 跳过服务检查

如果服务在其他地方运行，可以跳过检查：

```bash
python run_e2e.py --skip-service-check
```

## 测试模块说明

| 模块 | 文件 | 说明 |
|------|------|------|
| health | test_health.py | 健康检查接口测试 |
| http_request_formats | test_http_request_formats.py | HTTP 请求/响应格式测试、Tool Calling |
| models | test_model_management.py | 模型管理接口测试 |
| parsers | test_parsers.py | Parser 单元测试 |
| session_id | test_session_id.py | Session ID 传递和路由测试 |
| token_mode | test_token_mode.py | Token-in-Token-out 模式测试 |
| trajectory | test_trajectory.py | 轨迹查询接口测试 |

## 测试标记

- `@pytest.mark.integration`: 集成测试，需要推理服务支持
- `@pytest.mark.slow`: 慢速测试，如流式响应测试

## 配置覆盖

可以通过环境变量覆盖默认配置：

```bash
# 自定义服务地址
export PROXY_URL="http://localhost:12300"
export LITELLM_URL="http://localhost:4000"

# 自定义测试模型
export TEST_MODEL="qwen3.5-2b"

# 运行测试
python run_e2e.py
```

## 使用 pytest 直接运行

也可以直接使用 pytest 命令：

```bash
# 运行所有测试
pytest tests/e2e/ -v

# 运行单个测试文件
pytest tests/e2e/test_health.py -v

# 运行特定测试
pytest tests/e2e/test_chat.py::TestChatCompletion::test_chat_completion_non_stream -v

# 生成 HTML 报告
pytest tests/e2e/ --html=report.html --self-contained-html
```

## 测试输出示例

```
============================================================
  TrajProxy 端到端测试
============================================================

ℹ 检查服务状态...
✓ TrajProxy 服务正常运行
ℹ 测试模块: 全部
ℹ 标记过滤: 无

test_health.py::TestHealthCheck::test_health_check PASSED
test_health.py::TestHealthCheck::test_health_check_responsive PASSED
test_chat.py::TestChatCompletion::test_chat_completion_non_stream PASSED
test_chat.py::TestChatCompletion::test_chat_completion_stream PASSED
test_models.py::TestModelsAPI::test_list_models PASSED
test_models.py::TestModelsAPI::test_register_and_delete_model PASSED
test_trajectory.py::TestTrajectoryAPI::test_query_trajectory_after_chat PASSED

============================================================
  测试汇总
============================================================
ℹ 总耗时: 12.34 秒
✓ 所有测试通过！
```

## 常见问题

### 1. 服务不可用

```
✗ TrajProxy 服务不可用
! 请确保 TrajProxy 已启动
```

**解决方案**: 先启动 TrajProxy 服务

```bash
cd traj_proxy
./start_docker.sh
```

### 2. 模型未注册

```
test_chat.py::test_chat_completion FAILED - 模型 'qwen3.5-2b' 未注册
```

**解决方案**: 检查 `config.yaml` 中是否配置了该模型

### 3. 推理服务连接失败

```
test_chat.py::test_chat_completion FAILED - Connection refused
```

**解决方案**: 确保推理服务已启动且地址正确

## 依赖

- Python 3.8+
- pytest >= 7.0
- requests >= 2.28

安装依赖：

```bash
pip install pytest requests
```
