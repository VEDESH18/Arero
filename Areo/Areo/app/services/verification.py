"""Public certificate verification.

Every certificate has an unguessable `verification_code` (see `models.new_verification_code`).
The QR code on the PDF links to the HTML verify page for that code; anyone can
open it without logging in. Only issued (SUCCEEDED) and VALID certificates verify;
everything else, including unknown codes, is reported as "not found".
"""

import re
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Certificate, CertificateStatus, Validity

VERIFY_PATH = "/api/v1/verify/{code}"

# Cheap pre-check so obviously malformed codes never reach the database.
_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,32}$")


def verification_url(public_base_url: str, code: str) -> str:
    """Absolute URL of the human-readable verify page, as encoded in the QR code."""
    return public_base_url.rstrip("/") + VERIFY_PATH.format(code=code) + "/page"


@dataclass(frozen=True)
class VerifiedCertificate:
    """What the public may see. Deliberately has no email address or internal ids."""

    recipient_name: str
    course_name: str
    issuer: str
    completion_date: date | None
    issued_at: datetime | None


def find_verified_certificate(db: Session, code: str) -> VerifiedCertificate | None:
    if not _CODE_PATTERN.fullmatch(code):
        return None
    cert = db.scalar(select(Certificate).where(Certificate.verification_code == code))
    if cert is None or cert.status != CertificateStatus.SUCCEEDED or cert.validity != Validity.VALID:
        return None
    job = cert.job
    return VerifiedCertificate(
        recipient_name=cert.recipient_name or "",
        course_name=cert.course_name or job.course_name,
        issuer=job.issuer_name,
        completion_date=cert.completion_date,
        issued_at=cert.generated_at,
    )
