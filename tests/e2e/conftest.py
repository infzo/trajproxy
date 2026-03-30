"""
pytest 配置和 fixtures

提供测试所需的通用 fixtures
"""

import pytest
import requests
import uuid
from typing import Generator

from tests.e2e.config import (
    PROXY_URL,
    LITELLM_URL,
    DEFAULT_MODEL,
    get_session_id,
    REQUEST_TIMEOUT
)


@pytest.fixture
def proxy_client() -> requests.Session:
    """
    创建用于访问 TrajProxy 的 HTTP 客户端

    返回:
        配置好的 requests.Session 实例
    """
    session = requests.Session()
    session.timeout = REQUEST_TIMEOUT
    yield session
    session.close()


@pytest.fixture
def unique_session_id() -> str:
    """
    生成唯一的 session_id，确保测试隔离

    返回:
        格式为 {prefix}_{uuid};{sample_id};{task_id} 的唯一 session_id
    """
    unique_prefix = f"e2e_{uuid.uuid4().hex[:8]}"
    return f"{unique_prefix};sample_001;task_001"


@pytest.fixture
def sample_id() -> str:
    """
    生成唯一的 sample_id

    返回:
        格式为 sample_{uuid} 的唯一 sample_id
    """
    return f"sample_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def task_id() -> str:
    """
    生成唯一的 task_id

    返回:
        格式为 task_{uuid} 的唯一 task_id
    """
    return f"task_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def default_headers(unique_session_id: str) -> dict:
    """
    默认请求头，包含唯一的 session_id

    参数:
        unique_session_id: 唯一 session_id fixture

    返回:
        请求头字典
    """
    return {
        "Content-Type": "application/json",
        "x-session-id": unique_session_id
    }


@pytest.fixture(scope="session")
def check_service_available():
    """
    检查服务是否可用

    在所有测试开始前检查 TrajProxy 是否运行
    """
    try:
        response = requests.get(f"{PROXY_URL}/health", timeout=5)
        if response.status_code != 200:
            pytest.fail(f"TrajProxy 服务不可用: {PROXY_URL}")
    except requests.exceptions.ConnectionError:
        pytest.fail(f"无法连接到 TrajProxy 服务: {PROXY_URL}")


@pytest.fixture
def registered_model_name() -> str:
    """
    返回预置模型名称

    返回:
        config.yaml 中配置的模型名称
    """
    return DEFAULT_MODEL


def pytest_configure(config):
    """
    pytest 配置钩子，注册自定义标记
    """
    config.addinivalue_line(
        "markers", "slow: 标记慢速测试（如流式测试）"
    )
    config.addinivalue_line(
        "markers", "integration: 标记集成测试（需要推理服务）"
    )
