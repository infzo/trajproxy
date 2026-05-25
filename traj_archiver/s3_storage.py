"""
S3 存储模块 - 归档文件上传/下载

兼容 AWS S3 和 MinIO/Ceph 等 S3 协议存储。
凭证通过标准 AWS 环境变量配置。
"""

import logging
from pathlib import Path
from typing import List, Optional

import boto3
from botocore.config import Config as BotoConfig

from traj_archiver.storage import Storage

logger = logging.getLogger(__name__)


class S3Storage(Storage):
    """S3 存储操作封装

    实现 Storage 统一接口，archive_location 为 s3:// URI。
    """

    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        endpoint_url: Optional[str] = None,
    ):
        self.bucket = bucket
        self.prefix = prefix.rstrip("/") + "/" if prefix else ""
        self.endpoint_url = endpoint_url

        client_kwargs = {}
        if endpoint_url:
            client_kwargs["endpoint_url"] = endpoint_url

        self.client = boto3.client(
            "s3",
            config=BotoConfig(
                retries={"max_attempts": 3, "mode": "standard"},
            ),
            **client_kwargs,
        )

        # 确保 bucket 存在
        self._ensure_bucket()

        logger.info(
            f"S3Storage 初始化: bucket={bucket}, prefix={self.prefix}, "
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
