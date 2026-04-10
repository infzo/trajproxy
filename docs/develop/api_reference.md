# API 参考文档

> **导航**: [文档中心](../README.md) | [架构设计](../design/architecture.md)

TrajProxy 提供两种 API 访问方式：

---

## 文档索引

### [Nginx 入口 API](api_nginx.md)

对外统一入口 (端口 12345)，支持：
- 路径参数提取 (run_id, session_id)
- LiteLLM 网关路由
- TrajProxy 管理接口转发

**推荐场景**：生产环境统一入口

### [TrajProxy API](api_proxy.md)

直接访问 TrajProxy Worker (端口 12300+)，支持：
- 聊天补全接口
- 模型管理接口
- 轨迹查询接口
- 健康检查

**推荐场景**：开发调试、内部服务调用

---

## 快速参考

| 场景 | 端口 | 文档 |
|------|------|------|
| 生产环境 | 12345 | [Nginx 入口 API](api_nginx.md) |
| 开发调试 | 12300+ | [TrajProxy API](api_proxy.md) |

## ID 设计规范

参见 [ID 设计规范](../design/identifier_design.md) 了解 run_id、session_id 的语义和提取规则。
