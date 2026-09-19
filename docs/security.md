# Security

This describes what cermat. actually implements, and where its limits are.

## Authentication

- **Passwords** are hashed with Argon2id (`argon2-cffi` defaults, RFC 9106 profile) and rehashed on sign-in if the parameters change. Plaintext passwords are never stored or logged.
- **Password rules**: 10–128 characters, not a common password, not your email address.
- **Sign-in** returns the same generic error for an unknown email and a wrong password. An unknown email is still checked against a dummy hash, so response timing does not reveal which accounts exist. Sign-in is throttled per IP and email (10 attempts per 5 minutes), and sign-up per IP.
- **Sessions** are opaque random tokens (`secrets.token_urlsafe(32)`) in an `httpOnly`, `SameSite=Lax` cookie that is `Secure` in production. Only an HMAC-SHA256 of the token is stored, so a database leak does not yield usable sessions. Sessions expire after `SESSION_TTL_HOURS` (default 14 days), and sign-out deletes the session row.
- **CSRF**: every `POST`/`PATCH`/`DELETE` must send `X-CSRF-Token`. This is a per-session token derived by HMAC from the session token (synchroniser-token pattern). A cross-site form cannot set the header, and `SameSite=Lax` blocks the cookie on cross-site sub-requests.
- **Same origin**: the browser only talks to the Next.js origin, which proxies `/api/*` to FastAPI. The session cookie is therefore first-party and CORS stays disabled by default. `CORS_ORIGINS` is available if you deliberately split origins, and `*` is refused in production.

## Authorization

- Tenancy tables: `users`, `workspaces`, `workspace_members` (roles `owner` / `member`).
- Every business table carries a non-null `workspace_id` foreign key: documents, transactions, review issues, attention events, intelligence settings and audit events.
- Every business endpoint depends on `workspace_context` (`app/auth/deps.py`). It authenticates the session, then takes the requested workspace (from `X-Workspace-Id`, or `?workspace_id=` for plain links such as file downloads) and **verifies membership** before any query runs. Every lookup is then filtered by that workspace id.
- A resource in another workspace is indistinguishable from a missing one (**404**), so ids cannot be probed.
- **Owners** manage members, rename or delete the workspace, change thresholds and reset the demo. **Members** upload, reconcile, review, export and use Ask cermat. and the Overview.
- The IDOR suite (`apps/api/tests/test_authorization.py`) has a second user probe every resource family (documents, source files, transactions, issues, reconciliation, activity, Ask context and suggestions, supplier intelligence, attention events, audit, exports, workspace header and query parameter, members, demo reset, deletion) and asserts 404/403 with no leaked content.

## Uploads

Every file is treated as untrusted (`app/services/uploads.py`):

1. Size ≤ `MAX_UPLOAD_MB`, enforced while streaming by middleware and again after reading. Other requests are limited to 1 MB.
2. Extension allow-list: `.pdf .png .jpg .jpeg .webp`.
3. **Content sniffing** by magic bytes. The declared MIME type is ignored, and extension and content must agree.
4. PDFs must parse (`pypdf`), must not be encrypted, and may have at most `MAX_PDF_PAGES` pages.
5. The original filename is reduced to its final path component with control characters stripped, and kept **as metadata only**. Storage keys are server-generated (`<workspace>/<uuid><ext>`). The local provider also refuses any key that resolves outside its root.
6. Files are served back only through an authorised endpoint, with `X-Content-Type-Options: nosniff`, a restrictive `Content-Security-Policy`/`sandbox`, `Cache-Control: private, no-store`, and a sanitised `Content-Disposition`.

## Errors and logging

- Every error has one shape: `{"error": {"code", "message", "request_id"}}`. Validation errors list fields but never echo submitted values. Unhandled exceptions return `INTERNAL_ERROR` and are logged server-side with the request id. Stack traces, SQL and provider errors never reach clients.
- Every request gets an `X-Request-ID` (an inbound id is accepted if well-formed). Logs are JSON lines with `timestamp`, `level`, `request_id`, `route`, `user_id`, `workspace_id`, `event`, `status` and `duration_ms`. Bodies, headers, cookies, passwords, API keys and document content are not logged.
- The audit trail (`audit_events`) is append-only through the API and records operational actions: uploads, extractions, reconciliations, resolve/reopen, settings changes, member changes, deletions, exports and demo resets. Metadata is limited to ids, counts and statuses, and a denylist strips keys such as `password`, `token` and `extraction_data`.

## AI endpoints

- Extraction, Ask cermat. and the brief are rate-limited per user (`RATE_LIMIT_AI_PER_MINUTE`, default 20).
- Model calls have timeouts and bounded retries (`AI_TIMEOUT_SECONDS`, `AI_MAX_RETRIES`). Failures leave documents in a recoverable `failed` state with the source file kept.
- Ask cermat. is read-only and cannot run SQL. See [architecture.md](architecture.md#ask-cermat).
- Document text, supplier names and notes are treated as untrusted data in every prompt.

## Destructive actions

- Deleting a transaction removes its documents, review issues, attention events and stored files. The UI asks for confirmation.
- Deleting a workspace requires typing its exact name. The API checks the name too. Everything in the workspace is removed, including files.
- Demo reset only works on a workspace flagged `is_demo`.

## Privacy

What leaves your infrastructure:

- **Extraction**: the uploaded document (PDF or image) is sent to the configured AI provider together with extraction instructions, to produce structured fields.
- **Ask cermat.**: the question and minimal follow-up state (the previous question and entity labels) are sent for intent planning. For the answer, only the retrieved business records needed for the current question are sent: the matching exceptions, their expected/actual values and short evidence snippets. Never the whole database.
- **cermat. brief**: aggregate metrics and short titles (counts, variance totals, supplier names, pattern sentences).
- Nothing is sent to the AI provider when `OPENAI_API_KEY` is unset. Extraction is then unavailable and Ask and the brief work from records alone.

cermat. makes no guarantees beyond this about how the provider stores data; that is governed by your agreement with the provider. The provider integration is isolated in `app/services/extraction.py` and `app/services/ask_llm.py` so it can be replaced.

## Known limitations

- Rate limits and the brief cache are per API process. With several replicas each enforces its own window. A shared store (e.g. Redis) would be needed for strict global limits.
- There is no email verification or password reset flow yet. Members must already have an account to be added.
- Sessions are not bound to IP or user agent.
- Audit events are append-only through the application, but not tamper-evident against someone with direct database access.

## Checklist

- [x] `.env` ignored; `.env.example` holds placeholders only
- [x] passwords hashed with Argon2id
- [x] every business API requires a session; CSRF on unsafe methods
- [x] workspace membership enforced on every resource; IDOR tests pass
- [x] upload validation (content, extension, size, pages); server-generated keys; no path traversal
- [x] AI endpoints rate-limited
- [x] production errors sanitised; API docs disabled in production
- [x] CORS off by default, `*` refused in production
- [x] destructive actions confirmed
- [x] audit logging enabled
