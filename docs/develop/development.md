# 开发指南

> **导航**: [文档中心](../README.md) | [部署指南](deployment.md) | [配置说明](configuration.md)

本文档介绍如何搭建本地开发环境、理解代码结构、运行测试。

---

## 开发环境搭建

### 1. 克隆项目

```bash
git clone <repository_url>
cd TrajProxy
```

### 2. 创建虚拟环境

```bash
python -m venv venv
source venv/bin/activate  # Linux/macOS
# 或 venv\Scripts\activate  # Windows
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. 准备数据库

```bash
# 启动 PostgreSQL（Docker 方式）
docker run -d --name traj_proxy_db \
    -e POSTGRES_DB=traj_proxy \
    -e POSTGRES_USER=llmproxy \
    -e POSTGRES_PASSWORD=dbpassword9090 \
    -p 5432:5432 postgres:16

# 初始化数据库
export DATABASE_URL="postgresql://llmproxy:dbpassword9090@localhost:5432/traj_proxy"
python scripts/init_db.py
```

### 5. 配置推理服务

确保 LLM 推理服务可用，例如：

```bash
# 使用 vLLM
vllm serve Qwen/Qwen3.5-2B --port 8000

# 或使用 Ollama
ollama serve
```

### 6. 启动开发服务

```bash
# 直接启动（需要设置环境变量）
export RAY_WORKING_DIR="."
export RAY_PYTHONPATH="."
python -m traj_proxy.app
```

---

## 代码结构

```
TrajProxy/
├── configs/                        # 配置文件目录
│   ├── config.yaml                 # TrajProxy 主配置
│   ├── litellm.yaml                # LiteLLM 网关配置
│   ├── nginx.conf                  # Nginx 配置
│   └── prometheus.yml              # Prometheus 配置
│
├── traj_proxy/                     # 主代码目录
│   ├── app.py                      # 应用入口
│   ├── exceptions.py               # 自定义异常
│   │
│   ├── serve/                      # API 服务层
│   │   ├── routes.py               # API 路由定义
│   │   ├── schemas.py              # 请求/响应模型
│   │   └── dependencies.py         # FastAPI 依赖注入
│   │
│   ├── proxy_core/                 # 推理核心模块
│   │   ├── processor.py            # 统一请求处理器
│   │   ├── processor_manager.py    # 处理器管理器
│   │   ├── infer_client.py         # 推理客户端
│   │   ├── infer_response_parser.py # Infer 响应解析器
│   │   ├── context.py              # 处理上下文数据类
│   │   ├── provider.py             # 轨迹查询提供者
│   │   │
│   │   ├── pipeline/               # 处理管道
│   │   │   ├── base.py             # 管道基类
│   │   │   ├── direct_pipeline.py  # 直接转发管道
│   │   │   └── token_pipeline.py   # Token 模式管道
│   │   │
│   │   ├── converters/             # 转换器
│   │   │   ├── message_converter.py # 消息转换器
│   │   │   └── token_converter.py  # Token 转换器
│   │   │
│   │   ├── builders/               # 响应构建器
│   │   │   ├── openai_builder.py   # OpenAI 响应构建器
│   │   │   └── stream_builder.py   # 流式响应构建器
│   │   │
│   │   ├── cache/                  # 缓存
│   │   │   └── prefix_cache.py     # 前缀匹配缓存
│   │   │
│   │   └── parsers/                # 解析器模块
│   │       ├── base.py             # 基础数据结构
│   │       ├── parser_manager.py   # 解析器管理器
│   │       └── vllm_compat/        # vLLM 兼容解析器
│   │           ├── tool_parsers/   # 工具解析器
│   │           └── reasoning_parsers/ # 推理解析器
│   │
│   ├── store/                      # 存储模块
│   │   ├── database_manager.py     # 数据库连接池管理
│   │   ├── model_repository.py     # 模型配置仓库
│   │   ├── request_repository.py   # 请求记录仓库
│   │   ├── model_synchronizer.py   # 模型同步器
│   │   ├── notification_listener.py # LISTEN/NOTIFY 监听器
│   │   └── models.py               # 数据模型定义
│   │
│   ├── workers/                    # Worker 模块
│   │   ├── worker.py               # ProxyWorker 实现
│   │   ├── manager.py              # Worker 管理器
│   │   └── route_registrar.py      # 路由注册器
│   │
│   └── utils/                      # 工具模块
│       ├── config.py               # 配置管理
│       ├── logger.py               # 日志系统
│       └── validators.py           # 参数校验器
│
├── tests/                          # 测试目录
│   └── e2e/                        # 端到端测试
│       ├── run_tests.sh            # 测试运行脚本
│       ├── layers/                 # 分层测试
│       │   ├── nginx/              # Nginx 入口层测试
│       │   └── proxy/              # Proxy 直连层测试
│       └── utils.sh                # 测试工具函数
│
├── scripts/                        # 脚本目录
│   ├── docker-compose/             # Docker Compose 部署
│   │   ├── start.sh               # 启动脚本
│   │   ├── build_image.sh         # 构建镜像
│   │   └── entrypoint.sh          # 容器入口点
│   ├── docker-allinone/            # 混合容器部署
│   │   ├── build.sh               # 构建镜像
│   │   └── entrypoint.sh          # 容器入口点
│   └── tools/                      # 工具脚本
│       ├── archive_records.py      # 详情数据归档脚本
│       ├── download_tokenizer.py   # Tokenizer 下载
│       └── verify_jinja_consistency.py # Jinja 模板一致性验证
│
├── dockers/                        # Docker 相关
│   ├── docker-compose.yml          # 容器编排
│   └── Dockerfile                  # 镜像构建
│
├── models/                         # Tokenizer 模型目录
├── docs/                           # 文档目录
├── requirements.txt                # Python 依赖
└── readme.md                       # 项目说明
```

---

## 核心模块说明

### serve - API 服务层

处理 HTTP 请求的路由和参数校验。

- **routes.py**: 定义聊天补全、模型管理、轨迹查询等 API 端点
- **schemas.py**: Pydantic 模型定义
- **dependencies.py**: FastAPI 依赖注入

### proxy_core - 推理核心

核心请求处理流程（Pipeline 模式）：

```
请求 → routes.py → ProcessorManager.get_processor()
                     ↓
              Processor (选择 Pipeline)
                     ↓
         ┌─────────┴─────────┐
         ↓                   ↓
   DirectPipeline      TokenPipeline
   (直接转发模式)       (Token 模式)
         ↓                   ↓
         │            MessageConverter (messages → prompt_text)
         │                   ↓
         │            TokenConverter (prompt_text → token_ids, 前缀匹配)
         │                   ↓
         └───────┬───────────┘
                 ↓
          InferClient (发送到推理服务)
                 ↓
         ┌───────┴───────┐
         ↓               ↓
   DirectPipeline   TokenPipeline
   (直接返回)        (token_ids → text)
                         ↓
                  Parser (解析 tool_calls, reasoning)
                         ↓
                  ResponseBuilder (构建 OpenAI Response)
