"""
filters - 消息内容净化层

提供在 tokenize 之前对消息内容执行归一化的工具，
保障 TITO 模式前缀匹配缓存的稳定性。

当前模块：
  - content_sanitizer: 归一化 system message 中的动态变化字段

依赖关系：
  - 被 MessageConverter 通过构造函数注入，仅在 TITO 模式下生效
  - 不依赖任何外部服务、数据库或推理客户端
"""

from traj_proxy.proxy_core.filters.content_sanitizer import (
    ContentSanitizer,
    SanitizeRule,
)

__all__ = ["ContentSanitizer", "SanitizeRule"]
