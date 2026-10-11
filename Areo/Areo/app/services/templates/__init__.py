"""Template registry: one entry per design, one module per design.

The registry drives everything: the API lists it, the request schema
validates `certificate.template` against it, and the worker renders with it.
Adding a design = create a module with `render(c, data)` + add one line here,
including a `QrPlacement` in an empty area of the design for the verification QR.
"""

from collections.abc import Callable
from dataclasses import dataclass

from reportlab.lib import colors
from reportlab.pdfgen.canvas import Canvas

from app.services.templates import academic, achievement, classic, corporate, elegant, midnight, minimal, modern
from app.services.templates.common import PAGE_SIZE, CertificateData, QrPlacement


@dataclass(frozen=True)
class Template:
    id: str
    name: str
    description: str
    render: Callable[[Canvas, CertificateData], None]
    qr: QrPlacement
    page_size: tuple[float, float] = PAGE_SIZE


DEFAULT_TEMPLATE = "classic"

# QR spots (A4 landscape, 842 x 595 pt): bottom-right corner, inside each border and clear
# of the signature lines, seals and badges.
TEMPLATES: dict[str, Template] = {
    t.id: t
    for t in (
        Template("classic", "Classic", "Centered layout with a navy and gold double border.",
                 classic.render, QrPlacement(756, 58)),
        Template("modern", "Modern", "Left-aligned layout with a teal side band.",
                 modern.render, QrPlacement(752, 40)),
        Template("minimal", "Minimal", "Black-and-white serif design with a thin border; printer-friendly.",
                 minimal.render, QrPlacement(758, 60)),
        Template("elegant", "Elegant", "Cream paper, gold double border with corner ornaments, italic serif.",
                 elegant.render, QrPlacement(758, 62, colors.HexColor("#3E2C1C"))),
        Template("corporate", "Corporate", "Dark header bar, left-aligned body and a blue star badge.",
                 corporate.render, QrPlacement(692, 66, colors.HexColor("#64748B"))),
        Template("achievement", "Achievement", "Bold red title and a gold seal with ribbons; for awards.",
                 achievement.render, QrPlacement(766, 56)),
        Template("midnight", "Midnight", "Dark navy page with gold and white lettering; best on screen.",
                 midnight.render, QrPlacement(758, 56, colors.HexColor("#CBD5E1"))),
        Template("academic", "Academic", "Diploma style: institution as heading, maroon border and seal.",
                 academic.render, QrPlacement(750, 60)),
    )
}
