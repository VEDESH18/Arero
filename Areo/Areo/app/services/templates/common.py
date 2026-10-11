"""Shared building blocks for every certificate template.

Templates only draw; they never touch the database or files. Each one is a
function `render(canvas, data)` that uses the helpers below, so long text
shrinks to fit (and finally truncates) the same way in every design.
"""

import math
from dataclasses import dataclass
from datetime import date

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.colors import Color
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

PAGE_SIZE = landscape(A4)
PAGE_WIDTH, PAGE_HEIGHT = PAGE_SIZE
CENTER_X = PAGE_WIDTH / 2
MARGIN = 36

SANS, SANS_BOLD, SANS_ITALIC = "Helvetica", "Helvetica-Bold", "Helvetica-Oblique"
SERIF, SERIF_BOLD, SERIF_ITALIC, SERIF_BOLD_ITALIC = "Times-Roman", "Times-Bold", "Times-Italic", "Times-BoldItalic"


@dataclass(frozen=True)
class CertificateData:
    title: str
    recipient_name: str
    course_name: str
    issuer_name: str
    issue_date: date
    certificate_id: str
    completion_date: date | None = None
    verification_code: str | None = None  # printed under the QR code
    verification_url: str | None = None   # encoded in the QR code; no QR is drawn without it


@dataclass(frozen=True)
class QrPlacement:
    """Where a template puts its verification QR: centre x and the bottom of the block (caption included).

    Each built-in template picks a spot that is empty in its design, so the QR never overlaps content.
    """

    center_x: float
    bottom: float
    caption_color: Color = colors.HexColor("#555555")


def fit_font_size(text: str, font: str, max_size: float, min_size: float, max_width: float) -> float:
    """Largest font size (down to min_size) at which `text` fits within max_width."""
    size = max_size
    while size > min_size and stringWidth(text, font, size) > max_width:
        size -= 1
    return size


def truncate_to_width(text: str, font: str, size: float, max_width: float) -> str:
    """Last resort for absurdly long text: cut it with an ellipsis so it never overflows."""
    if stringWidth(text, font, size) <= max_width:
        return text
    while text and stringWidth(text + "...", font, size) > max_width:
        text = text[:-1]
    return text + "..."


def draw_fitted(
    c: Canvas,
    text: str,
    font: str,
    max_size: float,
    min_size: float,
    x: float,
    y: float,
    max_width: float,
    align: str = "center",
) -> None:
    """Draw text that shrinks (then truncates) to stay within max_width. align: center | left | right."""
    size = fit_font_size(text, font, max_size, min_size, max_width)
    text = truncate_to_width(text, font, size, max_width)
    c.setFont(font, size)
    if align == "center":
        c.drawCentredString(x, y, text)
    elif align == "right":
        c.drawRightString(x, y, text)
    else:
        c.drawString(x, y, text)


def fill_page(c: Canvas, color: Color) -> None:
    c.setFillColor(color)
    c.rect(0, 0, PAGE_WIDTH, PAGE_HEIGHT, stroke=0, fill=1)


def border(c: Canvas, inset: float, color: Color, width: float) -> None:
    c.setStrokeColor(color)
    c.setLineWidth(width)
    c.rect(inset, inset, PAGE_WIDTH - 2 * inset, PAGE_HEIGHT - 2 * inset)


def star(c: Canvas, cx: float, cy: float, outer: float, color: Color, points: int = 5) -> None:
    """Filled star polygon centred on (cx, cy)."""
    inner = outer * 0.45
    path = c.beginPath()
    for i in range(points * 2):
        radius = outer if i % 2 == 0 else inner
        angle = math.pi / 2 + i * math.pi / points
        x, y = cx + radius * math.cos(angle), cy + radius * math.sin(angle)
        path.moveTo(x, y) if i == 0 else path.lineTo(x, y)
    path.close()
    c.setFillColor(color)
    c.drawPath(path, stroke=0, fill=1)


