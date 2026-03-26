"""
路由注册器

管理 Worker 的路由注册，提供解耦的路由组合方式
"""
from fastapi import FastAPI


class RouteRegistrar:
    """路由注册器 - 管理多个路由模块的注册"""

    def __init__(self, app: FastAPI):
        """
        初始化路由注册器

        参数:
            app: FastAPI 应用实例
        """
        self.app = app

    def register_proxy_routes(self):
        """注册 ProxyCore 相关路由"""
        from traj_proxy.proxy_core.routes import router as proxy_router, admin_router as admin_router

        self.app.include_router(proxy_router, prefix="/proxy/v1", tags=["OpenAI Chat"])
        self.app.include_router(proxy_router, prefix="/proxy/{session_id}/v1", tags=["OpenAI Chat (Path-based)"])
        self.app.include_router(admin_router, prefix="/proxy/models", tags=["Admin"])

    def register_transcript_routes(self):
        """注册 TranscriptProvider 相关路由"""
        from traj_proxy.transcript_provider.routes import router as transcript_router

        self.app.include_router(transcript_router, prefix="/transcript", tags=["Transcript"])

    def register_health_route(self):
        """注册健康检查路由（只保留一个）"""
        @self.app.get("/health", tags=["Health"])
        async def health():
            return {"status": "ok"}

    def register_all(self):
        """注册所有路由"""
        self.register_proxy_routes()
        self.register_transcript_routes()
        self.register_health_route()
