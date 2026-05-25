"""
S3 存储模块 - 标准 S3 协议（AWS / MinIO / Ceph）

凭证方式：显式 AK/SK → boto3 默认链 → IAM 角色
"""

import logging
from pathlib import Path

import boto3
from botocore.config import Config as BotoConfig

logger = logging.getLogger(__name__)


class S3Storage:
    """标准 S3 存储，archive_location 为 s3:// URI"""

    def __init__(self, bucket, prefix="", endpoint_url=None,
                 access_key=None, secret_key=None, session_token=None,
                 region=None, verify_ssl=True):
        self.bucket = bucket
        self.prefix = prefix.rstrip("/") + "/" if prefix else ""

        client_kwargs = {}
        config_kwargs = {"retries": {"max_attempts": 3, "mode": "standard"}}

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
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            logger.info("SSL 证书校验已关闭")

        self.client = boto3.client("s3", config=BotoConfig(**config_kwargs), **client_kwargs)
        self._ensure_bucket()

        auth = "AK/SK" if access_key else "boto3-default"
        logger.info(f"S3Storage 初始化: bucket={bucket}, prefix={self.prefix}, auth={auth}")

    def _ensure_bucket(self):
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

    def download(self, key: str, local_path: Path) -> Path:
        bucket, full_key = self._resolve_key(key)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(bucket, full_key, str(local_path))
        logger.info(f"已下载: s3://{bucket}/{full_key} → {local_path}")
        return local_path

    def exists(self, key: str) -> bool:
        bucket, full_key = self._resolve_key(key)
        try:
            self.client.head_object(Bucket=bucket, Key=full_key)
            return True
        except Exception:
            return False

    def validate(self) -> None:
        import tempfile
        probe_key = f"{self.prefix}.probe/archiver_startup_check"
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f:
            f.write("archiver s3 startup probe")
            probe_path = Path(f.name)
        try:
            self.client.upload_file(str(probe_path), self.bucket, probe_key)
            logger.info(f"S3 上传验证成功: s3://{self.bucket}/{probe_key}")
            try:
                self.client.delete_object(Bucket=self.bucket, Key=probe_key)
            except Exception:
                logger.warning("清理探测文件失败（不影响运行）")
        finally:
            probe_path.unlink(missing_ok=True)

    def _resolve_key(self, key: str):
        """解析 s3:// URI 或纯 key → (bucket, full_key)"""
        if key.startswith("s3://"):
            path = key[5:]
            bucket, _, full_key = path.partition("/")
            return bucket, full_key
        return self.bucket, f"{self.prefix}{key}"
