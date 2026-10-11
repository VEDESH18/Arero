"""Custom company templates: one configurable design driven by stored settings.

Layout, top to bottom: logo, title, body text (split around the recipient's
name, which is printed large), an "Issued ..." line, then a signature block on
the left and the verification QR code in the bottom-right corner, which is kept
free of other content.

All user text is drawn with plain canvas text calls, which never interpret
markup; control characters are stripped, and every line is shrunk, wrapped or
truncated so nothing leaves the page.
"""

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape, portrait
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfgen.canvas import Canvas

from app.services.templates.common import (
    QR_SIZE, CertificateData, QrPlacement, draw_fitted, fit_font_size, truncate_to_width,
)

# Built-in PDF fonts only (no font files to ship): family -> (regular, bold, italic).
FONTS: dict[str, tuple[str, str, str]] = {
    "Helvetica": ("Helvetica", "Helvetica-Bold", "Helvetica-Oblique"),
    "Times-Roman": ("Times-Roman", "Times-Bold", "Times-Italic"),
    "Courier": ("Courier", "Courier-Bold", "Courier-Oblique"),
}
BORDER_STYLES = ("none", "simple", "double")
LAYOUTS = ("landscape", "portrait")
PLACEHOLDERS = ("name", "course", "issuer", "date")

PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*([A-Za-z_]+)\s*\}\}")
_NAME_PLACEHOLDER = re.compile(r"\{\{\s*name\s*\}\}")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

MARGIN = 36
INSET = 40                 # content inset from the border
BOTTOM_ZONE = MARGIN + 160  # signature block and QR live below this line
BODY_NAME_SPACE = 30 + 16   # extra space above and below the large name line
TEXT = colors.HexColor("#1F2937")
MUTED = colors.HexColor("#6B7280")


def clean_text(value: str) -> str:
    return _CONTROL_CHARS.sub(" ", value).strip()


def fill_placeholders(text: str, data: CertificateData) -> str:
    values = {
        "name": data.recipient_name,
        "course": data.course_name,
        "issuer": data.issuer_name,
        "date": f"{data.issue_date:%d %B %Y}",
    }
    return PLACEHOLDER_PATTERN.sub(lambda m: values.get(m.group(1), m.group(0)), text)


@dataclass(frozen=True)
class CustomTemplateSpec:
    """Everything needed to draw a custom template. Built from a `CustomTemplate` row."""

    id: str
    name: str
    primary_color: str
    secondary_color: str
    font: str
    title_text: str
    body_text: str
    border_style: str
    layout: str
    logo_path: Path
    signature_path: Path | None = None
    signer_name: str | None = None
    signer_title: str | None = None

    @property
    def page_size(self) -> tuple[float, float]:
        return landscape(A4) if self.layout == "landscape" else portrait(A4)

    @property
    def qr(self) -> QrPlacement:
        width, _ = self.page_size
        return QrPlacement(width - MARGIN - INSET + 4 - QR_SIZE / 2, MARGIN + 14, MUTED)

    def render(self, c: Canvas, data: CertificateData) -> None:
        _render(c, data, self)


def _render(c: Canvas, data: CertificateData, spec: CustomTemplateSpec) -> None:
    width, height = spec.page_size
    primary, secondary = colors.HexColor(spec.primary_color), colors.HexColor(spec.secondary_color)
    regular, bold, italic = FONTS[spec.font]
    center = width / 2
    content_width = width - 2 * (MARGIN + INSET)

    # Fail this certificate (not the whole job) with a clear message if an image has gone missing.
    if not spec.logo_path.is_file():
        raise FileNotFoundError(f"Logo image for template '{spec.name}' is missing")
    if spec.signature_path is not None and not spec.signature_path.is_file():
        raise FileNotFoundError(f"Signature image for template '{spec.name}' is missing")

    _border(c, spec.border_style, primary, secondary, width, height)

    # Logo, centred at the top.
    top = height - MARGIN - 26
    logo_h = _draw_image(c, spec.logo_path, center, top, max_w=200, max_h=70)
    cursor = top - logo_h - 38

    # Title with an accent line under it.
    c.setFillColor(primary)
    title = clean_text(fill_placeholders(spec.title_text, data))
    draw_fitted(c, title, bold, 30, 12, center, cursor, content_width)
    c.setStrokeColor(secondary)
    c.setLineWidth(2)
    c.line(center - 60, cursor - 14, center + 60, cursor - 14)
    cursor -= 50

    # Body: the text before {{name}}, the name in large type, then the rest.
    parts = _NAME_PLACEHOLDER.split(spec.body_text, maxsplit=1)
    before, after = (parts[0], parts[1]) if len(parts) == 2 else (spec.body_text, "")
    before = clean_text(fill_placeholders(before, data))
    after = clean_text(fill_placeholders(after, data))
    name = clean_text(data.recipient_name)

    available = cursor - (BOTTOM_ZONE + 30)
    size, before_lines, after_lines = _fit_body(before, after, italic, regular, content_width, available)
    leading = size * 1.35
    block_height = (len(before_lines) + len(after_lines) + 1) * leading + BODY_NAME_SPACE
    cursor -= max(available - block_height, 0) / 2  # centre the body in the space left (matters in portrait)
    c.setFillColor(TEXT)
    for line in before_lines:
        c.setFont(italic, size)
        c.drawCentredString(center, cursor, line)
        cursor -= leading
    cursor -= 30
    c.setFillColor(primary)
    draw_fitted(c, name, bold, 38, 12, center, cursor, content_width)
    cursor -= 16 + leading
    c.setFillColor(TEXT)
    for line in after_lines:
        c.setFont(regular, size)
        c.drawCentredString(center, cursor, line)
        cursor -= leading

    # Issue line just above the bottom zone.
    issued = f"Issued {data.issue_date:%d %B %Y} by {clean_text(data.issuer_name)}"
    if data.completion_date:
        issued = f"Completed {data.completion_date:%d %B %Y}  ·  " + issued
    c.setFillColor(MUTED)
    draw_fitted(c, issued, regular, 11, 7, center, BOTTOM_ZONE - 14, content_width)

    _signature_block(c, data, spec, MARGIN + INSET + 110, MARGIN + 70, regular, bold, secondary)

    c.setFillColor(MUTED)
    c.setFont(regular, 7)
    c.drawString(MARGIN + INSET, MARGIN + 12, f"Certificate ID: {data.certificate_id}")


