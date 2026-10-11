"""Application settings, loaded from environment variables or a .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./certificates.db"
    storage_dir: Path = Path("./storage")
    worker_threads: int = 4
    max_recipients_per_job: int = 1000          # per request (POST /jobs or one PATCH batch)
    max_total_recipients_per_job: int = 5000    # across the job's lifetime, including PATCH additions
    log_level: str = "INFO"

    # Public verification. The QR code on every certificate links to
    # {PUBLIC_BASE_URL}/api/v1/verify/{code}/page, so set this to the address recipients can reach.
    public_base_url: str = "http://127.0.0.1:8000"
    verify_rate_limit_per_minute: int = 30      # per client IP on /verify endpoints; 0 disables

    # Custom template uploads (logo / signature images).
    upload_dir: Path = Path("./uploads")
    max_upload_bytes: int = 2 * 1024 * 1024
    max_image_dimension: int = 4000             # larger images are rejected outright
    image_downscale_to: int = 1000              # accepted images are shrunk to fit this box

    # Email delivery (optional). Emailing is disabled until SMTP_HOST and SMTP_FROM are set.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_starttls: bool = True   # port 587: upgrade the connection with STARTTLS
    smtp_use_ssl: bool = False   # port 465: implicit TLS from the start
    smtp_from: str | None = None  # e.g. "Acme Academy <certificates@acme.example>"
    smtp_timeout: float = 30.0

    @property
    def email_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_from)


@lru_cache
def get_settings() -> Settings:
    return Settings()
