"""Integration-test harness: a migrated throwaway database and an in-process API client."""

from __future__ import annotations

import asyncio
import io
import unittest
from typing import Any

import asyncpg
import httpx2 as httpx
from alembic import command
from alembic.config import Config

from app.config import settings
from app.core.ratelimit import limiter
from app.schemas import AIExtraction

API_ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
PASSWORD = "correct-horse-battery-7"


def _asyncpg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg", "postgresql")


def assert_test_database(url: str) -> str:
    name = url.rsplit("/", 1)[-1]
    if not name.endswith("_test"):
        raise RuntimeError(f"Refusing to reset non-test database {name!r}")
    return name


async def _recreate(url: str) -> bool:
    name = assert_test_database(url)
    admin_url = _asyncpg_dsn(url).rsplit("/", 1)[0] + "/postgres"
    try:
        admin = await asyncpg.connect(admin_url, timeout=3)
    except Exception:  # noqa: BLE001
        return False
    try:
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1 AND pid <> pg_backend_pid()",
            name,
        )
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    return True


def migrate(url: str, revision: str = "head") -> None:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    config.attributes["database_url"] = url
    command.upgrade(config, revision)


def fresh_database(url: str | None = None, revision: str = "head") -> bool:
    """Drop, recreate and migrate the test database. False if PostgreSQL is unreachable."""
    url = url or settings.database_url
    if not asyncio.run(_recreate(url)):
        return False
    migrate(url, revision)
    return True


def blank_pdf(pages: int = 1) -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5b0000000049454e44ae426082"
)


def extraction(kind: str, supplier: str = "ABC Supplies Sdn. Bhd.") -> AIExtraction:
    """Deterministic stand-in for the model: PO 10 @ 42, DO 8, INV 10 @ 44."""
    quantity = {"purchase_order": 10, "delivery_order": 8, "invoice": 10}[kind]
    price = {"purchase_order": 42.0, "delivery_order": None, "invoice": 44.0}[kind]
    number = {"purchase_order": "PO-T-1", "delivery_order": "DO-T-1", "invoice": "INV-T-1"}[kind]
    return AIExtraction(
        supplier_name=supplier,
        supplier_registration_no=None,
        document_number=number,
        document_date="2026-09-10",
        currency="MYR",
        subtotal=None,
        tax=None,
        total=quantity * price if price else None,
        line_items=[{"description": "Office Chair", "sku": "CHAIR-1", "quantity": quantity,
                     "unit_price": price, "line_total": quantity * price if price else None}],
        evidence=[
            {"field_path": "line_items.0.quantity", "source_text": f"Office Chair Qty {quantity}", "page": 1, "confidence": 0.93},
            {"field_path": "line_items.0.unit_price", "source_text": f"Unit Price RM{price}", "page": 1, "confidence": 0.91},
        ],
        overall_confidence=0.94,
        review_reasons=[],
    )


class Api:
    """One signed-in browser: cookie jar + CSRF + current workspace."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.csrf: str | None = None
        self.workspace_id: str | None = None
        self.user: dict | None = None

    def headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        if self.workspace_id:
            headers["X-Workspace-Id"] = self.workspace_id
        headers.update(extra or {})
        return headers

    async def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = self.headers(kwargs.pop("headers", None))
        return await self.client.request(method, path, headers=headers, **kwargs)

    async def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request("POST", path, **kwargs)

    async def patch(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request("PATCH", path, **kwargs)

    async def delete(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request("DELETE", path, **kwargs)

    async def sign_up(self, email: str, name: str = "Test User") -> httpx.Response:
        response = await self.client.post(
            "/api/v1/auth/signup", json={"email": email, "password": PASSWORD, "display_name": name}
        )
        if response.status_code == 200:
            self._adopt(response.json())
        return response

    async def sign_in(self, email: str, password: str = PASSWORD) -> httpx.Response:
        response = await self.client.post("/api/v1/auth/signin", json={"email": email, "password": password})
        if response.status_code == 200:
            self._adopt(response.json())
        return response

    def _adopt(self, info: dict) -> None:
        self.csrf = info["csrf_token"]
        self.user = info["user"]
        real = [w for w in info["workspaces"] if not w["is_demo"]]
        self.workspace_id = (real or info["workspaces"])[0]["id"]

    async def upload(self, kind: str, filename: str | None = None, data: bytes | None = None) -> httpx.Response:
        return await self.post(
            "/api/v1/documents",
            files={"file": (filename or f"{kind}.pdf", blank_pdf() if data is None else data, "application/pdf")},
            data={"document_type": kind},
        )

    async def reconciled_transaction(self, name: str = "PO-T-1", supplier: str = "ABC Supplies Sdn. Bhd.") -> dict:
        """Create → upload ×3 → extract (fake model) → attach → reconcile."""
        from unittest import mock

        import app.routers.documents as documents_router

        txn = (await self.post("/api/v1/transactions", json={"name": name})).json()
        document_ids = {}
        for kind in ("purchase_order", "delivery_order", "invoice"):
            doc = (await self.upload(kind)).json()
            with mock.patch.object(documents_router, "run_ai_extraction",
                                   lambda **kw: extraction(kw["document_type"].value, supplier)):
                extracted = await self.post(f"/api/v1/documents/{doc['id']}/extract")
                assert extracted.status_code == 200, extracted.text
            attached = await self.post(f"/api/v1/transactions/{txn['id']}/documents/{doc['id']}")
            assert attached.status_code == 200, attached.text
            document_ids[kind] = doc["id"]
        reconciled = await self.post(f"/api/v1/transactions/{txn['id']}/reconcile")
        assert reconciled.status_code == 200, reconciled.text
        return {"transaction": txn, "documents": document_ids, "reconciliation": reconciled.json()}


class ApiTestCase(unittest.IsolatedAsyncioTestCase):
    """Fresh migrated database per test class; one in-process client per browser."""

    database_available = False

    @classmethod
    def setUpClass(cls) -> None:
        cls.database_available = fresh_database()

    async def asyncSetUp(self) -> None:
        if not self.database_available:
            self.skipTest("PostgreSQL test database is not reachable")
        from app.database import engine
        from app.main import app
        from app.routers.ask import get_ask_model
        from app.routers.intelligence import get_brief_model

        await engine.dispose()  # new event loop per test → fresh pool
        await self._truncate()
        app.dependency_overrides[get_ask_model] = lambda: None
        app.dependency_overrides[get_brief_model] = lambda: None
        limiter.reset()
        self.app = app
        self._clients: list[httpx.AsyncClient] = []

    async def _truncate(self) -> None:
        """Empty every table between tests (test database only)."""
        assert_test_database(settings.database_url)
        connection = await asyncpg.connect(_asyncpg_dsn(settings.database_url))
        try:
            tables = [
                row["tablename"]
                for row in await connection.fetch(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
                )
            ]
            if tables:
                await connection.execute("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " CASCADE")
        finally:
            await connection.close()

    async def asyncTearDown(self) -> None:
        for client in self._clients:
            await client.aclose()
        from app.database import engine

        await engine.dispose()

    def browser(self) -> Api:
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        self._clients.append(client)
        return Api(client)

    async def signed_in(self, email: str, name: str = "Test User") -> Api:
        api = self.browser()
        response = await api.sign_up(email, name)
        assert response.status_code == 200, response.text
        return api
