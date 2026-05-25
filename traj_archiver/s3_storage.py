"""
S3 存储模块 - 归档文件上传/下载

兼容 AWS S3 / MinIO / Ceph 等标准 S3 协议存储，
同时支持华为云 CSB 等非标 S3 网关的 token 认证。

凭证方式（按优先级）：
  1. YAML s3.app_token → CSB token 认证（unsigned + csb-token 头）
  2. 环境变量 CSB_APP_TOKEN → 同上
  3. YAML s3.access_key / s3.secret_key → 显式 AK/SK
  4. 环境变量 AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY → boto3 默认链
  5. IAM 角色（EC2/ECS/EKS 自动获取）
"""

import logging
from pathlib import Path
from typing import List, Optional

import boto3
from botocore.config import Config as BotoConfig
from botocore import UNSIGNED

from traj_archiver.storage import Storage

logger = logging.getLogger(__name__)


class S3Storage(Storage):
    """S3 存储操作封装

    实现 Storage 统一接口，archive_location 为 s3:// URI。

    app_token 非空时自动切换为 CSB 网关模式（跳过 SigV4，注入 csb-token 请求头）。
    """

    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        endpoint_url: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        session_token: Optional[str] = None,
        app_token: Optional[str] = None,
        region: Optional[str] = None,
        verify_ssl: bool = True,
    ):
        self.bucket = bucket
        self.prefix = prefix.rstrip("/") + "/" if prefix else ""
        self.endpoint_url = endpoint_url
        self.app_token = app_token

        client_kwargs = {}
        boto_config_kwargs = {
            "retries": {"max_attempts": 3, "mode": "standard"},
        }

        if endpoint_url:
            client_kwargs["endpoint_url"] = endpoint_url
        if access_key:
            client_kwargs["aws_access_key_id"] = access_key
            client_kwargs["aws_secret_access_key"] = secret_key
        if session_token:
            client_kwargs["aws_session_token"] = session_token
        if region:
            client_kwargs["region_name"] = region
        if not verify_ssl:
            client_kwargs["verify"] = False
            boto_config_kwargs["signature_version"] = UNSIGNED  # https + verify=False 组合要求
            # 禁用 SSL 警告
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            logger.info("SSL 证书校验已关闭")

        # 构造 botocore config
        if app_token:
            boto_config_kwargs["signature_version"] = UNSIGNED

        self.client = boto3.client(
            "s3",
            config=BotoConfig(**boto_config_kwargs),
            **client_kwargs,
        )

        # CSB token 注入：所有请求自动附带 csb-token 头
        if app_token:

            def _inject_csb_token(request, **_kw):
                request.headers["csb-token"] = app_token

            self.client.meta.events.register(
                "request-created.s3.*", _inject_csb_token
            )

        # 确保 bucket 存在
        self._ensure_bucket()

        auth_info = "CSB-token" if app_token else ("AK/SK" if access_key else "boto3-default-chain")
        logger.info(
            f"S3Storage 初始化: bucket={bucket}, prefix={self.prefix}, "
            f"auth={auth_info}, "
            f"endpoint={'AWS' if not endpoint_url else endpoint_url}"
        )

    def _ensure_bucket(self):
        """确保 bucket 存在，不存在则创建"""
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception:
            try:
                self.client.create_bucket(Bucket=self.bucket)
                logger.info(f"已创建 bucket: {self.bucket}")
            except Exception as e:
                logger.warning(f"创建 bucket 失败（可能已存在）: {e}")

    def upload(self, local_path: Path, key: str) -> str:
        full_key = f"{self.prefix}{key}"
        self.client.upload_file(str(local_path), self.bucket, full_key)
        s3_uri = f"s3://{self.bucket}/{full_key}"
        logger.info(f"已上传: {local_path.name} → {s3_uri}")
        return s3_uri

    def _parse_key(self, key: str) -> tuple:
        """解析 key，支持 s3:// URI 或纯文件名

        Returns:
            (bucket, full_key)
        """
        if key.startswith("s3://"):
            path = key[5:]  # 去掉 s3://
            parts = path.split("/", 1)
            bucket = parts[0]
            full_key = parts[1] if len(parts) > 1 else ""
            return bucket, full_key
        return self.bucket, f"{self.prefix}{key}"

    def download(self, key: str, local_path: Path) -> Path:
        bucket, full_key = self._parse_key(key)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(bucket, full_key, str(local_path))
        logger.info(f"已下载: s3://{bucket}/{full_key} → {local_path}")
        return local_path

    def exists(self, key: str) -> bool:
        bucket, full_key = self._parse_key(key)
        try:
            self.client.head_object(Bucket=bucket, Key=full_key)
            return True
        except Exception:
            return False

    def list_archives(self) -> List[str]:
        keys = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        return keys
