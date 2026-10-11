"""Elegant: cream paper, gold double border with corner ornaments, italic serif lettering."""

from reportlab.lib import colors
from reportlab.pdfgen.canvas import Canvas

from app.services.templates.common import (
    CENTER_X, MARGIN, PAGE_HEIGHT, PAGE_WIDTH, SERIF, SERIF_BOLD, SERIF_BOLD_ITALIC, SERIF_ITALIC,
    CertificateData, border, completion_line, diamond, draw_fitted, fill_page, footer_id, signature_block,
)

PAPER = colors.HexColor("#FBF7EE")
GOLD = colors.HexColor("#B08D57")
INK = colors.HexColor("#3E2C1C")


def render(c: Canvas, data: CertificateData) -> None:
    max_width = PAGE_WIDTH - 2 * MARGIN - 140
    fill_page(c, PAPER)
    border(c, MARGIN, GOLD, 3)
    border(c, MARGIN + 8, GOLD, 0.75)
    for x in (MARGIN + 8, PAGE_WIDTH - MARGIN - 8):
        for y in (MARGIN + 8, PAGE_HEIGHT - MARGIN - 8):
            diamond(c, x, y, 9, GOLD)

    c.setFillColor(GOLD)
    draw_fitted(c, data.title, SERIF_ITALIC, 40, 18, CENTER_X, PAGE_HEIGHT - 145, max_width)
    c.setFillColor(INK)
    c.setFont(SERIF_ITALIC, 16)
    c.drawCentredString(CENTER_X, PAGE_HEIGHT - 195, "This certificate is proudly presented to")
    draw_fitted(c, data.recipient_name, SERIF_BOLD_ITALIC, 46, 12, CENTER_X, PAGE_HEIGHT - 258, max_width)

    # Flourish: two gold lines meeting at a small diamond.
    c.setStrokeColor(GOLD)
    c.setLineWidth(1)
    c.line(CENTER_X - 180, PAGE_HEIGHT - 278, CENTER_X - 12, PAGE_HEIGHT - 278)
    c.line(CENTER_X + 12, PAGE_HEIGHT - 278, CENTER_X + 180, PAGE_HEIGHT - 278)
    diamond(c, CENTER_X, PAGE_HEIGHT - 278, 5, GOLD)

    c.setFillColor(INK)
    c.setFont(SERIF, 16)
    c.drawCentredString(CENTER_X, PAGE_HEIGHT - 318, "in recognition of successfully completing")
    draw_fitted(c, data.course_name, SERIF_BOLD, 26, 10, CENTER_X, PAGE_HEIGHT - 352, max_width)
    completion_line(c, data, SERIF_ITALIC, 13, CENTER_X, PAGE_HEIGHT - 382)

    signature_block(c, data, SERIF_BOLD, SERIF_ITALIC, INK, baseline=MARGIN + 105)
    footer_id(c, data, SERIF, GOLD, MARGIN + 24)
