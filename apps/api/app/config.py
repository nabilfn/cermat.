from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://cermat:cermat@db:5432/cermat"
    openai_api_key: str = ""
    openai_model: str = "gpt-5.6-luna"
    data_dir: Path = Path("/app/data/uploads")
    max_upload_bytes: int = 15 * 1024 * 1024

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
