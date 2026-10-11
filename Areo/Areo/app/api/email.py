"""Email endpoint: send each recipient their certificate PDF over SMTP."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_app_settings, get_job_or_404, get_runner
from app.api.docs import NOT_FOUND_JOB, error
from app.config import Settings
from app.database import get_db
from app.schemas import EmailQueuedResponse
from app.services import email_delivery
from app.services.job_runner import JobRunner

router = APIRouter(prefix="/jobs", tags=["4. Email"])

DbSession = Annotated[Session, Depends(get_db)]


@router.post(
    "/{job_id}/email",
    status_code=202,
    response_model=EmailQueuedResponse,
    summary="Email certificates to recipients",
    responses={
        **NOT_FOUND_JOB,
        409: error("The job is still running, or has no generated certificates."),
        503: error("Email is not configured on the server (set SMTP_HOST and SMTP_FROM)."),
    },
)
def email_certificates(
    job_id: str,
    request: Request,
    db: DbSession,
    runner: Annotated[JobRunner, Depends(get_runner)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    resend: Annotated[bool, Query(description="Also email recipients who were already sent their certificate.")] = False,
) -> EmailQueuedResponse:
    """Sends each recipient **their own** certificate PDF as an attachment, over SMTP, in the background.

    Safe to call again: by default only recipients who have not received it yet (never sent, or failed) are emailed.
    Follow delivery under `emails` in **GET /api/v1/jobs/{job_id}**.
    """
    if not settings.email_configured:
        raise HTTPException(status_code=503, detail="Email is not configured: set SMTP_HOST and SMTP_FROM in .env")
    job = get_job_or_404(db, job_id)
    try:
        queued, already_sent = email_delivery.queue_job_emails(db, job, resend)
    except email_delivery.JobNotReadyForEmailError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    if queued:
        runner.submit_emails(job.id)
    return EmailQueuedResponse(
        job_id=job.id,
        queued=queued,
        already_sent=already_sent,
        status_url=str(request.url_for("get_job", job_id=job.id)),
    )
