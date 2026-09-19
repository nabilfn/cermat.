# Deployment

Target architecture:

```text
Browser ──► Next.js (web) ──/api/*──► FastAPI (api) ──► Managed PostgreSQL
                                          └──────────► S3-compatible object storage
                                          └──────────► AI provider (optional)
```

The browser only ever talks to the web origin. Next.js proxies `/api/*` to the API, so there are no hard-coded `localhost` URLs and no cross-site cookies. Nothing is tied to a single cloud vendor.

## Production images

Both apps have multi-stage Dockerfiles with a `production` target:

| Image | Notes |
|---|---|
| `apps/api` → `production` | Python 3.12-slim, non-root user (uid 10001), no dev tools or tests, no hot reload. The entrypoint validates configuration (and fails fast), runs `alembic upgrade head` unless `RUN_MIGRATIONS=false`, then starts uvicorn with `WEB_CONCURRENCY` workers. Healthcheck on `/health`. |
| `apps/web` → `production` | Next.js standalone server, non-root user. **`API_INTERNAL_URL` is a build argument**, because rewrites are resolved when the app is built. |

No secrets are baked into either image; everything comes from the environment at runtime.

## Option A — single host (Docker Compose)

Good for a small team on one VM, with PostgreSQL and uploads on named volumes.

```bash
cp .env.example .env
# set APP_ENV=production, SECRET_KEY, POSTGRES_PASSWORD, OPENAI_API_KEY
docker compose -f docker-compose.prod.yml up --build -d
```

- Only the web container publishes a port (`WEB_PORT`, default 3000). Put a TLS-terminating reverse proxy (Caddy, nginx, a load balancer) in front, and keep `COOKIE_SECURE=true`.
- Files use `STORAGE_PROVIDER=local` on the `cermat_uploads` volume. `ALLOW_LOCAL_STORAGE_IN_PRODUCTION=true` is set explicitly in that file for this reason. Back up both volumes.

## Option B — managed services (recommended)

1. **PostgreSQL**: any managed PostgreSQL 14+ (RDS, Cloud SQL, Neon, Supabase, …). Set `DATABASE_URL=postgresql+asyncpg://…`.
2. **Object storage**: any S3-compatible bucket (S3, R2, GCS interoperability, MinIO, Spaces). Set `STORAGE_PROVIDER=s3`, `S3_BUCKET`, and, as needed, `S3_ENDPOINT_URL`, `S3_REGION`, `S3_ACCESS_KEY_ID` and `S3_SECRET_ACCESS_KEY`. Source files are served through short-lived presigned URLs after the API authorises the request.
3. **API**: run the `apps/api` production image on any container platform (Cloud Run, ECS/Fargate, Fly.io, Render, Kubernetes, …). With more than one replica, run migrations once per release (`alembic upgrade head` as a release or pre-deploy job) and set `RUN_MIGRATIONS=false` on the service.
4. **Web**: build `apps/web` with `--build-arg API_INTERNAL_URL=https://<internal api address>` and run it on any container platform, or on Vercel with `API_INTERNAL_URL` set.
5. Point your domain at the web service only. The API does not need to be public.

### Required environment (API)

| Variable | Production value |
|---|---|
| `APP_ENV` | `production` |
| `SECRET_KEY` | ≥ 32 random characters. Startup fails otherwise. |
| `DATABASE_URL` | managed PostgreSQL. Startup fails on the development credentials. |
| `STORAGE_PROVIDER` / `S3_*` | `s3`. Local storage is refused unless explicitly allowed. |
| `OPENAI_API_KEY`, `OPENAI_MODEL` | optional |
| `COOKIE_SECURE` | `true` (the default in production) |
| `CORS_ORIGINS` | leave empty with the proxy setup |
| `WEB_CONCURRENCY` | uvicorn workers (default 2) |

## Operations

- `GET /health`: the process is alive (never contacts the AI provider).
- `GET /ready`: PostgreSQL is reachable and migrated. Returns 503 otherwise, which is suitable for readiness probes.
- Logs are JSON on stdout with `request_id`, `route`, `status` and `duration_ms`. Send them to any log backend. Every response carries `X-Request-ID` for correlating user reports.
- A restarted API marks extractions stuck in `processing` for more than 15 minutes as `failed`, so they can be retried.
- OpenAPI docs (`/docs`) are disabled in production.

## Upgrading an existing installation

```bash
docker compose exec api alembic upgrade head
```

Migrations are idempotent. Data created before accounts existed moves into a *Legacy workspace*. Give an account ownership of it with:

```bash
docker compose exec api python -m scripts.claim_legacy_workspace you@example.com
```

Take a database backup first. The workspace migration has no downgrade path by design.
