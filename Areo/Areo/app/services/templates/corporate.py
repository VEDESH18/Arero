"""Corporate: dark header bar, left-aligned body, star badge, and a footer strip."""

from reportlab.lib import colors
from reportlab.pdfgen.canvas import Canvas

from app.services.templates.common import (
    PAGE_HEIGHT, PAGE_WIDTH, SANS, SANS_BOLD, CertificateData, completion_line, draw_fitted, footer_id, star,
)

HEADER = colors.HexColor("#1E293B")
BLUE = colors.HexColor("#2563EB")
TEXT = colors.HexColor("#0F172A")
MUTED = colors.HexColor("#64748B")
HEADER_HEIGHT = 110
FOOTER_HEIGHT = 50
LEFT = 60


def render(c: Canvas, data: CertificateData) -> None:
    max_width = PAGE_WIDTH - LEFT - 230  # leave room for the badge on the right

    c.setFillColor(HEADER)
    c.rect(0, PAGE_HEIGHT - HEADER_HEIGHT, PAGE_WIDTH, HEADER_HEIGHT, stroke=0, fill=1)
    c.setFillColor(BLUE)
    c.rect(0, PAGE_HEIGHT - HEADER_HEIGHT - 6, PAGE_WIDTH, 6, stroke=0, fill=1)
    c.setFillColor(colors.white)
    draw_fitted(c, data.title.upper(), SANS_BOLD, 26, 12, LEFT, PAGE_HEIGHT - 68, PAGE_WIDTH * 0.55, align="left")
    c.setFillColor(colors.HexColor("#CBD5E1"))
    draw_fitted(c, data.issuer_name, SANS, 14, 8, PAGE_WIDTH - LEFT, PAGE_HEIGHT - 66, PAGE_WIDTH * 0.3, align="right")

    c.setFillColor(MUTED)
    c.setFont(SANS, 14)
    c.drawString(LEFT, PAGE_HEIGHT - 190, "AWARDED TO")
    c.setFillColor(TEXT)
    draw_fitted(c, data.recipient_name, SANS_BOLD, 40, 12, LEFT, PAGE_HEIGHT - 240, max_width, align="left")
    c.setFillColor(MUTED)
    c.setFont(SANS, 14)
    c.drawString(LEFT, PAGE_HEIGHT - 290, "IN RECOGNITION OF COMPLETING")
    c.setFillColor(BLUE)
    draw_fitted(c, data.course_name, SANS_BOLD, 24, 10, LEFT, PAGE_HEIGHT - 325, max_width, align="left")
    c.setFillColor(TEXT)
    completion_line(c, data, SANS, 13, LEFT, PAGE_HEIGHT - 358, align="left")

    # Badge on the right: blue disc, white ring, white star.
    cx, cy = PAGE_WIDTH - 150, PAGE_HEIGHT - 270
    c.setFillColor(BLUE)
    c.circle(cx, cy, 62, stroke=0, fill=1)
    c.setStrokeColor(colors.white)
    c.setLineWidth(2)
    c.circle(cx, cy, 52, stroke=1, fill=0)
    star(c, cx, cy, 30, colors.white)

    c.setFillColor(colors.HexColor("#F1F5F9"))
    c.rect(0, 0, PAGE_WIDTH, FOOTER_HEIGHT, stroke=0, fill=1)
    c.setFillColor(TEXT)
    c.setFont(SANS_BOLD, 11)
    c.drawString(LEFT, 20, f"Issued {data.issue_date:%d %B %Y}")
    footer_id(c, data, SANS, MUTED, 20, x=PAGE_WIDTH - LEFT, align="right")