```

**核心组件**：

| 组件 | 文件 | 说明 |
|------|------|------|
| Processor | `processor.py` | 统一请求处理器，选择 Pipeline |
| ProcessorManager | `processor_manager.py` | 多模型处理器管理器 |
| DirectPipeline | `pipeline/direct_pipeline.py` | 直接转发管道 |
| TokenPipeline | `pipeline/token_pipeline.py` | Token 模式管道 |
| MessageConverter | `converters/message_converter.py` | 消息 → PromptText |
| TokenConverter | `converters/token_converter.py` | Text ↔ TokenIds |
| PrefixMatchCache | `cache/prefix_cache.py` | 前缀匹配缓存 |
| OpenAIResponseBuilder | `builders/openai_builder.py` | 构建非流式响应 |
| StreamChunkBuilder | `builders/stream_builder.py` | 构建流式响应 |

### store - 存储层

- **DatabaseManager**: 管理连接池
- **ModelRepository**: 模型配置 CRUD
- **RequestRepository**: 请求轨迹存储
- **ModelSynchronizer**: 模型配置同步
- **NotificationListener**: LISTEN/NOTIFY 实时同步

### workers - Worker 管理

- **WorkerManager**: 启动/管理多个 ProxyWorker
- **ProxyWorker**: FastAPI 应用，处理请求
- **RouteRegistrar**: 注册 API 路由

---

## 运行测试

### 测试环境准备

```bash
# 1. 启动数据库
docker run -d --name test_db \
    -e POSTGRES_DB=traj_proxy \
    -e POSTGRES_USER=llmproxy \
    -e POSTGRES_PASSWORD=dbpassword9090 \
    -p 5432:5432 postgres:16

# 2. 初始化数据库
export DATABASE_URL="postgresql://llmproxy:dbpassword9090@localhost:5432/traj_proxy"
python scripts/init_db.py

# 3. 启动推理服务（或 mock 服务）
# 确保 http://localhost:8000 有可用的推理服务
```

### 运行 E2E 测试

```bash
cd tests/e2e

# 运行全部两层测试（Nginx + Proxy）
./run_tests.sh

# 仅运行 Nginx 层测试（port 12345）
./run_tests.sh --layer nginx

# 仅运行 Proxy 层测试（port 12300）
./run_tests.sh --layer proxy

# 按编号搜索运行
./run_tests.sh F100

