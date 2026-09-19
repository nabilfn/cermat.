"""Application settings, validated at startup.

Production refuses to start with insecure defaults instead of silently running
with them. Development and test keep zero-config local operation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_SECRET = "dev-only-insecure-secret-change-me-0000000000"


class Settings(BaseSettings):
    app_env: Literal["development", "test", "production"] = "development"

    database_url: str = "postgresql+asyncpg://cermat:cermat@db:5432/cermat"
    secret_key: str = INSECURE_SECRET

    # AI provider. Features degrade gracefully without a key.
    openai_api_key: str = ""
    openai_model: str = "gpt-5.6-luna"
    ai_timeout_seconds: float = Field(default=90, gt=0, le=600)
    ai_max_retries: int = Field(default=2, ge=0, le=5)

    # Comma-separated. Only needed when a browser calls the API from another origin;
    # the Next.js app proxies /api/* so the default deployment is same-origin.
    cors_origins: str = ""

    # Uploads & storage
    max_upload_mb: int = Field(default=15, ge=1, le=100)
    max_pdf_pages: int = Field(default=30, ge=1, le=500)
    storage_provider: Literal["local", "s3"] = "local"
    data_dir: Path = Path("/app/data/uploads")
    allow_local_storage_in_production: bool = False
    s3_bucket: str = ""
    s3_endpoint_url: str = ""
    s3_region: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_prefix: str = "cermat"

    # Sessions
    session_ttl_hours: int = Field(default=24 * 14, ge=1, le=24 * 90)
    cookie_secure: bool | None = None  # defaults to True in production

    # Rate limits for AI-backed endpoints (per user, per minute, per process)
    rate_limit_ai_per_minute: int = Field(default=20, ge=1, le=1000)

    # First account created in development claims data that pre-dates workspaces.
    claim_legacy_workspace: bool | None = None

    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @model_validator(mode="after")
    def _validate_for_environment(self) -> "Settings":
        if self.app_env != "production":
            return self
        problems: list[str] = []
        if self.secret_key == INSECURE_SECRET or len(self.secret_key) < 32:
            problems.append("SECRET_KEY must be set to a random value of at least 32 characters.")
        if "cermat:cermat@" in self.database_url:
            problems.append("DATABASE_URL still uses the development credentials.")
        if self.storage_provider == "local" and not self.allow_local_storage_in_production:
            problems.append(
                "STORAGE_PROVIDER=local stores files on the container filesystem. Use s3, or set "
                "ALLOW_LOCAL_STORAGE_IN_PRODUCTION=true only with a persistent volume."
            )
        if self.storage_provider == "s3" and not self.s3_bucket:
            problems.append("S3_BUCKET is required when STORAGE_PROVIDER=s3.")
        if any(origin.strip() == "*" for origin in self.cors_origins.split(",")):
            problems.append("CORS_ORIGINS may not be '*' with credentialed requests.")
        if problems:
            raise ValueError("Unsafe production configuration:\n- " + "\n- ".join(problems))
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def secure_cookies(self) -> bool:
        return self.is_production if self.cookie_secure is None else self.cookie_secure

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def legacy_claim_enabled(self) -> bool:
        if self.claim_legacy_workspace is not None:
            return self.claim_legacy_workspace
        return self.app_env == "development"


settings = Settings()
if settings.storage_provider == "local":
    settings.data_dir.mkdir(parents=True, exist_ok=True)
