"""
端到端测试配置

支持通过环境变量覆盖默认配置
"""

import os

# ============================================
# 服务地址配置
# ============================================

# Nginx 网关地址（完整链路入口）
NGINX_URL = os.getenv("NGINX_URL", "http://localhost:12345")

# TrajProxy Worker 直接访问地址
PROXY_URL = os.getenv("PROXY_URL", "http://localhost:12300")

# LiteLLM 网关地址
LITELLM_URL = os.getenv("LITELLM_URL", "http://localhost:4000")

# LiteLLM 认证密钥
LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "sk-1234")

# Anthropic API 版本
ANTHROPIC_VERSION = os.getenv("ANTHROPIC_VERSION", "2023-06-01")

# ============================================
# 测试模型配置
# ============================================

# 预置模型名称（在 config.yaml 中配置）
DEFAULT_MODEL = os.getenv("TEST_MODEL", "qwen3.5-2b")

# 测试用的 run_id（如果模型按 run_id 隔离）
DEFAULT_RUN_ID = os.getenv("TEST_RUN_ID", "")

# ============================================
# 测试数据配置
# ============================================

# 测试会话ID前缀
SESSION_PREFIX = os.getenv("TEST_SESSION_PREFIX", "e2e_test")

# 默认测试消息
TEST_MESSAGE = "你好，请回复'测试成功'"

# 流式测试超时时间（秒）
STREAM_TIMEOUT = 60

# 非流式测试超时时间（秒）
REQUEST_TIMEOUT = 120

# ============================================
# 工具函数
# ============================================


def get_session_id(sample_id: str = "sample_001", task_id: str = "task_001") -> str:
    """
    生成测试用的 session_id

    格式: {prefix};{sample_id};{task_id}
    """
    return f"{SESSION_PREFIX};{sample_id};{task_id}"


def get_headers(session_id: str = None) -> dict:
    """
    获取测试请求头

    参数:
        session_id: 会话ID，默认使用 get_session_id() 生成

    返回:
        请求头字典
    """
    if session_id is None:
        session_id = get_session_id()

    return {
        "Content-Type": "application/json",
        "x-session-id": session_id
    }
