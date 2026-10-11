"""Public verification endpoints: no authentication, rate limited per client IP.

`GET /verify/{code}` returns JSON; `GET /verify/{code}/page` is the page the QR
code opens on a phone. Neither ever reveals the recipient's email address.
"""

from html import escape
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.api.docs import error
from app.database import get_db
from app.schemas import VerificationNotFound, VerificationResult
from app.services.verification import VerifiedCertificate, find_verified_certificate

router = APIRouter(prefix="/verify", tags=["3. Certificates"])

DbSession = Annotated[Session, Depends(get_db)]
NOT_FOUND_MESSAGE = "Certificate not found"


def rate_limit(request: Request) -> None:
    """Slow down code guessing: at most VERIFY_RATE_LIMIT_PER_MINUTE requests per IP."""
    client = request.client.host if request.client else "unknown"
    retry_after = request.app.state.verify_limiter.hit(client)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="Too many verification requests; please wait and try again.",
            headers={"Retry-After": str(int(retry_after) + 1)},
        )


RATE_LIMITED = {429: error("Too many requests from this IP; see the `Retry-After` header.")}


@router.get(
    "/{code}",
    response_model=VerificationResult,
    summary="Verify a certificate (JSON)",
    dependencies=[Depends(rate_limit)],
    responses={404: {"model": VerificationNotFound, "description": "Unknown, not yet issued or invalid code."}, **RATE_LIMITED},
)
def verify_certificate(code: str, db: DbSession) -> VerificationResult | JSONResponse:
    """Public check that a certificate is genuine. `code` is printed under the QR code on the PDF."""
    found = find_verified_certificate(db, code)
    if found is None:
        return JSONResponse(status_code=404, content=VerificationNotFound(message=NOT_FOUND_MESSAGE).model_dump())
    return VerificationResult(
        recipient_name=found.recipient_name,
        course_name=found.course_name,
        issuer=found.issuer,
        completion_date=found.completion_date,
        issued_at=found.issued_at,
    )


@router.get(
    "/{code}/page",
    response_class=HTMLResponse,
    summary="Verify a certificate (web page)",
    dependencies=[Depends(rate_limit)],
    responses={
        200: {"content": {"text/html": {}}, "description": "Green 'VALID CERTIFICATE' page."},
        404: {"content": {"text/html": {}}, "description": "Red 'CERTIFICATE NOT FOUND' page."},
        **RATE_LIMITED,
    },
)
def verify_certificate_page(code: str, db: DbSession) -> HTMLResponse:
    """The page the QR code opens: a clear valid / not-found verdict that works on a phone."""
    found = find_verified_certificate(db, code)
    if found is None:
        return HTMLResponse(_not_found_page(), status_code=404)
    return HTMLResponse(_valid_page(found))


# ---------- HTML ----------
# Every value from the database is passed through html.escape before it is inserted.

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{title}</title>
<style>
  :root {{ color-scheme: light; }}
  body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         background: #f3f4f6; color: #111827; }}
  main {{ max-width: 520px; margin: 0 auto; padding: 24px 16px; }}
  .verdict {{ border-radius: 16px; padding: 28px 20px; text-align: center; color: #fff; }}
  .verdict .icon {{ font-size: 56px; line-height: 1; }}
  .verdict h1 {{ margin: 12px 0 4px; font-size: 26px; letter-spacing: .5px; }}
  .verdict p {{ margin: 0; opacity: .95; }}
  .valid {{ background: #15803d; }}
  .invalid {{ background: #b91c1c; }}
  dl {{ background: #fff; border-radius: 16px; padding: 8px 20px; margin: 16px 0 0;
        box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  dt {{ font-size: 13px; color: #6b7280; margin-top: 14px; }}
  dd {{ margin: 2px 0 14px; font-size: 18px; font-weight: 600; overflow-wrap: anywhere; }}
  dd + dt {{ border-top: 1px solid #e5e7eb; padding-top: 14px; }}
  footer {{ text-align: center; font-size: 13px; color: #6b7280; margin-top: 20px; }}
</style>
</head>
<body><main>{body}<footer>Bulk Certificate Generator &middot; public verification</footer></main></body>
</html>
"""


def _valid_page(cert: VerifiedCertificate) -> str:
    rows = [
        ("Awarded to", cert.recipient_name),
        ("Course", cert.course_name),
        ("Issued by", cert.issuer),
    ]
    if cert.completion_date:
        rows.append(("Completed on", f"{cert.completion_date:%d %B %Y}"))
    if cert.issued_at:
        rows.append(("Issued on", f"{cert.issued_at:%d %B %Y}"))
    details = "".join(f"<dt>{escape(label)}</dt><dd>{escape(value)}</dd>" for label, value in rows)
    body = (
        '<section class="verdict valid"><div class="icon">&#10004;</div>'
        "<h1>VALID CERTIFICATE</h1><p>This certificate is genuine and was issued as shown below.</p></section>"
        f"<dl>{details}</dl>"
    )
    return _PAGE.format(title="Valid certificate", body=body)


def _not_found_page() -> str:
    body = (
        '<section class="verdict invalid"><div class="icon">&#10006;</div>'
        "<h1>CERTIFICATE NOT FOUND</h1><p>Possibly fake: no issued certificate matches this code.</p></section>"
        "<dl><dt>What to do</dt><dd>Check that you scanned the QR code on the original certificate, "
        "or contact the issuing organisation.</dd></dl>"
    )
    return _PAGE.format(title="Certificate not found", body=body)
