"""Render certificates to PDF with ReportLab.

The designs live in `app/services/templates/` (one module per template, all
listed in the `TEMPLATES` registry). This module is the single entry point the
rest of the app uses: render a real certificate to a file, or a sample to bytes.

A template is either a built-in id ("classic", ...) or an already-resolved
template object (a custom company template); both expose `render`, `qr` and
`page_size`. Every certificate gets the verification QR code on top of the design.
"""

import dataclasses
import io
from datetime import date
from pathlib import Path
from typing import Protocol

from reportlab.pdfgen import canvas
from reportlab.pdfgen.canvas import Canvas

from app.services.templates import DEFAULT_TEMPLATE, TEMPLATES, Template
from app.services.templates.common import CertificateData, QrPlacement, draw_verification_qr
from app.services.verification import verification_url

__all__ = [
    "DEFAULT_TEMPLATE",
    "TEMPLATES",
    "CertificateData",
    "Template",
    "RenderableTemplate",
    "generate_certificate_pdf",
    "render_preview_pdf",
    "SAMPLE_DATA",
]

SAMPLE_VERIFICATION_CODE = "SAMPLE-PREVIEW"

SAMPLE_DATA = CertificateData(
    title="Certificate of Completion",
    recipient_name="Jane Doe",
    course_name="Sample Course",
    issuer_name="Example Academy",
    issue_date=date(2026, 1, 15),
    certificate_id="PREVIEW",
    completion_date=date(2026, 1, 10),
    verification_code=SAMPLE_VERIFICATION_CODE,
)


class RenderableTemplate(Protocol):
    @property
    def qr(self) -> QrPlacement: ...

    @property
    def page_size(self) -> tuple[float, float]: ...

    def render(self, c: Canvas, data: CertificateData) -> None: ...


def _resolve(template: "str | RenderableTemplate") -> RenderableTemplate:
    if not isinstance(template, str):
        return template
    found = TEMPLATES.get(template)
    if found is None:
        raise ValueError(f"Unknown template '{template}'")
    return found


def _draw(target, data: CertificateData, template: "str | RenderableTemplate") -> None:
    resolved = _resolve(template)
    if not data.recipient_name.strip():
        raise ValueError("recipient_name is empty")

    c = canvas.Canvas(target, pagesize=resolved.page_size)
    c.setTitle(f"{data.title} - {data.recipient_name}")
    c.setAuthor(data.issuer_name)
    resolved.render(c, data)
    draw_verification_qr(c, data, resolved.qr)
    c.showPage()
    c.save()


def generate_certificate_pdf(
    data: CertificateData, output_path: Path, template_id: "str | RenderableTemplate" = DEFAULT_TEMPLATE
) -> None:
    """Render one certificate to `output_path` (the caller handles atomic rename)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _draw(str(output_path), data, template_id)


def render_preview_pdf(template: "str | RenderableTemplate", public_base_url: str = "http://127.0.0.1:8000") -> bytes:
    """A sample certificate in memory (with a sample QR), so users can see a template before choosing it."""
    data = dataclasses.replace(
        SAMPLE_DATA, verification_url=verification_url(public_base_url, SAMPLE_VERIFICATION_CODE)
    )
    buffer = io.BytesIO()
    _draw(buffer, data, template)
    return buffer.getvalue()
