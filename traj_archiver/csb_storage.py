"""
CSB (Cloud Service Broker) 存储模块 - 华为云 CSB 网关原生 REST API

CSB 网关不是标准 S3 协议，boto3 无法可靠对接。
本模块使用 requests 库直接调用 CSB REST API，与官方上传脚本协议一致。

上传协议:
  1. endpoint 解析: GET {endpoint_url}?bucketid={bucket}&token={app_token}&vendor={vendor}&region={region}
  2. bucket 认证:   GET {file_server}/rest/boto3/s3/bucket-auth?...
  3. 文件上传:      PUT {file_server}/rest/boto3/s3/{vendor}/{region}/{app_token}/{bucket}/{base64_key}
"""

import base64
import json
import logging
import ssl
import tempfile
import time
from pathlib import Path
from urllib import request as urllib_request

import requests

logger = logging.getLogger(__name__)

# 上传重试次数
MAX_RETRIES = 3
# 请求超时（秒）
REQUEST_TIMEOUT = 120


class CSBStorage:
    """华为云 CSB 网关存储操作封装

    使用 CSB 原生 REST API，不走 boto3。
    archive_location 格式: csb://{bucket}/{prefix}{key}
    """

    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        endpoint_url: str = "",
        app_token: str = "",
        vendor: str = "HEC",
        region: str = "",
    ):
        self.bucket = bucket
        self.prefix = prefix.rstrip("/") + "/" if prefix else ""
        self.app_token = app_token
        self.vendor = vendor
        self.region = region

        # 禁用代理
        self.session = requests.Session()
        self.session.trust_env = False

        # 解析实际文件服务器地址
        self.file_server = self._resolve_endpoint(endpoint_url)
        # 验证 bucket 认证
        self._bucket_auth()

        logger.info(
            f"CSBStorage 初始化: bucket={bucket}, prefix={self.prefix}, "
            f"vendor={vendor}, region={region}, "
            f"file_server={self.file_server}"
        )

    def _resolve_endpoint(self, endpoint_url: str) -> str:
        """调用 CSB endpoint API 解析实际文件服务器地址"""
        url = (
            f"{endpoint_url}?"
            f"bucketid={self.bucket}"
            f"&token={self.app_token}"
            f"&vendor={self.vendor}"
            f"&region={self.region}"
        )
        ctx = ssl._create_unverified_context()
        req = urllib_request.Request(url=url)
        resp = urllib_request.urlopen(req, context=ctx, timeout=60)
        result = resp.read().decode("utf-8")
        result_dict = json.loads(result)
        if not result_dict.get("success"):
            raise RuntimeError(f"CSB endpoint 解析失败: {result_dict.get('msg')}")

        file_server = result_dict["result"]
        logger.info(f"CSB endpoint 已解析: {file_server}")
        return file_server

    def _bucket_auth(self):
        """验证 bucket 认证"""
        url = (
            f"{self.file_server}/rest/boto3/s3/bucket-auth?"
            f"vendor={self.vendor}"
            f"&region={self.region}"
            f"&bucketid={self.bucket}"
            f"&apptoken={self.app_token}"
        )
        resp = self.session.get(url, timeout=REQUEST_TIMEOUT, verify=False)
        result = resp.json()
        if not result.get("success"):
            raise RuntimeError(f"CSB bucket 认证失败: {result.get('msg')}")
        logger.info(f"CSB bucket 认证通过: {self.bucket}")

    def _encode_key(self, key: str) -> str:
        """base64 url-safe 编码对象键"""
        return base64.urlsafe_b64encode(key.encode("utf-8")).decode("utf-8")

    def _build_upload_url(self, encoded_key: str) -> str:
        """构造上传 URL"""
        return (
            f"{self.file_server}/rest/boto3/s3/"
            f"{self.vendor}/{self.region}/{self.app_token}/"
            f"{self.bucket}/{encoded_key}"
        )

    def upload(self, local_path: Path, key: str) -> str:
        full_key = f"{self.prefix}{key}"
        encoded_key = self._encode_key(full_key)
        url = self._build_upload_url(encoded_key)
        file_size = local_path.stat().st_size
        headers = {
            "Content-Type": "application/json",
            "csb-token": self.app_token,
            "Connection": "close",
        }

        last_error = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                with open(local_path, "rb") as f:
                    resp = self.session.put(
                        url, data=f, headers=headers, timeout=REQUEST_TIMEOUT, verify=False,
                    )
                if resp.status_code == 200:
                    location = f"csb://{self.bucket}/{full_key}"
                    logger.info(f"已上传: {local_path.name} ({file_size} bytes) → {location}")
                    return location
                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                logger.warning(
                    f"上传失败 (尝试 {attempt}/{MAX_RETRIES}): "
                    f"{local_path.name} → {last_error}"
                )
            except Exception as e:
                last_error = str(e)
                logger.warning(f"上传异常 (尝试 {attempt}/{MAX_RETRIES}): {e}")

            if attempt < MAX_RETRIES:
                time.sleep(2 * attempt)

        raise RuntimeError(
            f"上传失败（已重试 {MAX_RETRIES} 次）: {local_path.name} → {last_error}"
        )

    def download(self, key: str, local_path: Path) -> Path:
        full_key = key.split("://", 1)[-1] if "://" in key else f"{self.prefix}{key}"
        # 去掉 bucket 前缀（如果有）
        if full_key.startswith(f"{self.bucket}/"):
            full_key = full_key[len(self.bucket) + 1:]
        elif not full_key.startswith(self.prefix):
            full_key = f"{self.prefix}{key}"

        encoded_key = self._encode_key(full_key)
        url = self._build_upload_url(encoded_key)
        headers = {
            "csb-token": self.app_token,
        }

        resp = self.session.get(url, headers=headers, timeout=REQUEST_TIMEOUT, verify=False)
        if resp.status_code != 200:
            raise RuntimeError(f"下载失败: HTTP {resp.status_code}: {resp.text[:200]}")

        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(resp.content)
        logger.info(f"已下载: csb://{self.bucket}/{full_key} → {local_path}")
        return local_path

    def exists(self, key: str) -> bool:
        full_key = key.split("://", 1)[-1] if "://" in key else f"{self.prefix}{key}"
        if full_key.startswith(f"{self.bucket}/"):
            full_key = full_key[len(self.bucket) + 1:]

        encoded_key = self._encode_key(full_key)
        # 使用 CSB 元数据接口
        url = (
            f"{self.file_server}/rest/boto3/s3/object/metadata?"
            f"vendor={self.vendor}"
            f"&region={self.region}"
            f"&bucketid={self.bucket}"
            f"&apptoken={self.app_token}"
            f"&objectkey={encoded_key}"
        )
        try:
            resp = self.session.get(url, timeout=REQUEST_TIMEOUT, verify=False)
            result = resp.json()
            return result.get("success", False)
        except Exception:
            return False

    def validate(self) -> None:
        """上传探测文件验证 CSB 可写"""
        probe_key = f"{self.prefix}.probe/archiver_startup_check"
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f:
            f.write("archiver csb startup probe")
            probe_path = Path(f.name)

        try:
            location = self.upload(probe_path, ".probe/archiver_startup_check")
            logger.info(f"CSB 上传验证成功: {location}")
            # 清理探测文件
            full_key = f"{self.prefix}.probe/archiver_startup_check"
            encoded_key = self._encode_key(full_key)
            url = self._build_upload_url(encoded_key)
            try:
                self.session.delete(
                    url,
                    headers={"csb-token": self.app_token},
                    timeout=REQUEST_TIMEOUT,
                    verify=False,
                )
            except Exception:
                logger.warning("清理探测文件失败（不影响运行）")
        finally:
            probe_path.unlink(missing_ok=True)
