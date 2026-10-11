"""Certificate endpoints: metadata and PDF download."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import get_certificate_or_404, get_storage, safe_filename, to_certificate_out
from app.api.docs import NOT_FOUND_CERTIFICATE, PDF_RESPONSE, error
from app.database import get_db
from app.models import CertificateStatus
from app.schemas import CertificateOut
from app.services.storage import LocalStorage

router = APIRouter(prefix="/certificates", tags=["3. Certificates"])

DbSession = Annotated[Session, Depends(get_db)]


@router.get("/{certificate_id}", response_model=CertificateOut, summary="Get one certificate", responses=NOT_FOUND_CERTIFICATE)
def get_certificate(certificate_id: str, request: Request, db: DbSession) -> CertificateOut:
    """Recipient details, status, error (if any) and `download_url` (when generated)."""
    return to_certificate_out(get_certificate_or_404(db, certificate_id), request)


@router.get(
    "/{certificate_id}/download",
    response_class=FileResponse,
    summary="Download one certificate PDF",
    responses={**PDF_RESPONSE, **NOT_FOUND_CERTIFICATE, 409: error("Not generated (status is not SUCCEEDED).")},
)
def download_certificate(
    certificate_id: str,
    db: DbSession,
    storage: Annotated[LocalStorage, Depends(get_storage)],
) -> FileResponse:
    """Returns the PDF. Only available once the certificate's status is `SUCCEEDED`."""
    cert = get_certificate_or_404(db, certificate_id)
    if cert.status != CertificateStatus.SUCCEEDED or not cert.file_path:
        raise HTTPException(
            status_code=409,
            detail=f"Certificate is not available for download (status: {cert.status.value})",
        )
    if not storage.exists(cert.file_path):
        raise HTTPException(status_code=404, detail="Certificate file is missing from storage")

    filename = f"{safe_filename(cert.recipient_name)}_{cert.id[:8]}.pdf"
    return FileResponse(storage.absolute_path(cert.file_path), media_type="application/pdf", filename=filename)
