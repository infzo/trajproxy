"""
健康检查 API 测试

测试 TrajProxy 的健康检查接口
"""

import pytest
import requests

from tests.e2e.config import PROXY_URL


class TestHealthCheck:
    """健康检查测试类"""

    def test_health_check(self, proxy_client: requests.Session):
        """
        测试健康检查接口

        验证点:
        - 返回状态码 200
        - 响应体包含 {"status": "ok"}
        """
        response = proxy_client.get(f"{PROXY_URL}/health")

        assert response.status_code == 200, f"健康检查失败: {response.text}"

        data = response.json()
        assert data.get("status") == "ok", f"健康状态异常: {data}"

    def test_health_check_responsive(self, proxy_client: requests.Session):
        """
        测试健康检查响应时间

        验证点:
        - 响应时间小于 1 秒
        """
        import time

        start = time.time()
        response = proxy_client.get(f"{PROXY_URL}/health")
        elapsed = time.time() - start

        assert response.status_code == 200
        assert elapsed < 1.0, f"健康检查响应过慢: {elapsed:.2f}s"