def _fit_body(
    before: str, after: str, italic: str, regular: str, width: float, available: float
) -> tuple[float, list[str], list[str]]:
    """Largest font size (16 down to 8) at which the wrapped body fits; truncate lines as a last resort."""
    name_space = BODY_NAME_SPACE
    for size in range(16, 7, -1):
        before_lines = _wrap(before, italic, size, width)
        after_lines = _wrap(after, regular, size, width)
        if (len(before_lines) + len(after_lines) + 1) * size * 1.35 + name_space <= available:
            return size, before_lines, after_lines
    size = 8
    max_lines = max(int((available - name_space) / (size * 1.35)) - 1, 2)
    before_lines = _wrap(before, italic, size, width)[: max_lines // 2]
    after_lines = _wrap(after, regular, size, width)[: max_lines - len(before_lines)]
    if after_lines:
        after_lines[-1] = truncate_to_width(after_lines[-1] + " ...", regular, size, width)
    return size, before_lines, after_lines


def _wrap(text: str, font: str, size: float, width: float) -> list[str]:
    if not text:
        return []
    return [truncate_to_width(line, font, size, width) for line in simpleSplit(text, font, size, width)]


def _border(c: Canvas, style: str, primary, secondary, width: float, height: float) -> None:
    if style == "none":
        return
    c.setStrokeColor(primary)
    c.setLineWidth(5 if style == "double" else 3)
    c.rect(MARGIN, MARGIN, width - 2 * MARGIN, height - 2 * MARGIN)
    if style == "double":
        c.setStrokeColor(secondary)
        c.setLineWidth(1.5)
        c.rect(MARGIN + 10, MARGIN + 10, width - 2 * (MARGIN + 10), height - 2 * (MARGIN + 10))


def _draw_image(c: Canvas, path: Path, center_x: float, top: float, max_w: float, max_h: float) -> float:
    """Draw an image scaled to fit max_w x max_h, centred on center_x with its top at `top`. Returns its height."""
    reader = ImageReader(str(path))
    img_w, img_h = reader.getSize()
    scale = min(max_w / img_w, max_h / img_h)
    w, h = img_w * scale, img_h * scale
    c.drawImage(reader, center_x - w / 2, top - h, width=w, height=h, mask="auto")
    return h


def _signature_block(
    c: Canvas, data: CertificateData, spec: CustomTemplateSpec, x: float, baseline: float, regular: str, bold: str, line_color
) -> None:
    """Signature image (optional) above a line, then signer name and title; falls back to the issuer."""
    if spec.signature_path is not None:
        _draw_image(c, spec.signature_path, x, baseline + 56, max_w=180, max_h=50)
    c.setStrokeColor(line_color)
    c.setLineWidth(1)
    c.line(x - 110, baseline, x + 110, baseline)
    name = clean_text(spec.signer_name or "") or clean_text(data.issuer_name)
    label = clean_text(spec.signer_title or "") if spec.signer_name else "Issued by"
    c.setFillColor(TEXT)
    draw_fitted(c, name, bold, 13, 7, x, baseline - 16, 220)
    if label:
        c.setFillColor(MUTED)
        size = fit_font_size(label, regular, 10, 6, 220)
        c.setFont(regular, size)
        c.drawCentredString(x, baseline - 30, truncate_to_width(label, regular, size, 220))
