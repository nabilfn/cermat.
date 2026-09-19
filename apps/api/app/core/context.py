"""Per-request context (request id, user, workspace) for logs and audit events."""

from __future__ import annotations

from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)
workspace_id_var: ContextVar[str | None] = ContextVar("workspace_id", default=None)
route_var: ContextVar[str | None] = ContextVar("route", default=None)