# 指定层内特定用例
./run_tests.sh --layer nginx F100 F101
```

### 测试文件说明

**测试目录结构**：

```
tests/e2e/
├── run_tests.sh          # 顶层编排器
├── config.sh             # 测试配置
├── utils.sh              # 工具函数
└── layers/
    ├── nginx/            # Nginx 入口层测试 (port 12345)
    │   ├── run_layer.sh
    │   └── scenarios/
    │       ├── F100_basic_chat.sh
    │       ├── F101_claude_scenario.sh
    │       ├── F102_streaming_chat.sh
    │       └── ...
    └── proxy/            # Proxy 直连层测试 (port 12300)
        ├── run_layer.sh
        └── scenarios/
            ├── F200_model_register_list_delete.sh
            ├── F201_pangu_integration.sh
            └── ...
```

**测试分类**：

| 前缀 | 类型 | 说明 |
|------|------|------|
| F1xx | 功能测试 | Nginx 层基础功能 |
| F2xx | 功能测试 | Proxy 层功能 |
| P1xx | 性能测试 | 并发、稳定性测试 |

---

## 调试技巧

### 日志查看

日志输出到标准输出，可通过重定向保存：

```bash
python -m traj_proxy.app 2>&1 | tee logs/debug.log
```

### 修改日志级别

在代码中使用：

```python
from traj_proxy.utils.logger import get_logger
logger = get_logger(__name__)
logger.setLevel("DEBUG")
```

### 数据库调试

```bash
# 连接数据库
psql -h localhost -U llmproxy -d traj_proxy

# 查看最近的请求记录（活跃数据）
SELECT m.unique_id, m.model, m.start_time, m.total_tokens,
       d.prompt_text, d.response_text
FROM request_metadata m
JOIN request_details_active d ON m.unique_id = d.unique_id
WHERE m.archive_location IS NULL
ORDER BY m.start_time DESC LIMIT 10;

# 查看会话元数据（含活跃+已归档）
SELECT unique_id, model, start_time, total_tokens, archive_location
FROM request_metadata
ORDER BY start_time DESC LIMIT 10;

# 查看已注册模型
SELECT * FROM model_registry;

# 查看详情分区
SELECT relname FROM pg_class
WHERE relname LIKE 'request_details_active_%'
  AND relnamespace = 'public'::regnamespace;
```

### 单独测试组件

```python
# 测试 Tokenizer
from transformers import AutoTokenizer
tokenizer = AutoTokenizer.from_pretrained("Qwen/Qwen3.5-2B")
tokens = tokenizer.encode("Hello, world!")
print(tokens)

# 测试 Parser
from traj_proxy.proxy_core.parsers import ParserManager
parser_cls = ParserManager.get_tool_parser_cls("deepseek_v3")
print(parser_cls)
```

---

## 添加新的 Parser

### 1. 创建 Parser 类

在 `traj_proxy/proxy_core/parsers/vllm_compat/tool_parsers/` 或 `reasoning_parsers/` 下创建文件：

```python
from traj_proxy.proxy_core.parsers.base import (
    ToolParser, ExtractedToolCallInfo, ToolCall, FunctionCall
)

class MyToolParser(ToolParser):
    """自定义工具解析器"""

    def extract_tool_calls(
        self,
        model_output: str,
        tools: Optional[List[dict]] = None,
        request: Optional[Any] = None
    ) -> ExtractedToolCallInfo:
        # 实现解析逻辑
        pass

    def extract_tool_calls_streaming(
        self,
        previous_text: str,
        current_text: str,
        delta_text: str,
        previous_token_ids: Sequence[int],
        current_token_ids: Sequence[int],
        delta_token_ids: Sequence[int],
        tools: Optional[List[dict]] = None,
        request: Optional[Any] = None
    ) -> Optional[DeltaMessage]:
        # 实现流式解析逻辑
        pass
```

### 2. 注册 Parser

在 `traj_proxy/proxy_core/parsers/vllm_compat/tool_parsers/__init__.py` 中注册：

```python
from .my_parser import MyToolParser

# 添加到模块导出
__all__ = [..., "MyToolParser"]
```

然后在 `parser_manager.py` 中添加映射。

### 3. 使用 Parser

在模型配置中指定：

```yaml
models:
  - model_name: my-model
    tool_parser: "my_parser"
```

---

## 代码风格

- **缩进**: 4 个空格
- **命名**: snake_case（变量/函数），PascalCase（类）
- **注释**: 中文，详细说明
- **导入**: 绝对路径，项目根路径开头

示例：

```python
from traj_proxy.store.models import ModelConfig
from traj_proxy.utils.logger import get_logger

logger = get_logger(__name__)


def process_request(messages: list, model_name: str) -> dict:
    """处理请求

    Args:
        messages: 消息列表
        model_name: 模型名称

    Returns:
        处理结果字典
    """
    # 实现逻辑
    pass
```
