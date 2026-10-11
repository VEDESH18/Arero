"""Modern: left-aligned layout with a solid teal side band holding issuer and date."""

from reportlab.lib import colors
from reportlab.pdfgen.canvas import Canvas

from app.services.templates.common import (
    PAGE_HEIGHT, PAGE_WIDTH, SANS, SANS_BOLD, CertificateData, completion_line, draw_fitted, footer_id,
)

BAND = colors.HexColor("#0F766E")
ACCENT = colors.HexColor("#F59E0B")
TEXT = colors.HexColor("#1F2937")
BAND_WIDTH = 190


def render(c: Canvas, data: CertificateData) -> None:
    left = BAND_WIDTH + 60
    max_width = PAGE_WIDTH - left - 60

    c.setFillColor(BAND)
    c.rect(0, 0, BAND_WIDTH, PAGE_HEIGHT, stroke=0, fill=1)
    c.setFillColor(ACCENT)
    c.rect(BAND_WIDTH, 0, 8, PAGE_HEIGHT, stroke=0, fill=1)

    c.setFillColor(colors.white)
    draw_fitted(c, data.issuer_name, SANS_BOLD, 18, 9, 30, PAGE_HEIGHT - 80, BAND_WIDTH - 50, align="left")
    c.setFont(SANS, 11)
    c.drawString(30, 100, "Issued on")
    c.setFont(SANS_BOLD, 13)
    c.drawString(30, 82, f"{data.issue_date:%d %b %Y}")

    c.setFillColor(BAND)
    draw_fitted(c, data.title.upper(), SANS_BOLD, 30, 14, left, PAGE_HEIGHT - 130, max_width, align="left")
    c.setFillColor(TEXT)
    c.setFont(SANS, 15)
    c.drawString(left, PAGE_HEIGHT - 190, "Presented to")
    draw_fitted(c, data.recipient_name, SANS_BOLD, 44, 12, left, PAGE_HEIGHT - 250, max_width, align="left")
    c.setStrokeColor(ACCENT)
    c.setLineWidth(3)
    c.line(left, PAGE_HEIGHT - 268, left + 120, PAGE_HEIGHT - 268)

    c.setFont(SANS, 15)
    c.drawString(left, PAGE_HEIGHT - 310, "for successfully completing")
    c.setFillColor(BAND)
    draw_fitted(c, data.course_name, SANS_BOLD, 24, 10, left, PAGE_HEIGHT - 345, max_width, align="left")
    c.setFillColor(TEXT)
    completion_line(c, data, SANS, 13, left, PAGE_HEIGHT - 378, align="left")

    footer_id(c, data, SANS, colors.grey, 40, x=left, align="left")
