"""
BlobStorage - route_experts 大字段卸载存储抽象层

功能边界:
    为 route_experts 卸载特性（见 docs/design/features/route-experts-offload.md）
    提供统一的外部对象存储接口。负责 blob 的上传、流式读取、删除、存在性检查。
    不负责: TTL 清理、引用表 CRUD、marker 构造（由 OffloadingRepository 负责）。

对外接口:
    - StreamHandle: 已打开的 blob 流句柄，提供 iter_bytes / close
    - BlobStorage: 抽象基类，定义 put / open_stream / delete / exists / aclose
    - CSBBlobStorage: 华为 CSB 实现，三步认证（endpoint resolve → bucket auth → PUT/GET）
    - LocalDiskBlobStorage: 本地盘实现，写 {write_path}/{key}
    - create_blob_storage(config): 工厂函数，按 backend 配置选择实现

流式读取设计契约（P0 修复）:
    open_stream 是 async 方法，所有 I/O 启动（认证、文件打开、HTTP stream __aenter__）
    在返回前 await 完成。失败时同步抛 BlobStorageError，由路由 try/except 捕获映射为 502。
    StreamHandle.iter_bytes 只 yield 字节块，不做任何 I/O 启动或状态变更。
    StreamHandle.close 由上层（路由 StreamingResponse finally）调用，幂等释放资源。

依赖关系:
    - httpx（异步 HTTP 客户端，CSB 实现使用）
    - traj_proxy.exceptions.ProxyCoreError（异常基类）
    - traj_proxy.utils.logger（统一日志）
    架构上与 traj_archiver 解耦，不 import traj_archiver 任何模块；
    CSB 三步认证流程对齐 traj_archiver/csb_storage.py 但为独立实现。
"""

import asyncio
import base64
import json
from abc import ABC, abstractmethod
from contextlib import AbstractAsyncContextManager
from io import BufferedReader
from pathlib import Path
from typing import AsyncIterator, Optional

import httpx

from traj_proxy.exceptions import ProxyCoreError
from traj_proxy.utils.logger import get_logger

logger = get_logger(__name__)

# 本地盘流式读取的块大小
_CHUNK_SIZE = 64 * 1024
# CSB HTTP 请求超时（秒）
_REQUEST_TIMEOUT = 120.0
# CSB 默认厂商与区域（对齐 traj_archiver/csb_storage.py 默认值）
_DEFAULT_VENDOR = "HEC"
_DEFAULT_REGION = ""


class BlobStorageError(ProxyCoreError):
    """Blob 存储操作异常

    继承 ProxyCoreError，使 serve 层错误处理器自动映射为 HTTP 500。
    """
    status_code = 500
    error_type = "blob_storage_error"


