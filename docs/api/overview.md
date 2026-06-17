# API 参考文档

> **导航**: [文档中心](../README.md) | [架构设计](../design/architecture.md)

TrajProxy 提供两种 API 访问方式：

---

## 文档索引

### [Nginx 入口 API](nginx.md)

对外统一入口 (端口 12345)，支持：
- 路径参数提取 (run_id, session_id)
- LiteLLM 网关路由
- TrajProxy 管理接口转发

**推荐场景**：生产环境统一入口

### [TrajProxy API](proxy.md)

直接访问 TrajProxy Worker (端口 12300+)，支持：
- 聊天补全接口
- 模型管理接口
- 轨迹查询接口
- 健康检查

**推荐场景**：开发调试、内部服务调用

### [OpenAI 兼容接口规范](openai-compat.md)

聊天补全接口的完整参数规范，包含：
- 所有输入/输出参数的支持情况（Direct 模式 vs TITO 模式）
- 不支持的参数说明（`n`、`enable_search`）
- Proxy 行为说明（透传机制、两种模式差异）

**推荐场景**：了解参数兼容性、接入评估

---

## 快速参考

| 场景 | 端口 | 文档 |
|------|------|------|
| 生产环境 | 12345 | [Nginx 入口 API](nginx.md) |
| 开发调试 | 12300+ | [TrajProxy API](proxy.md) |
| 参数规范 | - | [OpenAI 兼容接口规范](openai-compat.md) |

## ID 设计规范

参见 [ID 设计规范](../design/modules/identifiers.md) 了解 run_id、session_id 的语义和提取规则。
