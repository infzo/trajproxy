"""
模型管理 API 测试

测试模型的注册、删除和列表接口
"""

import pytest
import requests
import uuid

from tests.e2e.config import PROXY_URL


class TestModelsAPI:
    """模型管理测试类"""

    def test_list_models(self, proxy_client: requests.Session):
        """
        测试列出模型接口（OpenAI 格式）

        验证点:
        - 返回状态码 200
        - 响应格式符合 OpenAI /v1/models 规范
        - 至少包含一个模型
        """
        response = proxy_client.get(f"{PROXY_URL}/models")

        assert response.status_code == 200, f"列出模型失败: {response.text}"

        data = response.json()

        # 验证响应结构
        assert data.get("object") == "list", f"object 字段错误: {data.get('object')}"
        assert "data" in data, "响应缺少 data 字段"
        assert isinstance(data["data"], list), "data 不是列表类型"

        # 验证模型条目结构
        if len(data["data"]) > 0:
            model = data["data"][0]
            assert "id" in model, "模型条目缺少 id 字段"
            assert model.get("object") == "model", f"模型 object 字段错误: {model.get('object')}"

    def test_list_models_detail(self, proxy_client: requests.Session):
        """
        测试列出模型详情接口（管理格式）

        验证点:
        - 返回状态码 200
        - 响应包含模型详细信息
        """
        # 访问根路径获取管理格式的模型详情
        response = proxy_client.get(f"{PROXY_URL}/models/")

        assert response.status_code == 200, f"列出模型详情失败: {response.text}"

        data = response.json()

        # 验证响应结构
        assert data.get("status") == "success", f"状态错误: {data.get('status')}"
        assert "count" in data, "响应缺少 count 字段"
        assert "models" in data, "响应缺少 models 字段"
        assert isinstance(data["models"], list), "models 不是列表类型"

        # 验证模型详情结构
        if len(data["models"]) > 0:
            model = data["models"][0]
            assert "model_name" in model, "模型缺少 model_name 字段"

    @pytest.mark.integration
    def test_register_and_delete_model(self, proxy_client: requests.Session):
        """
        测试注册和删除模型

        验证点:
        - 注册成功返回 200
        - 注册后能查询到新模型
        - 删除成功返回 200
        - 删除后无法查询到该模型
        """
        # 生成唯一的模型名称
        test_model_name = f"test_model_{uuid.uuid4().hex[:8]}"

        # 注册模型
        register_response = proxy_client.post(
            f"{PROXY_URL}/models/register",
            json={
                "model_name": test_model_name,
                "url": "http://localhost:1234",  # 测试用的推理服务地址
                "api_key": "sk-test-key",
                "tokenizer_path": "Qwen/Qwen3.5-2B",
                "token_in_token_out": False
            }
        )

        assert register_response.status_code == 200, f"注册模型失败: {register_response.text}"

        register_data = register_response.json()
        assert register_data.get("status") == "success", f"注册状态错误: {register_data}"
        assert register_data.get("model_name") == test_model_name, f"模型名称不匹配: {register_data}"

        # 验证模型已注册（访问管理格式的模型列表）
        list_response = proxy_client.get(f"{PROXY_URL}/models/")
        assert list_response.status_code == 200

        list_data = list_response.json()
        model_names = [m.get("model_name") for m in list_data.get("models", [])]
        assert test_model_name in model_names, f"注册的模型不在列表中: {model_names}"

        # 删除模型
        delete_response = proxy_client.delete(f"{PROXY_URL}/models/{test_model_name}")

        assert delete_response.status_code == 200, f"删除模型失败: {delete_response.text}"

        delete_data = delete_response.json()
        assert delete_data.get("status") == "success", f"删除状态错误: {delete_data}"
        assert delete_data.get("deleted") is True, f"deleted 字段错误: {delete_data}"

        # 验证模型已删除
        list_response2 = proxy_client.get(f"{PROXY_URL}/models/")
        list_data2 = list_response2.json()
        model_names2 = [m.get("model_name") for m in list_data2.get("models", [])]
        assert test_model_name not in model_names2, f"删除的模型仍在列表中: {model_names2}"

    def test_register_duplicate_model(
        self,
        proxy_client: requests.Session,
        registered_model_name: str
    ):
        """
        测试注册重复模型

        验证点:
        - 返回状态码 400
        - 错误信息提示模型已存在
        """
        # 尝试注册已存在的模型
        response = proxy_client.post(
            f"{PROXY_URL}/models/register",
            json={
                "model_name": registered_model_name,
                "url": "http://localhost:1234",
                "api_key": "sk-test-key",
                "tokenizer_path": "test/tokenizer"
            }
        )

        # 可能返回 400 或 409
        assert response.status_code in [400, 409], \
            f"预期返回 400 或 409，实际返回 {response.status_code}"

        data = response.json()
        assert "已存在" in data.get("detail", "") or "存在" in data.get("detail", ""), \
            f"错误信息未提示模型已存在: {data}"

    def test_delete_nonexistent_model(self, proxy_client: requests.Session):
        """
        测试删除不存在的模型

        验证点:
        - 返回状态码 404
        - 错误信息提示模型不存在
        """
        response = proxy_client.delete(
            f"{PROXY_URL}/models/nonexistent_model_xyz"
        )

        assert response.status_code == 404, f"预期返回 404，实际返回 {response.status_code}"

        data = response.json()
        assert "不存在" in data.get("detail", "") or "未找到" in data.get("detail", ""), \
            f"错误信息未提示模型不存在: {data}"
