"""
路由注册器

管理 Worker 的路由注册，提供解耦的路由组合方式
"""
from fastapi import FastAPI


class RouteRegistrar:
    """路由注册器 - 管理多个路由模块的注册"""

    def __init__(self, app: FastAPI):
        self.app = app

    def register_proxy_routes(self):
        """注册 ProxyCore 相关路由"""
        from traj_proxy.serve.routes import (
            chat_router,
            model_router,
        )

        # /v1/chat/completions - 无session-id的聊天
        self.app.include_router(chat_router, prefix="/v1", tags=["OpenAI Chat"])
        # /s/{run_id}/{session_id}/v1/chat/completions - 带run_id和session_id的聊天
        self.app.include_router(chat_router, prefix="/s/{run_id}/{session_id}/v1", tags=["OpenAI Chat (Path-based)"])
        # /s/{session_id}/v1/chat/completions - 仅带session_id的聊天（无run_id）
        self.app.include_router(chat_router, prefix="/s/{session_id}/v1", tags=["OpenAI Chat (Path-based, no run_id)"])
        # /models/* - 模型管理 API
        self.app.include_router(model_router, prefix="/models", tags=["Admin"])

    def register_transcript_routes(self):
        """注册 TranscriptProvider 相关路由"""
        from traj_proxy.serve.routes import transcript_router

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
