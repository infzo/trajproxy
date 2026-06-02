#!/usr/bin/env python3
"""缓存推理代理服务 - 加速 E2E 测试执行

位于 traj_proxy 和真实推理服务之间的中间层:
  - 首次收到请求时，转发给真实推理服务并缓存响应到本地 JSON 文件
  - 后续相同请求（SHA256 哈希匹配）直接返回缓存结果
  - 对调用方（traj_proxy）完全透明，响应格式逐字节一致

用法:
    REAL_INFER_URL="http://host.docker.internal:8000/v1" \\
    REAL_INFER_API_KEY="sk-1234" \\
    python3 cache_infer_server.py [port]

    默认端口: 18999

缓存存储:
    tests/e2e/cache_store/<sha256_hash>.json

管理 API:
    GET  /cache/health         - 健康检查
    GET  /cache/stats          - 缓存统计（条目数、命中/未命中计数、缓存列表）
    DELETE /cache/clear         - 清空所有缓存
    DELETE /cache/entry/<hash>  - 删除指定缓存条目

环境变量:
    REAL_INFER_URL         - 真实推理服务地址（必需），如 http://host.docker.internal:8000/v1
    REAL_INFER_API_KEY     - 真实推理服务 API Key（可选，不设置则透传原始 Authorization header）
    CACHE_STORE_DIR        - 缓存存储目录（可选，默认为脚本同级的 cache_store/）
"""

import hashlib
import json
import os
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any, Dict, Optional

import httpx

# ============================================================
# 全局缓存统计
# ============================================================
_cache_stats: Dict[str, Any] = {
    "hits": 0,
    "misses": 0,
    "entries": 0,
}
_stats_lock = threading.Lock()


def _get_cache_store_dir() -> Path:
    """获取缓存存储目录"""
    env_dir = os.environ.get("CACHE_STORE_DIR", "")
    if env_dir:
        return Path(env_dir)
    # 默认为脚本所在目录下的 cache_store/
    return Path(__file__).resolve().parent / "cache_store"


CACHE_STORE_DIR = _get_cache_store_dir()


def _get_real_infer_url() -> str:
    """获取真实推理服务地址"""
    url = os.environ.get("REAL_INFER_URL", "")
    if not url:
        print("[ERROR] REAL_INFER_URL 环境变量未设置", flush=True)
        sys.exit(1)
    return url.rstrip("/")


def _get_real_infer_api_key() -> Optional[str]:
    """获取真实推理服务 API Key（可选）"""
    return os.environ.get("REAL_INFER_API_KEY") or None


REAL_INFER_URL = _get_real_infer_url()
REAL_INFER_API_KEY = _get_real_infer_api_key()

# ============================================================
# httpx 客户端（复用连接）
# ============================================================
_client: Optional[httpx.Client] = None
_client_lock = threading.Lock()


def _get_client() -> httpx.Client:
    """获取或创建 httpx 客户端（线程安全）"""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = httpx.Client(
                    timeout=httpx.Timeout(connect=10.0, read=600.0, write=30.0, pool=10.0),
                    limits=httpx.Limits(max_connections=50),
                )
    return _client


# ============================================================
# 请求上下文数据类
# ============================================================
class RequestContext:
    """封装待缓存的请求信息"""

    def __init__(self, method: str, path: str, body_json: dict):
        self.method = method
        self.path = path
        self.body = body_json

    def compute_hash(self) -> str:
        """计算请求的 SHA256 哈希（缓存键）"""
        canonical_body = json.dumps(self.body, sort_keys=True, ensure_ascii=False)
        raw = f"{self.method}|{self.path}|{canonical_body}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ============================================================
