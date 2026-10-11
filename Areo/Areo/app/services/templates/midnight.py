"""Midnight: dark navy page with gold and white lettering (best viewed on screen)."""

from reportlab.lib import colors
from reportlab.pdfgen.canvas import Canvas

from app.services.templates.common import (
    CENTER_X, MARGIN, PAGE_HEIGHT, PAGE_WIDTH, SANS, SANS_BOLD,
    CertificateData, border, completion_line, draw_fitted, fill_page, footer_id, signature_block, star,
)

BACKGROUND = colors.HexColor("#0F172A")
GOLD = colors.HexColor("#EAB308")
WHITE = colors.white
SOFT = colors.HexColor("#CBD5E1")


def render(c: Canvas, data: CertificateData) -> None:
    max_width = PAGE_WIDTH - 2 * MARGIN - 100
    fill_page(c, BACKGROUND)
    border(c, MARGIN, GOLD, 1)
    border(c, MARGIN + 6, GOLD, 0.4)
    for offset in (-28, 0, 28):
        star(c, CENTER_X + offset, PAGE_HEIGHT - 95, 7 if offset else 10, GOLD)

    c.setFillColor(GOLD)
    draw_fitted(c, " ".join(data.title.upper()), SANS_BOLD, 22, 10, CENTER_X, PAGE_HEIGHT - 150, max_width)
    c.setFillColor(SOFT)
    c.setFont(SANS, 15)
    c.drawCentredString(CENTER_X, PAGE_HEIGHT - 200, "presented to")
    c.setFillColor(WHITE)
    draw_fitted(c, data.recipient_name, SANS_BOLD, 46, 12, CENTER_X, PAGE_HEIGHT - 260, max_width)
    c.setStrokeColor(GOLD)
    c.setLineWidth(1)
    c.line(CENTER_X - 140, PAGE_HEIGHT - 280, CENTER_X + 140, PAGE_HEIGHT - 280)

    c.setFillColor(SOFT)
    c.setFont(SANS, 15)
    c.drawCentredString(CENTER_X, PAGE_HEIGHT - 318, "for successfully completing")
    c.setFillColor(GOLD)
    draw_fitted(c, data.course_name, SANS_BOLD, 24, 10, CENTER_X, PAGE_HEIGHT - 352, max_width)
    c.setFillColor(SOFT)
    completion_line(c, data, SANS, 13, CENTER_X, PAGE_HEIGHT - 382)

    signature_block(c, data, SANS_BOLD, SANS, WHITE, baseline=MARGIN + 100)
    footer_id(c, data, SANS, SOFT, MARGIN + 22)
