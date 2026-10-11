"""Settings the background worker needs to render a certificate, beyond the job data itself."""

from dataclasses import dataclass
from pathlib import Path

from app.config import Settings


@dataclass(frozen=True)
class RenderContext:
    public_base_url: str = "http://127.0.0.1:8000"  # QR codes link to {public_base_url}/api/v1/verify/...
    upload_dir: Path | None = None                    # where custom template logos/signatures are stored

    @classmethod
    def from_settings(cls, settings: Settings) -> "RenderContext":
        return cls(public_base_url=settings.public_base_url, upload_dir=settings.upload_dir)