def diamond(c: Canvas, cx: float, cy: float, size: float, color: Color) -> None:
    path = c.beginPath()
    path.moveTo(cx, cy + size)
    path.lineTo(cx + size, cy)
    path.lineTo(cx, cy - size)
    path.lineTo(cx - size, cy)
    path.close()
    c.setFillColor(color)
    c.drawPath(path, stroke=0, fill=1)


def signature_block(
    c: Canvas,
    data: CertificateData,
    bold: str,
    regular: str,
    color: Color,
    baseline: float,
    positions: tuple[float, float] = (0.28, 0.72),
) -> None:
    """Issuer and issue date, each above a signature line with a small label below."""
    c.setFillColor(color)
    c.setStrokeColor(color)
    c.setLineWidth(1)
    for fraction, value, label in (
        (positions[0], data.issuer_name, "Issued by"),
        (positions[1], f"{data.issue_date:%d %B %Y}", "Date of issue"),
    ):
        x = PAGE_WIDTH * fraction
        c.line(x - 110, baseline, x + 110, baseline)
        draw_fitted(c, value, bold, 14, 8, x, baseline + 8, 220)
        c.setFont(regular, 11)
        c.drawCentredString(x, baseline - 16, label)


def completion_line(c: Canvas, data: CertificateData, font: str, size: float, x: float, y: float, align: str = "center") -> None:
    """Optional 'Completed on …' line, only when the recipient has a completion date."""
    if not data.completion_date:
        return
    text = f"Completed on {data.completion_date:%d %B %Y}"
    c.setFont(font, size)
    if align == "center":
        c.drawCentredString(x, y, text)
    else:
        c.drawString(x, y, text)


def footer_id(c: Canvas, data: CertificateData, font: str, color: Color, y: float, x: float = CENTER_X, align: str = "center") -> None:
    c.setFont(font, 9)
    c.setFillColor(color)
    text = f"Certificate ID: {data.certificate_id}"
    if align == "center":
        c.drawCentredString(x, y, text)
    elif align == "right":
        c.drawRightString(x, y, text)
    else:
        c.drawString(x, y, text)


QR_SIZE = 60          # side of the QR square, in points (~21 mm)
QR_CAPTION_SPACE = 17  # room under the QR for "Scan to verify" + the code
QR_PADDING = 3        # white margin around the QR so it scans on dark or tinted pages


def draw_verification_qr(c: Canvas, data: CertificateData, placement: QrPlacement) -> None:
    """QR code linking to the public verification page, with a caption and the code underneath.

    The QR is also a clickable link, so the PDF can be verified on screen without a phone.
    """
    if not data.verification_url:
        return
    widget = QrCodeWidget(data.verification_url, barLevel="M", barBorder=1)
    x0, y0, x1, y1 = widget.getBounds()
    drawing = Drawing(QR_SIZE, QR_SIZE, transform=[QR_SIZE / (x1 - x0), 0, 0, QR_SIZE / (y1 - y0), 0, 0])
    drawing.add(widget)

    x = placement.center_x - QR_SIZE / 2
    y = placement.bottom + QR_CAPTION_SPACE
    c.setFillColor(colors.white)
    c.rect(x - QR_PADDING, y - QR_PADDING, QR_SIZE + 2 * QR_PADDING, QR_SIZE + 2 * QR_PADDING, stroke=0, fill=1)
    renderPDF.draw(drawing, c, x, y)
    c.linkURL(data.verification_url, (x, y, x + QR_SIZE, y + QR_SIZE), relative=0)

    c.setFillColor(placement.caption_color)
    c.setFont(SANS_BOLD, 6.5)
    c.drawCentredString(placement.center_x, placement.bottom + 8, "Scan to verify")
    if data.verification_code:
        c.setFont(SANS, 5.5)
        c.drawCentredString(placement.center_x, placement.bottom + 1, data.verification_code)
