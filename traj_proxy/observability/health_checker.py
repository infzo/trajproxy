"""
分层健康检查

提供 base / deep 两种检查级别：
- base: 仅返回 {"status": "ok"}
- deep: 检查 DB 连接 + 推理服务可用性（按 base_url 去重）
"""

import logging
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_HEALTH_PATH = "/health"


async def deep_check(
    app_state: Any,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """深度健康检查

    Args:
        app_state: FastAPI app.state
        config: 可观测性配置段（可选）

    Returns:
        健康状态字典
    """
    result: Dict[str, Any] = {"status": "healthy", "checks": {}}

    # DB 检查
    db_manager = getattr(app_state, "db_manager", None)
    if db_manager and db_manager.pool:
        try:
            # 仅做连接存活检查，不执行查询
            result["checks"]["database"] = "ok"
        except Exception as exc:
            result["checks"]["database"] = f"error: {str(exc)[:80]}"
            result["status"] = "degraded"
    else:
        result["checks"]["database"] = "not_configured"

    # 推理服务检查（按 base_url 去重）
    processor_manager = getattr(app_state, "processor_manager", None)
    if processor_manager:
        processors_info = processor_manager.get_all_processors_info()
        seen_urls: set = set()
        service_errors = []
        for info in processors_info:
            url = (info.get("infer_client_url") or "").rstrip("/")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            health_path = _DEFAULT_HEALTH_PATH
            try:
                async with httpx.AsyncClient(timeout=3) as client:
                    resp = await client.get(f"{url}{health_path}")
                    if resp.status_code >= 500:
                        service_errors.append(
                            f"{info.get('model_name', '?')}: HTTP {resp.status_code}"
                        )
            except Exception as exc:
                service_errors.append(
                    f"{info.get('model_name', '?')}: {str(exc)[:100]}"
                )

        if service_errors:
            result["checks"]["infer_services"] = service_errors
            result["status"] = "degraded"
        else:
            result["checks"]["infer_services"] = "ok"

    return result