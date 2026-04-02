# TrajProxy 验证测试套件

基于 `CASES.md` 规范生成的自动化测试脚本，用于验证 TrajProxy 系统的核心功能。

## 目录结构

```
tests/verify/
├── CASES.md                      # 测试用例规范文档
├── README.md                     # 本文档
├── config.sh                     # 配置文件（集中管理参数）
├── common.sh                     # 公共函数库
├── run_all.sh                    # 一键执行脚本
├── scenario_1_model_mgmt.sh      # 场景一：模型注册、罗列、删除接口测试
├── scenario_2_openai_chat.sh     # 场景二：OpenAI Chat 测试
├── scenario_3_claude_chat.sh     # 场景三：Claude Chat 测试
└── scenario_4_tool_reason.sh     # 场景四：Tool / Reason 测试
```

## 快速开始

### 1. 一键运行所有测试

```bash
cd tests/verify
./run_all.sh
```

### 2. 运行指定场景

```bash
# 只运行场景一
./run_all.sh 1

# 运行多个场景
./run_all.sh 1 2 3

# 运行所有场景
./run_all.sh all
```

### 3. 运行单个场景脚本

```bash
# 场景一：模型管理接口测试
./scenario_1_model_mgmt.sh

# 场景二：OpenAI Chat 测试
./scenario_2_openai_chat.sh

# 场景三：Claude Chat 测试
./scenario_3_claude_chat.sh

# 场景四：Tool / Reason 测试
./scenario_4_tool_reason.sh
```

## 配置参数

### 方式 1: 环境变量

```bash
# 设置环境变量
export PROXY_URL="http://localhost:12300"
export NGINX_URL="http://localhost:12345"
export INFERENCE_URL="http://localhost:8000/v1"
export API_KEY="sk-test-key"
export TEST_MODEL="my_test_model"
export TOKENIZER_PATH="Qwen/Qwen2.5-3B"

# 运行测试
./run_all.sh
```

### 方式 2: 命令行参数

```bash
./run_all.sh \
  --proxy-url http://localhost:12300 \
  --nginx-url http://localhost:12345 \
  --inference-url http://localhost:8000/v1 \
  --model my_test_model
```

### 方式 3: 修改 config.sh

直接编辑 `config.sh` 文件修改默认配置。

## 测试场景说明

### 场景一：模型注册、罗列、删除接口测试

**测试内容:**
- 模型注册（带/不带 run_id）
- 模型配置（token-in-token-out/parser/tokenizer_path）
- 模型列表查询（管理格式/OpenAI格式）
- 模型删除
- 异常处理（重复注册/删除不存在模型）

**验证标准:**
- 检查响应体中的 `status` 字段（success/error）
- 检查 `detail` 字段包含完整配置信息
- **不能仅靠 HTTP 状态码判断**

### 场景二：OpenAI Chat 测试

**测试内容:**
- 直接转发模式（token_in_token_out=false）
  - 非流式对话
  - 流式对话
- Token-in-Token-out 模式（token_in_token_out=true）
  - 非流式对话
  - 流式对话（带 usage）
- 轨迹抓取验证

**验证标准（基于 vllm-0.16.0）:**
- 非流式: `object="chat.completion"`, `choices[0].message` 包含 role/content
- 流式: `object="chat.completion.chunk"`, `choices[0].delta` 增量传输
- usage: `prompt_tokens`, `completion_tokens`, `total_tokens` 准确

### 场景三：Claude Chat 测试

**测试内容:**
- 通过 LiteLLM 网关的 Claude (Anthropic) 格式请求
- 非流式对话
- 流式对话
- 轨迹抓取验证

**验证标准:**
- 非流式: `type="message"`, `content` 数组包含 text block
- 流式: 事件序列 message_start → content_block_delta → message_stop

### 场景四：Tool / Reason 测试

**测试内容:**
- OpenAI Tool Call (非流式/流式)
- Claude Tool Use (非流式/流式)
- Reasoning 提取 (非流式/流式)
- 组合测试 (Reasoning + Tool Call)

**验证标准:**
- OpenAI: `tool_calls` 数组, `finish_reason="tool_calls"`, `reasoning` 字段
- Claude: `content[0].type="tool_use"`, `stop_reason="tool_use"`

## 输出说明

### 测试输出格式

每个测试步骤都会输出：

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[CURL 命令]
curl -s -w '\n%{http_code}' -X POST 'http://localhost:12300/v1/chat/completions' ...
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[请求信息]
  Method: POST
  URL:    http://localhost:12300/v1/chat/completions
  Body:
    {
      "model": "test_model",
      "messages": [...]
    }
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[响应信息]
  HTTP Status: 200
  Body:
    {
      "id": "chatcmpl-abc123",
      "object": "chat.completion",
      ...
    }
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[PASS] 验证 HTTP 状态码 = 200
[PASS] 验证 object 字段
[PASS] 验证 message.content 非空
```

### 测试摘要

每个场景测试完成后会输出摘要：

```
========================================
  测试摘要: 场景一：模型注册、罗列、删除接口测试
========================================

  总测试数:  15
  通过: 13
  失败: 2
  跳过: 0

✗ 部分测试失败
```

## 依赖要求

- **Bash 4.0+**: Shell 脚本执行环境
- **curl**: HTTP 请求工具
- **python3**: JSON 解析和格式化

## 故障排查

### 1. 连接失败

```
[FAIL] 健康检查 (HTTP 000)
```

**解决方案:**
- 检查 TrajProxy 服务是否运行: `curl http://localhost:12300/health`
- 检查 PROXY_URL 配置是否正确

### 2. 模型注册失败

```
[FAIL] 模型注册 (期望 HTTP 200, 实际 HTTP 400)
```

**解决方案:**
- 检查推理服务是否运行: `curl http://localhost:8000/v1/models`
- 检查 INFERENCE_URL 和 API_KEY 配置

### 3. Claude 测试失败

```
[FAIL] Claude 非流式对话请求 (HTTP 401)
```

**解决方案:**
- 检查 LiteLLM 网关是否运行
- 检查 LITELLM_API_KEY 配置
- 确认 LiteLLM 已配置 Claude 模型

### 4. Token 模式测试失败

```
[FAIL] 注册 Token 模式模型 (验证 tokenizer_path 非空)
```

**解决方案:**
- 检查 TOKENIZER_PATH 配置
- 确保 tokenizer 模型可访问

## 扩展开发

### 添加新测试场景

1. 创建新的场景脚本 `scenario_X_xxx.sh`
2. 引入公共库: `source "${SCRIPT_DIR}/common.sh"`
3. 使用 `http_get`, `http_post`, `http_delete` 函数
4. 使用断言函数验证结果
5. 在 `run_all.sh` 中添加场景编号映射

### 自定义断言函数

在 `common.sh` 中添加新的断言函数：

```bash
# 示例: 验证 JSON 数组长度
assert_json_array_length() {
    local json="$1"
    local path="$2"
    local expected="$3"
    local description="$4"

    local actual
    actual=$(echo "$json" | python3 -c "import sys,json; arr=json.load(sys.stdin); print(len($path))")

    if [[ "$actual" == "$expected" ]]; then
        log_success "$description"
        return 0
    else
        log_error "$description (期望: $expected, 实际: $actual)"
        return 1
    fi
}
```

## 相关文档

- [测试用例规范 (CASES.md)](./CASES.md)
- [API 参考文档](../../docs/api_reference.md)
- [架构文档](../../docs/architecture.md)

## 许可证

Copyright © 2026 TrajProxy Team
