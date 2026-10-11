"""Academic: diploma style with the institution as the heading and a maroon seal."""

from reportlab.lib import colors
from reportlab.pdfgen.canvas import Canvas

from app.services.templates.common import (
    CENTER_X, MARGIN, PAGE_HEIGHT, PAGE_WIDTH, SERIF, SERIF_BOLD, SERIF_BOLD_ITALIC, SERIF_ITALIC,
    CertificateData, border, completion_line, draw_fitted, fill_page, footer_id, signature_block, star,
)

PAPER = colors.HexColor("#FFFDF7")
MAROON = colors.HexColor("#7F1D1D")
GOLD = colors.HexColor("#C5A253")
INK = colors.HexColor("#1C1917")


def render(c: Canvas, data: CertificateData) -> None:
    max_width = PAGE_WIDTH - 2 * MARGIN - 140
    fill_page(c, PAPER)
    border(c, MARGIN, MAROON, 8)
    border(c, MARGIN + 14, GOLD, 1)

    c.setFillColor(MAROON)
    draw_fitted(c, data.issuer_name.upper(), SERIF_BOLD, 28, 12, CENTER_X, PAGE_HEIGHT - 120, max_width)
    c.setFillColor(INK)
    draw_fitted(c, data.title, SERIF_ITALIC, 20, 10, CENTER_X, PAGE_HEIGHT - 155, max_width)

    c.setFont(SERIF_ITALIC, 16)
    c.drawCentredString(CENTER_X, PAGE_HEIGHT - 205, "Be it known that")
    draw_fitted(c, data.recipient_name, SERIF_BOLD, 44, 12, CENTER_X, PAGE_HEIGHT - 260, max_width)
    c.setFont(SERIF, 16)
    c.drawCentredString(CENTER_X, PAGE_HEIGHT - 300, "has satisfactorily completed the requirements of")
    c.setFillColor(MAROON)
    draw_fitted(c, data.course_name, SERIF_BOLD_ITALIC, 26, 10, CENTER_X, PAGE_HEIGHT - 338, max_width)
    c.setFillColor(INK)
    completion_line(c, data, SERIF_ITALIC, 13, CENTER_X, PAGE_HEIGHT - 368)

    # Signatures sit slightly left of symmetric to leave the bottom-right corner for the QR code.
    signature_block(c, data, SERIF_BOLD, SERIF, INK, baseline=MARGIN + 110, positions=(0.24, 0.70))
    _seal(c, CENTER_X, MARGIN + 105)
    footer_id(c, data, SERIF, colors.grey, MARGIN + 30)


def _seal(c: Canvas, cx: float, cy: float) -> None:
    c.setFillColor(MAROON)
    c.circle(cx, cy, 40, stroke=0, fill=1)
    c.setStrokeColor(GOLD)
    c.setLineWidth(2)
    c.circle(cx, cy, 33, stroke=1, fill=0)
    star(c, cx, cy, 18, GOLD)
