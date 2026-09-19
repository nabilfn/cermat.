"""Request id, latency logging and request-size limits (pure ASGI, stream-safe)."""

from __future__ import annotations

import logging
import re
import time
import uuid

from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import settings
from app.core.context import request_id_var, route_var, user_id_var, workspace_id_var
from app.core.errors import error_body

logger = logging.getLogger("cermat.request")
_INBOUND_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
JSON_BODY_LIMIT = 1024 * 1024  # 1 MB for everything except document uploads


def _body_limit(path: str, method: str) -> int:
    if method == "POST" and path.rstrip("/") == "/api/v1/documents":
        return settings.max_upload_bytes + 256 * 1024  # multipart overhead
    return JSON_BODY_LIMIT


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        inbound = headers.get(b"x-request-id", b"").decode("latin-1")
        request_id = inbound if _INBOUND_ID.match(inbound) else uuid.uuid4().hex
        tokens = [
            request_id_var.set(request_id),
            user_id_var.set(None),
            workspace_id_var.set(None),
            route_var.set(scope.get("path")),
        ]
        started = time.perf_counter()
        status_holder = {"status": 500}

        # Reject oversized bodies up front when Content-Length is declared …
        limit = _body_limit(scope.get("path", ""), scope.get("method", "GET"))
        declared = headers.get(b"content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            await self._reject(send, request_id)
            self._log(scope, 413, started)
            self._reset(tokens)
            return

        # … and count streamed bytes when it is not.
        received = {"bytes": 0}

        async def limited_receive() -> Message:
            message = await receive()
            if message["type"] == "http.request":
                received["bytes"] += len(message.get("body", b""))
                if received["bytes"] > limit:
                    raise _BodyTooLarge()
            return message

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                message.setdefault("headers", [])
                message["headers"].append((b"x-request-id", request_id.encode()))
            await send(message)

        try:
            await self.app(scope, limited_receive, send_with_id)
        except _BodyTooLarge:
            await self._reject(send, request_id)
            status_holder["status"] = 413
        finally:
            route = scope.get("route")
            if route is not None and getattr(route, "path", None):
                route_var.set(route.path)
            self._log(scope, status_holder["status"], started)
            self._reset(tokens)

    @staticmethod
    async def _reject(send: Send, request_id: str) -> None:
        import json

        body = json.dumps(
            error_body("UPLOAD_TOO_LARGE", f"Request body is too large (max {settings.max_upload_mb} MB for uploads).")
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"x-request-id", request_id.encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    def _log(scope: Scope, status: int, started: float) -> None:
        if scope.get("path") in {"/health", "/ready"} and status < 400:
            return
        logger.info(
            "request",
            extra={
                "event": "request",
                "method": scope.get("method"),
                "status": status,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )

    @staticmethod
    def _reset(tokens: list) -> None:
        for var, token in zip((request_id_var, user_id_var, workspace_id_var, route_var), tokens, strict=True):
            var.reset(token)


class _BodyTooLarge(StarletteHTTPException):
    """An HTTPException so FastAPI's body parsing re-raises it instead of turning it into a 400."""

    def __init__(self) -> None:
        super().__init__(413, f"Request body is too large (max {settings.max_upload_mb} MB for uploads).")
