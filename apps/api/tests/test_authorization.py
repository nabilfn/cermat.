"""IDOR tests: user B must never reach user A's workspace data.

Foreign resources answer 404 — indistinguishable from missing ones — so ids
cannot be probed. Every resource family in the API is covered.
"""

from __future__ import annotations

import unittest

from support import PNG_1PX, ApiTestCase

from app.auth.deps import WorkspaceContext
from app.core.security import csrf_for, digest, password_problems
from app.services.uploads import safe_display_name, sniff


class CrossWorkspaceAccessTests(ApiTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.alice = await self.signed_in("alice@example.com", "Alice")
        self.bob = await self.signed_in("bob@example.com", "Bob")
        flow = await self.alice.reconciled_transaction("ALICE-1")
        self.txn = flow["transaction"]["id"]
        self.docs = flow["documents"]
        self.issue = (await self.alice.get(f"/api/v1/transactions/{self.txn}/issues")).json()[0]["id"]
        attention = (await self.alice.get("/api/v1/attention")).json()
        self.event = attention["events"][0]["id"]
        # Bob has his own, unrelated data.
        await self.bob.post("/api/v1/transactions", json={"name": "BOB-TXN"})

    async def assertHidden(self, method: str, path: str, **kwargs) -> None:
        response = await self.bob.request(method, path, **kwargs)
        self.assertIn(response.status_code, (403, 404), f"{method} {path} → {response.status_code}")
        self.assertNotIn("ALICE", response.text)

    async def test_transactions(self) -> None:
        for method, path in (
            ("GET", f"/api/v1/transactions/{self.txn}"),
            ("GET", f"/api/v1/transactions/{self.txn}/reconciliation"),
            ("GET", f"/api/v1/transactions/{self.txn}/activity"),
            ("POST", f"/api/v1/transactions/{self.txn}/reconcile"),
            ("DELETE", f"/api/v1/transactions/{self.txn}"),
        ):
            await self.assertHidden(method, path)
        listing = (await self.bob.get("/api/v1/transactions")).json()
        self.assertEqual([t["name"] for t in listing["items"]], ["BOB-TXN"])
        search = (await self.bob.get("/api/v1/transactions?q=ALICE")).json()
        self.assertEqual(search["total"], 0)

    async def test_documents(self) -> None:
        doc = self.docs["invoice"]
        for method, path in (
            ("GET", f"/api/v1/documents/{doc}"),
            ("GET", f"/api/v1/documents/{doc}/file"),
            ("POST", f"/api/v1/documents/{doc}/extract"),
            ("DELETE", f"/api/v1/documents/{doc}"),
        ):
            await self.assertHidden(method, path)
        # Attaching Alice's document to Bob's transaction must fail too.
        bob_txn = (await self.bob.get("/api/v1/transactions")).json()["items"][0]["id"]
        await self.assertHidden("POST", f"/api/v1/transactions/{bob_txn}/documents/{doc}")

    async def test_review_issues(self) -> None:
        await self.assertHidden("GET", f"/api/v1/transactions/{self.txn}/issues")
        await self.assertHidden(
            "PATCH", f"/api/v1/transactions/{self.txn}/issues/{self.issue}", json={"status": "resolved"}
        )
        # Even through Bob's own transaction id, Alice's issue is not reachable.
        bob_txn = (await self.bob.get("/api/v1/transactions")).json()["items"][0]["id"]
        await self.assertHidden(
            "PATCH", f"/api/v1/transactions/{bob_txn}/issues/{self.issue}", json={"status": "resolved"}
        )
        still_open = (await self.alice.get(f"/api/v1/transactions/{self.txn}/issues")).json()
        self.assertTrue(all(i["status"] == "open" for i in still_open))

    async def test_ask_cermat(self) -> None:
        await self.assertHidden("GET", f"/api/v1/transactions/{self.txn}/context")
        await self.assertHidden("GET", f"/api/v1/ask/suggestions?transaction_id={self.txn}")
        await self.assertHidden("POST", "/api/v1/ask", json={"question": "Why is this flagged?", "transaction_id": self.txn})
        answer = (await self.bob.post("/api/v1/ask", json={"question": "Why is ALICE-1 flagged?"})).json()
        self.assertEqual(answer["outcome"], "not_found")
        workspace = (await self.bob.post("/api/v1/ask", json={"question": "What needs my attention?"})).json()
        self.assertNotIn("ALICE", str(workspace))

    async def test_supplier_intelligence(self) -> None:
        await self.assertHidden("GET", "/api/v1/intelligence/suppliers/abc-supplies")
        suppliers = (await self.bob.get("/api/v1/intelligence/suppliers")).json()
        self.assertEqual(suppliers["total"], 0)
        overview = (await self.bob.get("/api/v1/intelligence/overview")).json()
        self.assertEqual(overview["metrics"]["open_issue_count"], 0)
        self.assertEqual(overview["priority"], [])

    async def test_attention_events(self) -> None:
        await self.assertHidden("PATCH", f"/api/v1/attention/{self.event}", json={"dismissed": True})
        bob_events = (await self.bob.get("/api/v1/attention")).json()
        self.assertNotIn(self.event, [e["id"] for e in bob_events["events"]])
        alice_events = (await self.alice.get("/api/v1/attention")).json()
        self.assertIn(self.event, [e["id"] for e in alice_events["events"]])  # not dismissed by Bob

    async def test_audit_events(self) -> None:
        bob_audit = (await self.bob.get("/api/v1/audit?limit=200")).json()["items"]
        self.assertTrue(bob_audit)
        self.assertFalse([e for e in bob_audit if e["entity_id"] in {self.txn, *self.docs.values()}])

    async def test_exports(self) -> None:
        await self.assertHidden("GET", f"/api/v1/exports/review-issues.csv?transaction_id={self.txn}")
        csv = await self.bob.get("/api/v1/exports/review-issues.csv")
        self.assertEqual(csv.status_code, 200)
        self.assertNotIn("ALICE", csv.text)

    async def test_workspace_header_cannot_select_foreign_workspace(self) -> None:
        response = await self.bob.get("/api/v1/transactions", headers={"X-Workspace-Id": self.alice.workspace_id})
        self.assertEqual(response.status_code, 404)
        # The query-parameter form used by plain links is checked the same way.
        bob_client_only = self.bob.client
        for path in (
            f"/api/v1/documents/{self.docs['invoice']}/file?workspace_id={self.alice.workspace_id}",
            f"/api/v1/exports/review-issues.csv?workspace_id={self.alice.workspace_id}",
        ):
            leaked = await bob_client_only.get(path)
            self.assertEqual(leaked.status_code, 404, path)
        for method, path in (
            ("GET", f"/api/v1/workspaces/{self.alice.workspace_id}/members"),
            ("POST", f"/api/v1/workspaces/{self.alice.workspace_id}/demo/reset"),
            ("PATCH", f"/api/v1/workspaces/{self.alice.workspace_id}"),
        ):
            kwargs = {"json": {"name": "pwned"}} if method == "PATCH" else {}
            await self.assertHidden(method, path, **kwargs)
        delete = await self.bob.request(
            "DELETE", f"/api/v1/workspaces/{self.alice.workspace_id}", json={"confirm_name": "anything"}
        )
        self.assertEqual(delete.status_code, 404)
        self.assertEqual((await self.alice.get(f"/api/v1/transactions/{self.txn}")).status_code, 200)

    async def test_settings_are_per_workspace(self) -> None:
        await self.bob.patch("/api/v1/settings/intelligence", json={"overdue_review_days": 30})
        alice = (await self.alice.get("/api/v1/settings/intelligence")).json()
        self.assertEqual(alice["overdue_review_days"], 7)


class AuthorizationHelperTests(unittest.TestCase):  # no database needed
    def test_workspace_roles(self) -> None:
        self.assertTrue(WorkspaceContext(user=None, workspace=None, role="owner").is_owner)
        self.assertFalse(WorkspaceContext(user=None, workspace=None, role="member").is_owner)

    def test_tokens_are_bound_to_session_and_purpose(self) -> None:
        self.assertNotEqual(csrf_for("a"), csrf_for("b"))
        self.assertNotEqual(digest("t", "session"), digest("t", "csrf"))
        self.assertEqual(len(digest("t")), 64)

    def test_password_rules(self) -> None:
        self.assertTrue(password_problems("short", "x@example.com"))
        self.assertTrue(password_problems("password123", "x@example.com"))
        self.assertTrue(password_problems("aaaaaaaaaaaa", "x@example.com"))
        self.assertFalse(password_problems("correct-horse-battery-7", "x@example.com"))

    def test_upload_helpers(self) -> None:
        self.assertEqual(safe_display_name("../../etc/passwd.pdf"), "passwd.pdf")
        self.assertEqual(safe_display_name("C:\\Users\\x\\inv\x00oice.pdf"), "invoice.pdf")
        self.assertEqual(safe_display_name(""), "document")
        self.assertEqual(sniff(PNG_1PX), "image/png")
        self.assertEqual(sniff(b"%PDF-1.7"), "application/pdf")
        self.assertIsNone(sniff(b"MZ\x90"))
