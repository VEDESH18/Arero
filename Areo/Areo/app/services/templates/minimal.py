"""Minimal: black-and-white serif layout with a single thin border (printer-friendly)."""

from reportlab.lib import colors
from reportlab.pdfgen.canvas import Canvas

from app.services.templates.common import (
    CENTER_X, MARGIN, PAGE_HEIGHT, PAGE_WIDTH, SERIF, SERIF_BOLD, SERIF_ITALIC,
    CertificateData, border, completion_line, draw_fitted, footer_id, signature_block,
)

INK = colors.black
MUTED = colors.HexColor("#555555")


def render(c: Canvas, data: CertificateData) -> None:
    max_width = PAGE_WIDTH - 2 * MARGIN - 120
    border(c, MARGIN + 10, INK, 0.75)

    c.setFillColor(MUTED)
    draw_fitted(c, " ".join(data.title.upper()), SERIF, 18, 9, CENTER_X, PAGE_HEIGHT - 140, max_width)
    c.setFillColor(INK)
    draw_fitted(c, data.recipient_name, SERIF_BOLD, 46, 12, CENTER_X, PAGE_HEIGHT - 240, max_width)
    c.setFillColor(MUTED)
    c.setFont(SERIF_ITALIC, 16)
    c.drawCentredString(CENTER_X, PAGE_HEIGHT - 290, "has completed")
    c.setFillColor(INK)
    draw_fitted(c, data.course_name, SERIF, 26, 10, CENTER_X, PAGE_HEIGHT - 330, max_width)
    c.setFillColor(MUTED)
    completion_line(c, data, SERIF, 13, CENTER_X, PAGE_HEIGHT - 362)

    signature_block(c, data, SERIF_BOLD, SERIF, INK, baseline=MARGIN + 100)
    footer_id(c, data, SERIF, MUTED, MARGIN + 26)
