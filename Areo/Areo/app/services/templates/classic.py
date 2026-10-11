"""Classic: centered layout, navy double border with a gold inner line."""

from reportlab.lib import colors
from reportlab.pdfgen.canvas import Canvas

from app.services.templates.common import (
    CENTER_X, MARGIN, PAGE_HEIGHT, PAGE_WIDTH, SANS, SANS_BOLD, SANS_ITALIC,
    CertificateData, border, completion_line, draw_fitted, footer_id, signature_block,
)

PRIMARY = colors.HexColor("#1F3B6F")
ACCENT = colors.HexColor("#C9A227")
TEXT = colors.HexColor("#333333")


def render(c: Canvas, data: CertificateData) -> None:
    max_width = PAGE_WIDTH - 2 * MARGIN - 80
    border(c, MARGIN, PRIMARY, 6)
    border(c, MARGIN + 12, ACCENT, 2)

    c.setFillColor(PRIMARY)
    draw_fitted(c, data.title.upper(), SANS_BOLD, 36, 18, CENTER_X, PAGE_HEIGHT - 150, max_width)
    c.setFillColor(TEXT)
    c.setFont(SANS_ITALIC, 16)
    c.drawCentredString(CENTER_X, PAGE_HEIGHT - 210, "This is to certify that")
    c.setFillColor(PRIMARY)
    draw_fitted(c, data.recipient_name, SANS_BOLD, 40, 12, CENTER_X, PAGE_HEIGHT - 270, max_width)
    c.setStrokeColor(ACCENT)
    c.setLineWidth(1)
    c.line(CENTER_X - 200, PAGE_HEIGHT - 285, CENTER_X + 200, PAGE_HEIGHT - 285)

    c.setFillColor(TEXT)
    c.setFont(SANS, 16)
    c.drawCentredString(CENTER_X, PAGE_HEIGHT - 325, "has successfully completed")
    c.setFillColor(PRIMARY)
    draw_fitted(c, data.course_name, SANS_BOLD, 24, 10, CENTER_X, PAGE_HEIGHT - 360, max_width)
    c.setFillColor(TEXT)
    completion_line(c, data, SANS, 14, CENTER_X, PAGE_HEIGHT - 395)

    signature_block(c, data, SANS_BOLD, SANS, TEXT, baseline=MARGIN + 110)
    footer_id(c, data, SANS, colors.grey, MARGIN + 24)
