"""Structured JSON logging with request context. No secrets, bodies or documents."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

from app.core.context import request_id_var, route_var, user_id_var, workspace_id_var

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
            "route": route_var.get(),
            "user_id": user_id_var.get(),
            "workspace_id": workspace_id_var.get(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps({k: v for k, v in payload.items() if v is not None}, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # Our middleware logs requests; uvicorn's access log would duplicate them.
    logging.getLogger("uvicorn.access").disabled = True
    for noisy in ("httpx", "httpcore", "openai", "botocore", "boto3", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
