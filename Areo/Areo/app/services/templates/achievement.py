"""Achievement: bold red title, thin red frame, and a gold seal with ribbon tails."""

from reportlab.lib import colors
from reportlab.pdfgen.canvas import Canvas

from app.services.templates.common import (
    CENTER_X, MARGIN, PAGE_HEIGHT, PAGE_WIDTH, SANS, SANS_BOLD, SANS_ITALIC,
    CertificateData, border, completion_line, draw_fitted, footer_id, signature_block, star,
)

RED = colors.HexColor("#B91C1C")
GOLD = colors.HexColor("#D4A017")
TEXT = colors.HexColor("#111827")


def render(c: Canvas, data: CertificateData) -> None:
    max_width = PAGE_WIDTH - 2 * MARGIN - 100
    c.setFillColor(RED)
    c.rect(0, PAGE_HEIGHT - 14, PAGE_WIDTH, 14, stroke=0, fill=1)
    c.rect(0, 0, PAGE_WIDTH, 14, stroke=0, fill=1)
    border(c, MARGIN, RED, 1.5)

    c.setFillColor(RED)
    draw_fitted(c, data.title.upper(), SANS_BOLD, 38, 16, CENTER_X, PAGE_HEIGHT - 130, max_width)
    c.setFillColor(TEXT)
    c.setFont(SANS_ITALIC, 16)
    c.drawCentredString(CENTER_X, PAGE_HEIGHT - 185, "is proudly awarded to")
    draw_fitted(c, data.recipient_name, SANS_BOLD, 44, 12, CENTER_X, PAGE_HEIGHT - 245, max_width)
    c.setStrokeColor(GOLD)
    c.setLineWidth(2)
    c.line(CENTER_X - 160, PAGE_HEIGHT - 262, CENTER_X + 160, PAGE_HEIGHT - 262)

    c.setFont(SANS, 16)
    c.drawCentredString(CENTER_X, PAGE_HEIGHT - 300, "for outstanding achievement in")
    c.setFillColor(RED)
    draw_fitted(c, data.course_name, SANS_BOLD, 24, 10, CENTER_X, PAGE_HEIGHT - 335, max_width)
    c.setFillColor(TEXT)
    completion_line(c, data, SANS, 13, CENTER_X, PAGE_HEIGHT - 365)

    signature_block(c, data, SANS_BOLD, SANS, TEXT, baseline=MARGIN + 100, positions=(0.22, 0.5))
    _seal(c, PAGE_WIDTH * 0.8, MARGIN + 110)
    footer_id(c, data, SANS, colors.grey, MARGIN + 18)


def _seal(c: Canvas, cx: float, cy: float) -> None:
    """Gold seal: two red ribbon tails behind a gold disc with a ring and a star."""
    c.setFillColor(RED)
    for direction in (-1, 1):
        path = c.beginPath()
        path.moveTo(cx + direction * 10, cy)
        path.lineTo(cx + direction * 40, cy - 75)
        path.lineTo(cx + direction * 22, cy - 62)
        path.lineTo(cx + direction * 8, cy - 80)
        path.lineTo(cx - direction * 6, cy - 10)
        path.close()
        c.drawPath(path, stroke=0, fill=1)
    c.setFillColor(GOLD)
    c.circle(cx, cy, 46, stroke=0, fill=1)
    c.setStrokeColor(colors.white)
    c.setLineWidth(1.5)
    c.circle(cx, cy, 38, stroke=1, fill=0)
    star(c, cx, cy, 24, colors.white)
