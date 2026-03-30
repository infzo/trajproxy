"""
路由注册器

管理 Worker 的路由注册，提供解耦的路路由组合方式
"""
from fastapi import FastAPI


class RouteRegistrar:
    """路由注册器 - 管理多个路由模块的注册"""

    def __init__(self, app: FastAPI):
        self.app = app

    def register_proxy_routes(self):
        """注册 ProxyCore 相关路由"""
        from traj_proxy.proxy_core.routes import (
            router as proxy_router,
        )
        from traj_proxy.proxy_core.routes import admin_router

        from traj_proxy.proxy_core.routes import list_models

        # /v1/chat/completions - 无session_id的聊天
        self.app.include_router(proxy_router, prefix="/v1", tags=["OpenAI Chat"])
        # /s/{session_id}/v1/chat/completions - 带session_id的聊天
        self.app.include_router(proxy_router, prefix="/s/{session_id}/v1", tags=["OpenAI Chat (Path-based)"])
        # /models/* - 模型管理 API
        self.app.include_router(admin_router, prefix="/models", tags=["Admin"])

        # /models GET - OpenAI 格式的模型列表
        self.app.get("/models", tags=["Models"])(list_models)

    def register_transcript_routes(self):
        """注册 TranscriptProvider 相关路由"""
        from traj_proxy.transcript_provider.routes import router as transcript_router

        # 使用 include_router 保持异常处理一致性
        self.app.include_router(transcript_router, tags=["Transcript"])
    def register_health_route(self):
        """注册健康检查路由"""
        @self.app.get("/health", tags=["Health"])
        async def health():
            return {"status": "ok"}
    def register_all(self):
        """注册所有路由"""
        self.register_proxy_routes()
        self.register_transcript_routes()
        self.register_health_route()
