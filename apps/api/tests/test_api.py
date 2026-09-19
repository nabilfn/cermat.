"""API integration tests against a migrated PostgreSQL test database."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from support import PNG_1PX, ApiTestCase, blank_pdf

from app.config import settings
from app.database import SessionLocal
from app.models import DocumentModel, UserModel


class AuthTests(ApiTestCase):
    async def test_signup_hashes_password_and_sets_secure_session(self) -> None:
        api = self.browser()
        response = await api.sign_up("ada@example.com", "Ada")
        self.assertEqual(response.status_code, 200)
        cookie = response.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=lax", cookie)
        body = response.json()
        self.assertEqual(body["user"]["email"], "ada@example.com")
        self.assertEqual(body["workspaces"][0]["role"], "owner")
        self.assertTrue(body["csrf_token"])

        async with SessionLocal() as session:
            user = (await session.execute(select(UserModel).where(UserModel.email == "ada@example.com"))).scalar_one()
        self.assertTrue(user.password_hash.startswith("$argon2id$"))
        self.assertNotIn("correct-horse", user.password_hash)

    async def test_password_and_email_validation(self) -> None:
        api = self.browser()
        weak = await api.client.post("/api/v1/auth/signup", json={"email": "b@example.com", "password": "short", "display_name": "B"})
        self.assertEqual(weak.status_code, 422)
        self.assertEqual(weak.json()["error"]["code"], "VALIDATION_ERROR")
        self.assertNotIn("short", weak.text)  # submitted values are never echoed
        bad_email = await api.client.post("/api/v1/auth/signup", json={"email": "nope", "password": "a-strong-password-1", "display_name": "B"})
        self.assertEqual(bad_email.status_code, 422)

    async def test_duplicate_signup_and_generic_signin_errors(self) -> None:
        await self.signed_in("c@example.com")
        again = await self.browser().sign_up("C@Example.com")
        self.assertEqual(again.status_code, 409)
        wrong = await self.browser().sign_in("c@example.com", "wrong-password-123")
        unknown = await self.browser().sign_in("nobody@example.com", "wrong-password-123")
        self.assertEqual((wrong.status_code, unknown.status_code), (401, 401))
        self.assertEqual(wrong.json()["error"]["message"], unknown.json()["error"]["message"])
        ok = await self.browser().sign_in("c@example.com")
        self.assertEqual(ok.status_code, 200)

    async def test_protected_endpoints_require_session(self) -> None:
        anonymous = self.browser()
        for path in ("/api/v1/transactions", "/api/v1/intelligence/overview", "/api/v1/attention", "/api/v1/audit"):
            response = await anonymous.get(path)
            self.assertEqual(response.status_code, 401, path)
            self.assertEqual(response.json()["error"]["code"], "UNAUTHORIZED")
            self.assertTrue(response.json()["error"]["request_id"])

    async def test_csrf_required_for_unsafe_methods(self) -> None:
        api = await self.signed_in("d@example.com")
        no_token = await api.client.post(
            "/api/v1/transactions", json={"name": "x"}, headers={"X-Workspace-Id": api.workspace_id}
        )
        self.assertEqual(no_token.status_code, 403)
        forged = await api.client.post(
            "/api/v1/transactions", json={"name": "x"},
            headers={"X-Workspace-Id": api.workspace_id, "X-CSRF-Token": "forged"},
        )
        self.assertEqual(forged.status_code, 403)
        ok = await api.post("/api/v1/transactions", json={"name": "x"})
        self.assertEqual(ok.status_code, 201)

    async def test_signout_ends_session(self) -> None:
        api = await self.signed_in("e@example.com")
        self.assertEqual((await api.post("/api/v1/auth/signout")).status_code, 204)
        self.assertEqual((await api.get("/api/v1/auth/me")).status_code, 401)


class UploadSecurityTests(ApiTestCase):
    async def test_rejects_unsupported_and_disguised_files(self) -> None:
        api = await self.signed_in("up@example.com")
        exe = await api.upload("invoice", "invoice.exe", b"MZ\x90\x00")
        self.assertEqual((exe.status_code, exe.json()["error"]["code"]), (415, "UNSUPPORTED_DOCUMENT"))
        disguised = await api.upload("invoice", "invoice.pdf", PNG_1PX)
        self.assertEqual(disguised.status_code, 415)
        empty = await api.upload("invoice", "invoice.pdf", b"")
        self.assertEqual(empty.status_code, 400)

    async def test_rejects_oversized_and_long_pdfs(self) -> None:
        api = await self.signed_in("big@example.com")
        too_big = await api.upload("invoice", "big.pdf", b"%PDF-1.4\n" + b"0" * (settings.max_upload_bytes + 10))
        self.assertEqual((too_big.status_code, too_big.json()["error"]["code"]), (413, "UPLOAD_TOO_LARGE"))
        long_pdf = await api.upload("invoice", "long.pdf", blank_pdf(settings.max_pdf_pages + 1))
        self.assertEqual(long_pdf.status_code, 413)

    async def test_filename_is_metadata_only(self) -> None:
        api = await self.signed_in("path@example.com")
        response = await api.upload("invoice", "../../etc/passwd.pdf")
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["filename"], "passwd.pdf")
        async with SessionLocal() as session:
            document = await session.get(DocumentModel, response.json()["id"])
        self.assertTrue(document.storage_key.startswith(f"{api.workspace_id}/"))
        self.assertNotIn("passwd", document.storage_key)
        self.assertNotIn("..", document.storage_key)
        stored = Path(settings.data_dir, document.storage_key).resolve()
        self.assertTrue(stored.is_file())
        self.assertIn(Path(settings.data_dir).resolve(), stored.parents)

    async def test_image_upload_and_source_file_download(self) -> None:
        api = await self.signed_in("img@example.com")
        image = await api.upload("receipt", "Receipt 01.png", PNG_1PX)
        self.assertEqual(image.status_code, 201, image.text)
        self.assertEqual(image.json()["mime_type"], "image/png")
        file = await api.get(f"/api/v1/documents/{image.json()['id']}/file")
        self.assertEqual(file.status_code, 200)
        self.assertEqual(file.content, PNG_1PX)
        self.assertEqual(file.headers["x-content-type-options"], "nosniff")


class ExtractionFailureTests(ApiTestCase):
    async def test_failed_extraction_keeps_document_recoverable(self) -> None:
        api = await self.signed_in("fail@example.com")
        document = (await api.upload("invoice")).json()
        response = await api.post(f"/api/v1/documents/{document['id']}/extract")  # no API key in tests
        self.assertEqual(response.json()["error"]["code"], "AI_PROVIDER_ERROR")
        status = (await api.get(f"/api/v1/documents/{document['id']}")).json()
        self.assertEqual((status["status"], status["error_code"]), ("failed", "AI_PROVIDER_ERROR"))
        self.assertTrue(status["has_source_file"])


class SmokeFlowTests(ApiTestCase):
    async def test_end_to_end_product_path(self) -> None:
        """login → upload PO/DO/INV → extract → reconcile → evidence → resolve → overview → ask."""
        api = await self.signed_in("flow@example.com", "Nabil")
        flow = await api.reconciled_transaction()
        txn_id = flow["transaction"]["id"]
        codes = {issue["code"] for issue in flow["reconciliation"]["issues"]}
        self.assertIn("invoice_delivery_quantity_variance", codes)  # invoiced 10, delivered 8
        self.assertIn("invoice_price_variance", codes)  # 42 → 44

        context = (await api.get(f"/api/v1/transactions/{txn_id}/context")).json()
        price = next(i for i in context["issues"] if i["code"] == "invoice_price_variance")
        self.assertEqual((price["variance"]["delta"], price["variance"]["percentage"]), (2.0, 4.76))
        sources = {s["id"]: s for s in context["sources"]}
        cited = [sources[sid] for sid in price["source_ids"]]
        self.assertTrue(all(s["page"] == 1 and s["confidence"] and s["source_text"] for s in cited))

        issues = (await api.get(f"/api/v1/transactions/{txn_id}/issues")).json()
        target = next(i for i in issues if i["code"] == "invoice_price_variance")
        resolved = await api.patch(
            f"/api/v1/transactions/{txn_id}/issues/{target['id']}",
            json={"status": "resolved", "resolution_note": "Supplier issued credit note"},
        )
        self.assertEqual(resolved.status_code, 200)
        self.assertEqual(resolved.json()["resolved_by_name"], "Nabil")

        overview = (await api.get("/api/v1/intelligence/overview?period=30d")).json()
        self.assertEqual(overview["metrics"]["resolved_issue_count"], 1)
        self.assertEqual(overview["metrics"]["open_issue_count"], len(issues) - 1)

        answer = (await api.post("/api/v1/ask", json={"question": "What needs my attention?"})).json()
        self.assertEqual(answer["outcome"], "answered")
        self.assertIn("PO-T-1", " ".join(p["text"] for p in answer["answer"]["points"]) + answer["answer"]["headline"])

        activity = [e["action"] for e in (await api.get(f"/api/v1/transactions/{txn_id}/activity")).json()]
        for action in ("transaction_created", "document_uploaded", "document_extracted", "reconciliation_run", "issue_resolved"):
            self.assertIn(action, activity)

        csv = await api.get("/api/v1/exports/review-issues.csv?status=resolved")
        self.assertEqual(csv.status_code, 200)
        self.assertIn("Supplier issued credit note", csv.text)
        self.assertIn("text/csv", csv.headers["content-type"])

        history = (await api.get("/api/v1/transactions?q=INV-T-1")).json()
        self.assertEqual([t["name"] for t in history["items"]], ["PO-T-1"])
        self.assertEqual((await api.get("/api/v1/transactions?q=nothing-like-this")).json()["total"], 0)

    async def test_reconcile_rerun_keeps_review_decisions(self) -> None:
        api = await self.signed_in("rerun@example.com")
        flow = await api.reconciled_transaction()
        txn_id = flow["transaction"]["id"]
        issue = (await api.get(f"/api/v1/transactions/{txn_id}/issues")).json()[0]
        await api.patch(f"/api/v1/transactions/{txn_id}/issues/{issue['id']}", json={"status": "resolved"})
        again = await api.post(f"/api/v1/transactions/{txn_id}/reconcile")
        self.assertEqual(again.status_code, 200)
        after = {i["id"]: i for i in (await api.get(f"/api/v1/transactions/{txn_id}/issues")).json()}
        self.assertEqual(after[issue["id"]]["status"], "resolved")
        self.assertEqual(len(after), len(flow["reconciliation"]["issues"]))

    async def test_attention_events_do_not_duplicate(self) -> None:
        api = await self.signed_in("attn@example.com")
        await api.reconciled_transaction()
        first = (await api.get("/api/v1/attention")).json()
        second = (await api.get("/api/v1/attention")).json()
        self.assertGreater(first["active_count"], 0)
        self.assertEqual([e["id"] for e in first["events"]], [e["id"] for e in second["events"]])


class RoleTests(ApiTestCase):
    async def test_member_can_work_but_not_administer(self) -> None:
        owner = await self.signed_in("owner@example.com")
        member = await self.signed_in("member@example.com")
        added = await owner.post(f"/api/v1/workspaces/{owner.workspace_id}/members", json={"email": "member@example.com"})
        self.assertEqual(added.status_code, 201, added.text)
        member.workspace_id = owner.workspace_id

        self.assertEqual((await member.post("/api/v1/transactions", json={"name": "by member"})).status_code, 201)
        self.assertEqual((await member.upload("invoice")).status_code, 201)
        settings_change = await member.patch("/api/v1/settings/intelligence", json={"overdue_review_days": 3})
        self.assertEqual(settings_change.status_code, 403)
        invite = await member.post(f"/api/v1/workspaces/{owner.workspace_id}/members", json={"email": "owner@example.com"})
        self.assertEqual(invite.status_code, 403)
        self.assertEqual((await owner.patch("/api/v1/settings/intelligence", json={"overdue_review_days": 3})).status_code, 200)

        audit = [e["action"] for e in (await owner.get("/api/v1/audit")).json()["items"]]
        self.assertIn("member_invited", audit)
        self.assertIn("settings_changed", audit)

    async def test_last_owner_cannot_be_removed(self) -> None:
        owner = await self.signed_in("solo@example.com")
        response = await owner.delete(f"/api/v1/workspaces/{owner.workspace_id}/members/{owner.user['id']}")
        self.assertEqual(response.status_code, 409)


class DemoWorkspaceTests(ApiTestCase):
    async def test_demo_is_separate_labelled_and_resettable(self) -> None:
        api = await self.signed_in("demo@example.com")
        real_workspace = api.workspace_id
        demo = (await api.post("/api/v1/workspaces/demo")).json()
        self.assertTrue(demo["is_demo"])
        self.assertNotEqual(demo["id"], real_workspace)

        api.workspace_id = demo["id"]
        seeded = (await api.get("/api/v1/transactions?limit=100")).json()["total"]
        self.assertGreater(seeded, 15)
        overview = (await api.get("/api/v1/intelligence/overview")).json()
        self.assertGreater(overview["metrics"]["open_issue_count"], 0)
        self.assertTrue(overview["patterns"])

        await api.post("/api/v1/transactions", json={"name": "extra in demo"})
        reset = await api.post(f"/api/v1/workspaces/{demo['id']}/demo/reset")
        self.assertEqual(reset.status_code, 200)
        self.assertEqual((await api.get("/api/v1/transactions?limit=100")).json()["total"], seeded)

        api.workspace_id = real_workspace
        self.assertEqual((await api.get("/api/v1/transactions")).json()["total"], 0)  # real workspace untouched
        refused = await api.post(f"/api/v1/workspaces/{real_workspace}/demo/reset")
        self.assertEqual(refused.status_code, 403)


class DeletionTests(ApiTestCase):
    async def test_transaction_deletion_removes_records_and_files(self) -> None:
        api = await self.signed_in("del@example.com")
        flow = await api.reconciled_transaction()
        txn_id = flow["transaction"]["id"]
        async with SessionLocal() as session:
            keys = [
                (await session.get(DocumentModel, doc_id)).storage_key for doc_id in flow["documents"].values()
            ]
        self.assertEqual((await api.delete(f"/api/v1/transactions/{txn_id}")).status_code, 204)
        self.assertEqual((await api.get(f"/api/v1/transactions/{txn_id}")).status_code, 404)
        for doc_id in flow["documents"].values():
            self.assertEqual((await api.get(f"/api/v1/documents/{doc_id}")).status_code, 404)
        for key in keys:
            self.assertFalse(Path(settings.data_dir, key).exists())

    async def test_attached_document_cannot_be_deleted_alone(self) -> None:
        api = await self.signed_in("del2@example.com")
        flow = await api.reconciled_transaction()
        response = await api.delete(f"/api/v1/documents/{flow['documents']['invoice']}")
        self.assertEqual(response.status_code, 409)

    async def test_workspace_deletion_requires_typed_confirmation(self) -> None:
        api = await self.signed_in("del3@example.com", "Del")
        await api.reconciled_transaction()
        name = (await api.get("/api/v1/workspaces")).json()[0]["name"]
        wrong = await api.request("DELETE", f"/api/v1/workspaces/{api.workspace_id}", json={"confirm_name": "nope"})
        self.assertEqual(wrong.status_code, 422)
        ok = await api.request("DELETE", f"/api/v1/workspaces/{api.workspace_id}", json={"confirm_name": name})
        self.assertEqual(ok.status_code, 204)
        self.assertEqual((await api.get("/api/v1/workspaces")).json(), [])


class ErrorShapeTests(ApiTestCase):
    async def test_errors_share_one_shape(self) -> None:
        api = await self.signed_in("err@example.com")
        missing = await api.get("/api/v1/transactions/00000000-0000-0000-0000-000000000000")
        invalid = await api.get("/api/v1/transactions/not-a-uuid")
        for response, code in ((missing, "NOT_FOUND"), (invalid, "VALIDATION_ERROR")):
            error = response.json()["error"]
            self.assertEqual(error["code"], code)
            self.assertEqual(error["request_id"], response.headers["x-request-id"])
            self.assertNotIn("Traceback", response.text)

    async def test_rate_limit_on_ai_endpoints(self) -> None:
        from unittest import mock

        api = await self.signed_in("rate@example.com")
        with mock.patch.object(settings, "rate_limit_ai_per_minute", 2):
            codes = [(await api.post("/api/v1/ask", json={"question": "What needs my attention?"})).status_code for _ in range(3)]
        self.assertEqual(codes[:2], [200, 200])
        self.assertEqual(codes[2], 429)