# HTTP 请求处理器
# ============================================================
class CacheInferHandler(BaseHTTPRequestHandler):
    """缓存推理代理请求处理器"""

    # ---------------------------
    # 缓存读写
    # ---------------------------

    def _get_cache_path(self, request_hash: str) -> Path:
        """获取缓存文件路径"""
        return CACHE_STORE_DIR / f"{request_hash}.json"

    def _load_from_cache(self, request_hash: str) -> Optional[dict]:
        """从缓存加载响应数据，不存在则返回 None"""
        cache_path = self._get_cache_path(request_hash)
        if not cache_path.exists():
            return None
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                entry = json.load(f)
            # 更新命中计数
            entry["hit_count"] = entry.get("hit_count", 0) + 1
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(entry, f, ensure_ascii=False, indent=2)
            return entry
        except (json.JSONDecodeError, IOError, KeyError):
            # 缓存文件损坏，视为未命中
            return None

    def _save_to_cache(
        self,
        request_hash: str,
        ctx: RequestContext,
        status_code: int,
        resp_headers: Dict[str, str],
        is_streaming: bool,
        raw_body: str,
    ):
        """将响应保存到缓存文件"""
        CACHE_STORE_DIR.mkdir(parents=True, exist_ok=True)

        entry = {
            "request_hash": request_hash,
            "request": {
                "method": ctx.method,
                "path": ctx.path,
                "body": ctx.body,
            },
            "response": {
                "status_code": status_code,
                "headers": resp_headers,
                "is_streaming": is_streaming,
                "body": raw_body,
            },
            "cached_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            "hit_count": 0,
        }

        cache_path = self._get_cache_path(request_hash)
        # 先写入临时文件（含线程 ID 避免并发冲突），再原子重命名
        tmp_path = cache_path.with_suffix(f".tmp.{os.getpid()}_{threading.get_ident()}")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(entry, f, ensure_ascii=False, indent=2)
        tmp_path.replace(cache_path)

        with _stats_lock:
            _cache_stats["entries"] = len(list(CACHE_STORE_DIR.glob("*.json")))

    # ---------------------------
    # 真实后端转发
    # ---------------------------

    def _build_forward_headers(self) -> Dict[str, str]:
        """构建转发给真实后端的请求头"""
        headers: Dict[str, str] = {}

        # 透传部分原始请求头
        forward_header_keys = {
            "content-type",
            "accept",
            "accept-encoding",
            "user-agent",
        }
        for key, value in self.headers.items():
            if key.lower() in forward_header_keys:
                headers[key] = value

        # 设置认证头
        if REAL_INFER_API_KEY:
            headers["Authorization"] = f"Bearer {REAL_INFER_API_KEY}"
        elif "authorization" in {k.lower() for k in self.headers.keys()}:
            # 透传原始 Authorization
            for key, value in self.headers.items():
                if key.lower() == "authorization":
                    headers[key] = value
                    break

        return headers

    def _forward(self, ctx: RequestContext) -> tuple:
        """非流式转发请求到真实后端，返回 (status_code, headers, body_text)"""
        client = _get_client()
        url = f"{REAL_INFER_URL}{ctx.path}"
        headers = self._build_forward_headers()

        resp = client.request(
            method=ctx.method,
            url=url,
            headers=headers,
            content=json.dumps(ctx.body, ensure_ascii=False).encode("utf-8"),
        )

        # 提取需要的响应头
        resp_headers = {
            "content-type": resp.headers.get("content-type", "application/json"),
        }

        body_text = resp.text
        return resp.status_code, resp_headers, body_text

    def _forward_stream(self, ctx: RequestContext) -> tuple:
        """流式转发请求到真实后端，积累所有 SSE 数据后返回

        返回 (status_code, headers, raw_sse_text)
        raw_sse_text 包含完整的 SSE 原始字节（含 data: [DONE]）
        """
        client = _get_client()
        url = f"{REAL_INFER_URL}{ctx.path}"
        headers = self._build_forward_headers()
        headers["Accept"] = "text/event-stream"

        resp = client.request(
            method=ctx.method,
            url=url,
            headers=headers,
            content=json.dumps(ctx.body, ensure_ascii=False).encode("utf-8"),
        )

        # 收集所有 SSE 数据行
        raw_lines: list = []
        for line in resp.iter_lines():
            raw_lines.append(line)

        raw_text = "\n".join(raw_lines)
        # 确保以 \n 结尾（SSE 规范要求）
        if not raw_text.endswith("\n"):
            raw_text += "\n"

        resp_headers = {
            "content-type": resp.headers.get("content-type", "text/event-stream"),
        }

        return resp.status_code, resp_headers, raw_text

    # ---------------------------
    # 响应发送
    # ---------------------------

    def _send_response_from_cache(self, cache_entry: dict):
        """从缓存回放响应"""
        response = cache_entry["response"]
        status_code = response["status_code"]
        resp_headers = response.get("headers", {})
        is_streaming = response.get("is_streaming", False)
        raw_body = response.get("body", "")

        if is_streaming:
            self._send_sse_raw(status_code, resp_headers, raw_body)
        else:
            self._send_json_raw(status_code, resp_headers, raw_body)

    def _send_json_raw(self, status_code: int, headers: Dict[str, str], body: str):
        """发送非流式 JSON 响应"""
        content_type = headers.get("content-type", "application/json")
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))
        self.wfile.flush()
        self.close_connection = True

    def _send_sse_raw(self, status_code: int, headers: Dict[str, str], raw_sse: str):
        """发送流式 SSE 响应（直接写入原始字节）"""
        content_type = headers.get("content-type", "text/event-stream")
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.wfile.write(raw_sse.encode("utf-8"))
        self.wfile.flush()
        self.close_connection = True

    def _send_json_response(self, data: dict, status: int = 200):
        """发送 JSON 响应（用于管理 API）"""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
        self.wfile.flush()
        self.close_connection = True

    def _send_error(self, status: int, message: str):
        """发送错误响应"""
        self._send_json_response({"error": message}, status)

    # ---------------------------
    # 请求处理入口
    # ---------------------------

    def _read_body(self) -> bytes:
        """读取请求体"""
        content_length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(content_length) if content_length > 0 else b""

    def _parse_body(self, body_bytes: bytes) -> dict:
        """解析请求体为 JSON"""
        if not body_bytes:
            return {}
        try:
            return json.loads(body_bytes)
        except json.JSONDecodeError:
            return {"_raw": body_bytes.decode("utf-8", errors="replace")}

    def _handle_inference_request(self):
        """处理推理请求（/v1/chat/completions 或 /v1/completions）"""
        body_bytes = self._read_body()
        body = self._parse_body(body_bytes)
        ctx = RequestContext(method=self.command, path=self.path, body_json=body)
        request_hash = ctx.compute_hash()

        # ---- 检查缓存 ----
        cache_entry = self._load_from_cache(request_hash)
        if cache_entry is not None:
            with _stats_lock:
                _cache_stats["hits"] += 1
            self._send_response_from_cache(cache_entry)
            return

        # ---- 缓存未命中，转发到真实后端 ----
        with _stats_lock:
            _cache_stats["misses"] += 1

        is_streaming = body.get("stream", False)

        try:
            if is_streaming:
                status_code, resp_headers, raw_body = self._forward_stream(ctx)
            else:
                status_code, resp_headers, raw_body = self._forward(ctx)

            # ---- 保存到缓存 ----
            self._save_to_cache(request_hash, ctx, status_code, resp_headers, is_streaming, raw_body)

            # ---- 返回响应 ----
            if is_streaming:
                self._send_sse_raw(status_code, resp_headers, raw_body)
            else:
                self._send_json_raw(status_code, resp_headers, raw_body)

        except httpx.ConnectError as e:
            self._send_error(502, f"无法连接到真实推理服务: {REAL_INFER_URL} - {e}")
        except httpx.TimeoutException as e:
            self._send_error(504, f"真实推理服务响应超时: {e}")
        except Exception as e:
            self._send_error(500, f"转发请求失败: {type(e).__name__}: {e}")

    # ---------------------------
    # HTTP 方法分发
    # ---------------------------

    def do_POST(self):
        """处理 POST 请求"""
        if self.path in ("/v1/chat/completions", "/v1/completions"):
            self._handle_inference_request()
        else:
            self._send_error(404, f"未知的推理端点: {self.path}")

    def do_GET(self):
        """处理 GET 请求"""
        if self.path == "/cache/health":
            self._send_json_response({"status": "ok", "service": "cache_infer_server"})
        elif self.path == "/cache/stats":
            with _stats_lock:
                stats = dict(_cache_stats)
            # 列出缓存条目
            entries = []
            if CACHE_STORE_DIR.exists():
                for f in sorted(CACHE_STORE_DIR.glob("*.json")):
                    try:
                        with open(f, "r", encoding="utf-8") as fh:
                            entry = json.load(fh)
                        entries.append({
                            "hash": entry.get("request_hash", ""),
                            "path": entry.get("request", {}).get("path", ""),
                            "streaming": entry.get("response", {}).get("is_streaming", False),
                            "hit_count": entry.get("hit_count", 0),
                            "cached_at": entry.get("cached_at", ""),
                        })
                    except (json.JSONDecodeError, IOError):
                        pass
            stats["entries_list"] = entries
            self._send_json_response(stats)
        else:
            self._send_error(404, f"未知的管理端点: {self.path}")

    def do_DELETE(self):
        """处理 DELETE 请求"""
        if self.path == "/cache/clear":
            count = 0
            if CACHE_STORE_DIR.exists():
                for f in CACHE_STORE_DIR.glob("*.json"):
                    f.unlink()
                    count += 1
            with _stats_lock:
                _cache_stats["hits"] = 0
                _cache_stats["misses"] = 0
                _cache_stats["entries"] = 0
            self._send_json_response({"status": "cleared", "deleted": count})
        elif self.path.startswith("/cache/entry/"):
            request_hash = self.path[len("/cache/entry/"):]
            cache_path = self._get_cache_path(request_hash)
            if cache_path.exists():
                cache_path.unlink()
                with _stats_lock:
                    _cache_stats["entries"] = len(list(CACHE_STORE_DIR.glob("*.json")))
                self._send_json_response({"status": "deleted", "hash": request_hash})
            else:
                self._send_error(404, f"缓存条目不存在: {request_hash}")
        else:
            self._send_error(404, f"未知的管理端点: {self.path}")

    def log_message(self, format, *args):
        """静默日志（避免测试输出噪音）"""
        pass


# ============================================================
# 服务器启动
# ============================================================
def run_server(port: int = 18999):
    """启动缓存推理代理服务（多线程模式）"""

    class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True  # 主线程退出时自动结束子线程

    # 确保缓存目录存在
    CACHE_STORE_DIR.mkdir(parents=True, exist_ok=True)

    server = ThreadedHTTPServer(("0.0.0.0", port), CacheInferHandler)
    print(f"CacheInferServer started on port {port}", flush=True)
    print(f"  Real backend: {REAL_INFER_URL}", flush=True)
    print(f"  Cache dir:    {CACHE_STORE_DIR}", flush=True)
    print(f"  API key:      {'configured' if REAL_INFER_API_KEY else 'passthrough'}", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nCacheInferServer shutting down...", flush=True)
        server.shutdown()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18999
    run_server(port)
