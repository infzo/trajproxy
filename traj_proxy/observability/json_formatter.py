"""
JSON 格式日志输出器

通过 LOG_FORMAT=json 环境变量启用。
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any


class JsonFormatter(logging.Formatter):
    """JSON 格式日志输出器"""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "module": record.module,
            "worker_id": getattr(record, "worker_id", "-"),
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None)
        if request_id and request_id != "-":
            log_entry["request_id"] = request_id
        run_id = getattr(record, "run_id", None)
        if run_id and run_id != "-":
            log_entry["run_id"] = run_id
        unique_id = getattr(record, "unique_id", None)
        if unique_id and unique_id != "-":
            log_entry["unique_id"] = unique_id
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)