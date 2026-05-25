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

logger = logging.getLogger(__name__)


class S3Storage:
    """S3 存储操作封装"""

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
        logger.info(
            f"S3Storage 初始化: bucket={bucket}, prefix={self.prefix}, "
            f"endpoint={'AWS' if not endpoint_url else endpoint_url}"
        )

    def upload_file(self, local_path: Path, s3_key: Optional[str] = None) -> str:
        """上传文件到 S3

        Args:
            local_path: 本地文件路径
            s3_key: S3 对象键（不含前缀），默认使用文件名

        Returns:
            完整的 S3 URI
        """
        if s3_key is None:
            s3_key = local_path.name

        full_key = f"{self.prefix}{s3_key}"
        self.client.upload_file(str(local_path), self.bucket, full_key)

        s3_uri = f"s3://{self.bucket}/{full_key}"
        logger.info(f"已上传: {local_path.name} → {s3_uri}")
        return s3_uri

    def download_file(self, s3_key: str, local_path: Path) -> Path:
        """从 S3 下载文件

        Args:
            s3_key: S3 对象键（不含前缀）
            local_path: 本地保存路径

        Returns:
            本地文件路径
        """
        full_key = f"{self.prefix}{s3_key}"
        local_path.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, full_key, str(local_path))
        logger.info(f"已下载: s3://{self.bucket}/{full_key} → {local_path}")
        return local_path

    def list_archives(self) -> List[str]:
        """列出 S3 上的归档文件

        Returns:
            归档文件键名列表
        """
        keys = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        return keys

    def file_exists(self, s3_key: str) -> bool:
        """检查 S3 上文件是否存在

        Args:
            s3_key: S3 对象键（不含前缀）

        Returns:
            是否存在
        """
        full_key = f"{self.prefix}{s3_key}"
        try:
            self.client.head_object(Bucket=self.bucket, Key=full_key)
            return True
        except Exception:
            return False
