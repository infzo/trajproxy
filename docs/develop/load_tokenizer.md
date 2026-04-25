# Tokenizer 加载机制

## 概述

TrajProxy 支持两种 tokenizer 加载来源，按优先级自动回退：

1. **本地文件** — 镜像预置或手动放置在 `models_dir` 下
2. **数据库存储** — tokenizer 以 tar.gz 压缩包存储在 PostgreSQL，按需下载解压

对于多实例部署，无需在每个节点手动放置 tokenizer 文件，统一通过数据库管理即可。

## 整体流程

```mermaid
flowchart TD
    A[配置模型] -->|tokenizer_path: Qwen/Qwen3.5-2B| B[ProcessorManager.register]
    B --> C[_resolve_tokenizer_path]
    C --> D{本地 models_dir\n是否存在?}
    D -- 是 --> E[直接返回本地路径]
    D -- 否 --> F[_resolve_db_tokenizer]
    F --> G{数据库中\n是否存在?}
    G -- 是 --> H[下载 tar.gz → 解压到本地 → 返回路径]
    G -- 否 --> I[抛出 ValueError]
    H --> J[Processor 加载 tokenizer]
    E --> J
```

## 路径解析详解

`tokenizer_path` 仅支持**相对路径**，如 `Qwen/Qwen3.5-2B`。解析过程：

```mermaid
flowchart LR
    A["tokenizer_path = 'Qwen/Qwen3.5-2B'"] --> B["拼接本地路径\n/app/models/Qwen/Qwen3.5-2B"]
    B --> C{路径存在?}
    C -- 是 --> D["返回本地路径"]
    C -- 否 --> E["查询数据库\ntokenizer_packages"]
    E --> F{记录存在?}
    F -- 是 --> G["下载 tar.gz\n解压到本地"]
    F -- 否 --> H["ValueError:\ntokenizer 不存在"]
    G --> D
```

> 本地路径存在即命中，不会查询数据库。这是一个缓存优先策略。

## 数据库存储

### 表结构

```sql
tokenizer_packages (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,   -- 如 "Qwen/Qwen3.5-2B"
    content     BYTEA NOT NULL,        -- tar.gz 压缩包
    size        INTEGER NOT NULL,      -- 压缩包大小（字节）
    file_count  INTEGER,               -- 包含文件数
    created_at  TIMESTAMP DEFAULT NOW()
)
```

一个 tokenizer 对应一行，整个目录打包为单个 tar.gz 存入 `content` 字段。

### 上传流程

```mermaid
flowchart LR
    A[本地目录] --> B[遍历文件\n打包 tar.gz] --> C[写入数据库\nUPSERT]
    C --> D[完成]
```

上传时自动将指定目录递归打包为 tar.gz，使用 `ON CONFLICT ... DO UPDATE` 支持覆盖更新。

### 下载流程

```mermaid
flowchart TD
    A[查询数据库\nSELECT content] --> B[读取 tar.gz\n到内存]
    B --> C{路径安全检查}
    C -- 含 / 或 .. --> D[拒绝解压\nValueError]
    C -- 通过 --> E[解压到\ntarget_path]
    E --> F[返回本地路径]
```

解压前对压缩包内每个成员路径做安全校验，禁止绝对路径和路径穿越。

## 并发安全

多 Worker 同时注册同一模型时，可能并发触发下载：

```mermaid
sequenceDiagram
    participant W1 as Worker 1
    participant Lock as 文件锁
    participant DB as PostgreSQL
    participant W2 as Worker 2
    participant FS as 本地磁盘

    W1->>Lock: 请求锁
    W1->>Lock: 获得锁
    W1->>FS: 双重检查: 本地不存在
    W1->>DB: SELECT content
    DB-->>W1: tar.gz 数据
    W1->>FS: 解压到 models_dir
    W1->>Lock: 释放锁

    W2->>Lock: 请求锁 (阻塞)
    Note over W2: 等待 W1 释放锁
    W2->>Lock: 获得锁
    W2->>FS: 双重检查: 本地已存在
    W2->>FS: 直接返回缓存路径
    W2->>Lock: 释放锁
```

通过 `/tmp/tokenizer_dl_{name}.lock` 文件锁实现：
- 同一 tokenizer 同时只有一个 Worker 执行下载
- 获取锁后双重检查本地路径，避免重复下载

## 使用方式

### 上传 Tokenizer

```bash
# 单个上传
python scripts/manage_tokenizer.py upload \
    --name Qwen/Qwen3.5-2B \
    --path ./models/Qwen/Qwen3.5-2B

# 批量上传（自动扫描含 tokenizer_config.json 的目录）
python scripts/manage_tokenizer.py upload-all --models-dir ./models
```

### 配置模型

```yaml
proxy_workers:
  models:
    - model_name: qwen3.5-2b
      url: http://inference-server:8000/v1
      api_key: sk-xxxx
      tokenizer_path: Qwen/Qwen3.5-2B    # 相对路径
      token_in_token_out: true
```

首次使用时自动从数据库下载，后续使用本地缓存。

### 其他命令

```bash
# 列出数据库中的 tokenizer
python scripts/manage_tokenizer.py list

# 删除
python scripts/manage_tokenizer.py delete --name Qwen/Qwen3.5-2B

# 下载到本地（测试用）
python scripts/manage_tokenizer.py download --name Qwen/Qwen3.5-2B --output ./test
```

## 部署场景

### 场景一：镜像预置 + 数据库补充

```mermaid
graph TB
    subgraph Docker镜像
        M1[models/Qwen/Qwen2.5-7B]
        M2[models/Qwen/Qwen3.5-2B]
    end
    subgraph PostgreSQL
        M3[Qwen/Qwen3-Coder-30B-A3B-Instruct]
    end
    subgraph 运行时
        W1[Worker 1] -->|本地命中| M1
        W2[Worker 2] -->|本地命中| M2
        W3[Worker 3] -->|本地未命中| M3
        M3 -->|下载解压| L3[本地缓存]
        W3 --> L3
    end
```

常用 tokenizer 预置在镜像中，新模型通过数据库按需加载。

### 场景二：纯数据库管理

```mermaid
graph TB
    subgraph PostgreSQL
        M1[Qwen/Qwen2.5-7B]
        M2[Qwen/Qwen3.5-2B]
        M3[Qwen/Qwen3-Coder-30B-A3B-Instruct]
    end
    subgraph 运行时
        W1[Worker 1] -->|首次下载| M1
        W2[Worker 2] -->|首次下载| M2
        W3[Worker 3] -->|首次下载| M3
        M1 --> C1[本地缓存]
        M2 --> C2[本地缓存]
        M3 --> C3[本地缓存]
    end
```

镜像中不预置 models 目录，所有 tokenizer 统一由数据库管理。可移除 Dockerfile 中 `COPY models/` 行以减小镜像体积。

## 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| 上传失败："目录为空" | 指定目录无文件 | 确认路径指向含 `tokenizer_config.json` 的目录 |
| 首次请求延迟较高 | 需从数据库下载解压 | 属于正常现象，后续请求使用缓存 |
| 更新已上传的 tokenizer | 重复上传会覆盖 | 重新执行 `upload` 命令即可 |
| 清理本地缓存 | 需手动删除 | 删除 `models_dir` 下对应目录，下次请求自动重新下载 |