class StreamHandle(ABC):
    """已打开的 blob 流句柄.

    所有 I/O 启动 (认证、文件打开、HTTP stream 发起) 在 open_stream 返回前完成.
    iter_bytes 只 yield 字节块, 不做任何状态变更或额外 I/O 启动.
    close 在 StreamingResponse 的 finally 块调用, 释放资源.
    close 必须幂等 (多次调用不报错).
    """

    @abstractmethod
    def iter_bytes(self) -> AsyncIterator[bytes]:
        """yield 字节块. 不做任何 I/O 启动或状态变更.

        声明为非 async: 实现为 async generator (async def + yield),
        调用时直接返回 AsyncIterator (无需 await), 由消费者 async for 遍历.
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """释放资源. 幂等: 多次调用不报错."""
        ...


class BlobStorage(ABC):
    """外部对象存储抽象基类

    所有方法均为协程；open_stream 异步打开流返回 StreamHandle 以支持大对象流式传输。
    delete 幂等：对象不存在不视为错误。
    aclose 释放后端持有资源（如 httpx.AsyncClient），默认空实现，子类按需覆盖。
    """

    @abstractmethod
    async def put(self, key: str, data: bytes) -> None:
        """上传 blob。失败抛出 BlobStorageError，由调用方处理。"""
        ...

    @abstractmethod
    async def open_stream(self, key: str) -> StreamHandle:
        """异步打开流, 返回 StreamHandle.

        认证、文件打开、HTTP stream __aenter__ 都在此 await 完成.
        失败时抛 BlobStorageError, 由路由 try/except 捕获映射为 502.
        """
        ...

    @abstractmethod
    async def delete(self, key: str) -> None:
        """删除 blob，幂等：对象不存在不报错。"""
        ...

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """检查 blob 是否存在。"""
        ...

    async def aclose(self) -> None:
        """释放后端持有资源 (如 httpx.AsyncClient). 默认空实现, 子类按需覆盖."""
        ...


class _LocalStreamHandle(StreamHandle):
    """本地文件流句柄.

    持有已打开的文件对象, iter_bytes 按块读取, close 幂等关闭.
    所有文件 I/O 通过 asyncio.to_thread 卸载到线程池.
    iter_bytes 不做 open, 不做 close (close 由上层 finally 调用).
    """

    def __init__(self, file_obj: BufferedReader) -> None:
        self._file = file_obj
        self._closed = False

    async def iter_bytes(self) -> AsyncIterator[bytes]:
        """按 _CHUNK_SIZE 块读取文件并 yield. 不做 open, 不做 close."""
        while True:
            chunk = await asyncio.to_thread(self._file.read, _CHUNK_SIZE)
            if not chunk:
                break
            yield chunk

    async def close(self) -> None:
        """幂等关闭文件. 多次调用不报错."""
        if self._closed:
            return
        self._closed = True
        await asyncio.to_thread(self._file.close)


class _CSBStreamHandle(StreamHandle):
    """CSB HTTP 流句柄.

    持有已 __aenter__ 的 httpx stream context manager 和 response.
    iter_bytes 遍历 resp.aiter_bytes(), close 幂等调用 cm.__aexit__.
    iter_bytes 不做认证, 不做 stream 启动.
    """

    def __init__(
        self,
        cm: AbstractAsyncContextManager[httpx.Response],
        resp: httpx.Response,
    ) -> None:
        self._cm = cm
        self._resp = resp
        self._closed = False

    async def iter_bytes(self) -> AsyncIterator[bytes]:
        """yield HTTP 响应字节块. 不做认证, 不做 stream 启动."""
        async for chunk in self._resp.aiter_bytes():
            yield chunk

    async def close(self) -> None:
        """幂等关闭 HTTP stream. 多次调用不报错."""
        if self._closed:
            return
        self._closed = True
        await self._cm.__aexit__(None, None, None)


class CSBBlobStorage(BlobStorage):
    """华为 CSB 网关存储

    三步认证流程（对齐 traj_archiver/csb_storage.py）:
      1. endpoint 解析: GET {endpoint}?bucketid=&token=&vendor=&region= → file_server
      2. bucket 认证:   GET {file_server}/rest/boto3/s3/bucket-auth?...
      3. 对象操作:      PUT/GET/DELETE {file_server}/rest/boto3/s3/{vendor}/{region}/{token}/{bucket}/{b64key}

    认证延迟到首次操作时执行（async 无法在 __init__ 中做 I/O），
    结果缓存在 self._file_server，后续操作直接复用。
    aclose 释放 httpx.AsyncClient，供 Worker shutdown 调用。
    """

    def __init__(
        self,
        app_token: str,
        bucket: str,
        endpoint: str,
        vendor: str = _DEFAULT_VENDOR,
        region: str = _DEFAULT_REGION,
        verify_tls: bool = True,
    ) -> None:
        if not app_token:
            raise BlobStorageError("CSB app_token 不能为空")
        if not bucket:
            raise BlobStorageError("CSB bucket 不能为空")
        if not endpoint:
            raise BlobStorageError("CSB endpoint 不能为空")
        self._app_token = app_token
        self._bucket = bucket
        self._endpoint = endpoint
        self._vendor = vendor
        self._region = region
        self._file_server: Optional[str] = None
        self._auth_lock = asyncio.Lock()
        # trust_env=False 禁用代理（对齐 reference 的 session.trust_env=False）
        # verify_tls 可配置，默认 True（生产环境应启用 TLS 校验）
        self._verify_tls = verify_tls
        self._client = httpx.AsyncClient(
            trust_env=False,
            verify=verify_tls,
            timeout=httpx.Timeout(_REQUEST_TIMEOUT),
        )
        logger.info(
            f"CSBBlobStorage 初始化: bucket={bucket}, endpoint={endpoint}, "
            f"vendor={vendor}, region={region}"
        )

    async def _ensure_authenticated(self) -> None:
        """延迟执行三步认证的前两步（endpoint resolve + bucket auth），结果缓存。"""
        if self._file_server is not None:
            return
        async with self._auth_lock:
            if self._file_server is not None:
                return
            file_server = await self._resolve_endpoint()
            await self._bucket_auth(file_server)
            self._file_server = file_server

    async def _get_json(self, url: str, *, error_context: str) -> dict:
        """GET 请求并解析 JSON，统一处理 HTTP/JSON 错误。"""
        try:
            resp = await self._client.get(url)
        except httpx.HTTPError as e:
            # 脱敏: httpx 异常 repr 可能含 URL (含 app_token), 只保留异常类名
            raise BlobStorageError(
                f"{error_context} 请求失败: {type(e).__name__}"
            ) from e
        if resp.status_code != 200:
            raise BlobStorageError(
                f"{error_context} HTTP {resp.status_code}: {self._redact_token(resp.text[:200])}"
            )
        try:
            return resp.json()
        except json.JSONDecodeError as e:
            raise BlobStorageError(f"{error_context} 响应非 JSON: {e}") from e

    async def _resolve_endpoint(self) -> str:
        """步骤 1: 调用 CSB endpoint API 解析实际文件服务器地址。"""
        url = (
            f"{self._endpoint}?bucketid={self._bucket}"
            f"&token={self._app_token}&vendor={self._vendor}&region={self._region}"
        )
        result = await self._get_json(url, error_context="CSB endpoint 解析")
        if not result.get("success"):
            raise BlobStorageError(f"CSB endpoint 解析失败: {result.get('msg')}")
        file_server = result["result"]
        logger.info(f"CSB endpoint 已解析: {file_server}")
        return file_server

    async def _bucket_auth(self, file_server: str) -> None:
        """步骤 2: 验证 bucket 认证。"""
        url = (
            f"{file_server}/rest/boto3/s3/bucket-auth?"
            f"vendor={self._vendor}&region={self._region}"
            f"&bucketid={self._bucket}&apptoken={self._app_token}"
        )
        result = await self._get_json(url, error_context="CSB bucket 认证")
        if not result.get("success"):
            raise BlobStorageError(f"CSB bucket 认证失败: {result.get('msg')}")
        logger.info(f"CSB bucket 认证通过: {self._bucket}")

    def _encode_key(self, key: str) -> str:
        """base64 url-safe 编码对象键（对齐 reference 的 _encode_key）。"""
        return base64.urlsafe_b64encode(key.encode("utf-8")).decode("ascii")

    def _redact_token(self, text: str) -> str:
        """P2-2: 脱敏 app_token，防止 CSB 响应 body 回显时日志泄漏凭证。

        CSB 请求 URL 含 token query string，服务端错误响应理论上可能回显请求 URL；
        对所有 resp.text 输出做 token 替换，确保日志不含明文凭证。
        """
        if self._app_token and self._app_token in text:
            return text.replace(self._app_token, "***")
        return text

    def _build_object_url(self, encoded_key: str) -> str:
        """步骤 3 的 URL: 构造对象操作地址（PUT/GET/DELETE 共用）。"""
        return (
            f"{self._file_server}/rest/boto3/s3/{self._vendor}/{self._region}/"
            f"{self._app_token}/{self._bucket}/{encoded_key}"
        )

    def _invalidate_auth_on_failure(self, status_code: int) -> None:
        """对象操作收到认证失败或服务端错误时清除缓存的 file_server，
        下次操作触发重新认证（应对 CSB token 过期 / 服务端 failover）。
        """
        if status_code in (401, 403) or 500 <= status_code < 600:
            if self._file_server is not None:
                logger.warning(
                    f"CSB 对象操作收到 {status_code}，清除认证缓存以触发重新认证"
                )
                self._file_server = None

    async def put(self, key: str, data: bytes) -> None:
        """步骤 3 (PUT): 上传 blob。"""
        await self._ensure_authenticated()
        encoded_key = self._encode_key(key)
        url = self._build_object_url(encoded_key)
        headers = {
            "Content-Type": "application/json",
            "csb-token": self._app_token,
            "Connection": "close",
        }
        try:
            resp = await self._client.put(url, content=data, headers=headers)
        except httpx.HTTPError as e:
            # 脱敏: httpx 异常 repr 可能含 URL (含 app_token), 只保留异常类名
            raise BlobStorageError(
                f"CSB PUT 请求失败: key={key}, error={type(e).__name__}"
            ) from e
        if resp.status_code != 200:
            self._invalidate_auth_on_failure(resp.status_code)
            raise BlobStorageError(
                f"CSB PUT 失败: key={key}, status={resp.status_code}, "
                f"body={self._redact_token(resp.text[:200])}"
            )
        logger.info(f"CSB 已上传: key={key} ({len(data)} bytes)")

    async def open_stream(self, key: str) -> StreamHandle:
        """步骤 3 (GET): 异步打开流, 返回 _CSBStreamHandle.

        认证、HTTP stream __aenter__ 在此 await 完成.
        失败时抛 BlobStorageError, 由路由 try/except 捕获映射为 502.
        """
        await self._ensure_authenticated()
        encoded_key = self._encode_key(key)
        url = self._build_object_url(encoded_key)
        headers = {"csb-token": self._app_token}
        # P2-4: cm.__aenter__ 失败时（HTTPError），httpx 内部 send 失败会关闭 response
        # 并把 connection 回收到 pool，cm 对象无需显式 __aexit__（未 __aenter__ 成功的
        # context manager 调 __aexit__ 不安全）。若后续发现 httpx 版本泄漏，改用
        # client.send(request, stream=True) 直接管理 response 生命周期。
        try:
            cm = self._client.stream("GET", url, headers=headers)
            resp = await cm.__aenter__()
        except httpx.HTTPError as e:
            # 脱敏: httpx 异常 repr 可能含 URL (含 app_token), 只保留异常类名
            raise BlobStorageError(
                f"CSB GET 流式请求失败: key={key}, error={type(e).__name__}"
            ) from e
        if resp.status_code != 200:
            # 非 200: 读取错误 body 后退出 context, 同步抛异常
            # aread 失败不应阻断 cm.__aexit__ 资源释放; body 退化为占位符
            self._invalidate_auth_on_failure(resp.status_code)
            try:
                await resp.aread()
                body: str = self._redact_token(resp.text[:200])
            except Exception:
                body = "<unreadable>"
            finally:
                await cm.__aexit__(None, None, None)
            raise BlobStorageError(
                f"CSB GET 失败: key={key}, status={resp.status_code}, body={body}"
            )
        logger.info(f"CSB 已打开流: key={key}")
        return _CSBStreamHandle(cm, resp)

    async def delete(self, key: str) -> None:
        """步骤 3 (DELETE): 删除 blob，幂等（404 视为已删除）。"""
        await self._ensure_authenticated()
        encoded_key = self._encode_key(key)
        url = self._build_object_url(encoded_key)
        headers = {"csb-token": self._app_token}
        try:
            resp = await self._client.delete(url, headers=headers)
        except httpx.HTTPError as e:
            # 脱敏: httpx 异常 repr 可能含 URL (含 app_token), 只保留异常类名
            raise BlobStorageError(
                f"CSB DELETE 请求失败: key={key}, error={type(e).__name__}"
            ) from e
        if resp.status_code == 404:
            logger.info(f"CSB 对象不存在（删除幂等）: key={key}")
            return
        if resp.status_code not in (200, 204):
            self._invalidate_auth_on_failure(resp.status_code)
            raise BlobStorageError(
                f"CSB DELETE 失败: key={key}, status={resp.status_code}, "
                f"body={self._redact_token(resp.text[:200])}"
            )
        logger.info(f"CSB 已删除: key={key}")

    async def exists(self, key: str) -> bool:
        """通过 CSB 元数据接口检查对象是否存在。"""
        await self._ensure_authenticated()
        encoded_key = self._encode_key(key)
        url = (
            f"{self._file_server}/rest/boto3/s3/object/metadata?"
            f"vendor={self._vendor}&region={self._region}"
            f"&bucketid={self._bucket}&apptoken={self._app_token}"
            f"&objectkey={encoded_key}"
        )
        try:
            resp = await self._client.get(url)
        except httpx.HTTPError as e:
            # 脱敏: httpx 异常 repr 可能含 URL (含 app_token), 只保留异常类名
            logger.warning(
                f"CSB exists 检查请求失败: key={key}, error={type(e).__name__}"
            )
            return False
        if resp.status_code != 200:
            self._invalidate_auth_on_failure(resp.status_code)
            return False
        try:
            result = resp.json()
        except json.JSONDecodeError:
            return False
        return bool(result.get("success", False))

    async def aclose(self) -> None:
        """释放 httpx.AsyncClient, 供 Worker shutdown 调用 (修复 P2-#8 资源泄漏)."""
        await self._client.aclose()
        logger.info("CSBBlobStorage httpx.AsyncClient 已关闭")


class LocalDiskBlobStorage(BlobStorage):
    """本地盘存储实现

    写 {write_path}/{key}，流式读用文件流。所有文件 I/O 通过 asyncio.to_thread
    卸载到线程池，避免阻塞事件循环（项目无 aiofiles 依赖，与 tokenizer_cache /
    routes 的 to_thread 模式一致）。
    """

    def __init__(self, write_path: str) -> None:
        self._root = Path(write_path)
        self._root.mkdir(parents=True, exist_ok=True)
        logger.info(f"LocalDiskBlobStorage 初始化: write_path={self._root}")

    async def put(self, key: str, data: bytes) -> None:
        """写入 {root}/{key}，自动创建父目录。"""
        dest = self._root / key
        parent = dest.parent

        def _write() -> None:
            parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)

        try:
            await asyncio.to_thread(_write)
        except OSError as e:
            raise BlobStorageError(
                f"本地写入失败: key={key}, path={dest}, error={e}"
            ) from e
        logger.info(f"本地已写入: key={key} ({len(data)} bytes)")

    async def open_stream(self, key: str) -> StreamHandle:
        """异步打开 {root}/{key} 文件, 返回 _LocalStreamHandle.

        文件 open 在此 await 完成. 失败抛 BlobStorageError, 由路由捕获映射为 502.
        """
        path = self._root / key
        try:
            f = await asyncio.to_thread(open, path, "rb")
        except OSError as e:
            raise BlobStorageError(
                f"本地读取打开失败: key={key}, path={path}, error={e}"
            ) from e
        logger.info(f"本地已打开流: key={key}")
        return _LocalStreamHandle(f)

    async def delete(self, key: str) -> None:
        """删除 {root}/{key}，幂等（missing_ok=True）。"""
        path = self._root / key
        try:
            await asyncio.to_thread(path.unlink, missing_ok=True)
        except OSError as e:
            raise BlobStorageError(
                f"本地删除失败: key={key}, path={path}, error={e}"
            ) from e
        logger.info(f"本地已删除: key={key}")

    async def exists(self, key: str) -> bool:
        """检查 {root}/{key} 是否存在。"""
        path = self._root / key
        return await asyncio.to_thread(path.exists)


def create_blob_storage(config: dict) -> BlobStorage:
    """工厂函数：按 route_experts_offload 配置选择存储后端

    Args:
        config: route_experts_offload 配置字典，需含 backend 字段
                backend=="csb" → CSBBlobStorage（读 config["csb"]）
                backend=="local" → LocalDiskBlobStorage（读 config["local"]）

    Returns:
        BlobStorage 实例

    Raises:
        BlobStorageError: backend 未知时抛出
    """
    backend = config.get("backend", "local")
    if backend == "csb":
        csb_cfg = config.get("csb", {})
        return CSBBlobStorage(
            app_token=csb_cfg.get("app_token", ""),
            bucket=csb_cfg.get("bucket", ""),
            endpoint=csb_cfg.get("endpoint", ""),
            vendor=csb_cfg.get("vendor", _DEFAULT_VENDOR),
            region=csb_cfg.get("region", _DEFAULT_REGION),
            verify_tls=csb_cfg.get("verify_tls", True),
        )
    if backend == "local":
        local_cfg = config.get("local", {})
        write_path = local_cfg.get("write_path", "/data/r3")
        return LocalDiskBlobStorage(write_path=write_path)
    raise BlobStorageError(f"未知的 blob storage backend: {backend}")
