"""Job endpoints: create, add recipients, list, status, list certificates, ZIP download, retry."""

import logging
import tempfile
import zipfile
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import (
    get_app_settings,
    get_job_or_404,
    get_runner,
    get_storage,
    safe_filename,
    to_certificate_out,
    to_failure_out,
)
from app.api.docs import ADD_RECIPIENTS_EXAMPLES, CREATE_JOB_EXAMPLES, NOT_FOUND_JOB, ZIP_RESPONSE, error
from app.config import Settings
from app.database import get_db
from app.models import CertificateStatus, EmailStatus, JobStatus
from app.schemas import (
    AddRecipientsRequest,
    CertificatePage,
    JobCreatedResponse,
    JobCreateRequest,
    JobPage,
    JobStatusResponse,
    JobSummary,
    RecipientsAddedResponse,
    RetryResponse,
)
from app.services import email_delivery, job_service
from app.services.job_runner import JobRunner
from app.services.storage import LocalStorage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/jobs", tags=["2. Jobs"])

DbSession = Annotated[Session, Depends(get_db)]
Page = Annotated[int, Query(ge=1, description="Page number, starting at 1.")]
PageSize = Annotated[int, Query(ge=1, le=100, description="Items per page (max 100).")]


@router.post(
    "",
    status_code=202,
    response_model=JobCreatedResponse,
    summary="Submit a bulk certificate job",
    responses={422: error("Request unusable: bad `certificate` block, empty or too many recipients, "
                          "unknown template, or every recipient invalid (nothing is created).")},
)
def create_job(
    body: Annotated[JobCreateRequest, Body(openapi_examples=CREATE_JOB_EXAMPLES)],
    request: Request,
    db: DbSession,
    runner: Annotated[JobRunner, Depends(get_runner)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> JobCreatedResponse:
    """Validates every recipient, saves the job and returns **immediately** with a `job_id`.
    Certificates are generated in the background; follow progress with `status_url`.

    Invalid recipients are listed in `invalid_recipients` and skipped; the valid ones are still generated.
    Pick an example from the **Examples** dropdown below to try it.
    """
    try:
        created = job_service.create_job(db, body, settings.max_recipients_per_job)
    except (job_service.TooManyRecipientsError, job_service.UnknownTemplateError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except job_service.NoValidRecipientsError as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": str(exc), "invalid_recipients": [i.model_dump() for i in exc.invalid]},
        )

    job = created.job
    runner.submit(job.id)  # only after the commit, so the worker can see the rows
    db.refresh(job)

    return JobCreatedResponse(
        job_id=job.id,
        status=job.status,
        total=created.accepted + len(created.invalid),
        accepted=created.accepted,
        invalid=len(created.invalid),
        invalid_recipients=created.invalid,
        status_url=str(request.url_for("get_job", job_id=job.id)),
        certificates_url=str(request.url_for("list_job_certificates", job_id=job.id)),
    )


@router.patch(
    "/{job_id}/recipients",
    status_code=202,
    response_model=RecipientsAddedResponse,
    summary="Add recipients to an existing job",
    responses={
        **NOT_FOUND_JOB,
        409: error("The job cannot accept these recipients (it would exceed MAX_TOTAL_RECIPIENTS_PER_JOB)."),
        422: error("Empty or too large batch, or every new recipient invalid (nothing is added)."),
    },
)
def add_recipients(
    job_id: str,
    body: Annotated[AddRecipientsRequest, Body(openapi_examples=ADD_RECIPIENTS_EXAMPLES)],
    request: Request,
    db: DbSession,
    runner: Annotated[JobRunner, Depends(get_runner)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> RecipientsAddedResponse:
    """Validates the new recipients with the same rules as job creation; an email already in the job is a duplicate.

    Only the **new** certificates are generated; existing ones are untouched. A finished job goes back to
    `PENDING` and ends with the right final status once the new certificates are done. Safe to call while
    the job is running. ZIP numbering continues (`0004_…` after `0003_…`), and **POST …/email** without
    `resend` then emails only the new recipients.
    """
    job = get_job_or_404(db, job_id)
    try:
        added = job_service.add_recipients(
            db, job, body.recipients, settings.max_recipients_per_job, settings.max_total_recipients_per_job
        )
    except job_service.JobFullError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except job_service.TooManyRecipientsError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except job_service.NoValidRecipientsError as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": str(exc), "invalid_recipients": [i.model_dump() for i in exc.invalid]},
        )

    if added.needs_submit:
        runner.submit(job.id)  # after the commit, so the worker sees the new rows
    db.refresh(job)
    return RecipientsAddedResponse(
        job_id=job.id,
        status=job.status,
        accepted=added.accepted,
        rejected=len(added.invalid),
        rejected_recipients=added.invalid,
        counts=job_service.get_status_counts(db, job.id),
        status_url=str(request.url_for("get_job", job_id=job.id)),
    )


@router.get("", response_model=JobPage, summary="List all jobs")
def list_jobs(
    request: Request,
    db: DbSession,
    status: Annotated[JobStatus | None, Query(description="Only jobs in this status.")] = None,
    page: Page = 1,
    page_size: PageSize = 20,
) -> JobPage:
    """Newest first, with each job's counts and progress. Use a `job_id` from here in the endpoints below."""
    jobs, total = job_service.list_jobs(db, status, page, page_size)
    counts = job_service.counts_for_jobs(db, [j.id for j in jobs])
    return JobPage(
        items=[
            JobSummary(
                job_id=j.id,
                status=j.status,
                title=j.title,
                course_name=j.course_name,
                issuer_name=j.issuer_name,
                template=j.template_id,
                counts=counts[j.id],
                progress_percent=job_service.progress_percent(counts[j.id]),
                created_at=j.created_at,
                completed_at=j.completed_at,
                status_url=str(request.url_for("get_job", job_id=j.id)),
            )
            for j in jobs
        ],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get("/{job_id}", response_model=JobStatusResponse, summary="Get job status and progress", responses=NOT_FOUND_JOB)
def get_job(job_id: str, request: Request, db: DbSession) -> JobStatusResponse:
    """Counts per status, `progress_percent`, and `failed_certificates`: who did not get a certificate and why.

    Poll until `status` is `COMPLETED`, `COMPLETED_WITH_ERRORS` or `FAILED`.
    """
    job = get_job_or_404(db, job_id)
    counts = job_service.get_status_counts(db, job.id)
    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        title=job.title,
        course_name=job.course_name,
        issuer_name=job.issuer_name,
        issue_date=job.issue_date,
        template=job.template_id,
        counts=counts,
        progress_percent=job_service.progress_percent(counts),
        failed_certificates=[
            to_failure_out(c, request) for c in job_service.unsuccessful_certificates(db, job.id)
        ],
        emails=email_delivery.get_email_counts(db, job.id),
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        certificates_url=str(request.url_for("list_job_certificates", job_id=job.id)),
        download_url=str(request.url_for("download_job_zip", job_id=job.id)),
    )


@router.get(
    "/{job_id}/certificates",
    response_model=CertificatePage,
    summary="List a job's certificates",
    responses=NOT_FOUND_JOB,
)
def list_job_certificates(
    job_id: str,
    request: Request,
    db: DbSession,
    status: Annotated[CertificateStatus | None, Query(description="Only certificates in this status.")] = None,
    email_status: Annotated[EmailStatus | None, Query(description="Only certificates with this email status.")] = None,
    page: Page = 1,
    page_size: PageSize = 50,
) -> CertificatePage:
    """Every recipient in the job, in submission order, with status, error, `download_url` and email status."""
    get_job_or_404(db, job_id)
    items, total = job_service.list_certificates(db, job_id, status, page, page_size, email_status)
    return CertificatePage(
        items=[to_certificate_out(c, request) for c in items],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get(
    "/{job_id}/download",
    response_class=StreamingResponse,
    summary="Download all certificates as a ZIP",
    responses={**ZIP_RESPONSE, **NOT_FOUND_JOB, 409: error("No certificate has been generated yet.")},
)
def download_job_zip(
    job_id: str,
    db: DbSession,
    storage: Annotated[LocalStorage, Depends(get_storage)],
) -> StreamingResponse:
    """Every certificate generated **so far** (also works while the job is still running).
    Files are named `0001_Asha_Rao.pdf`, `0002_…` in submission order.
    """
    job = get_job_or_404(db, job_id)
    certificates = job_service.succeeded_certificates(db, job.id)
    if not certificates:
        raise HTTPException(status_code=409, detail="No certificates have been generated for this job yet")

    # Spooled file: stays in memory for small archives, spills to disk for large ones.
    buffer = tempfile.SpooledTemporaryFile(max_size=10 * 1024 * 1024)
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for cert in certificates:
            if not cert.file_path or not storage.exists(cert.file_path):
                logger.warning("File for certificate %s is missing on disk; skipped from ZIP", cert.id)
                continue
            name = f"{cert.row_index + 1:04d}_{safe_filename(cert.recipient_name)}.pdf"
            archive.write(storage.absolute_path(cert.file_path), arcname=name)
    buffer.seek(0)

    filename = f"certificates_{safe_filename(job.course_name)}_{job.id[:8]}.zip"
    return StreamingResponse(
        _iter_file(buffer),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/{job_id}/retry",
    response_model=RetryResponse,
    summary="Retry failed certificates",
    responses={**NOT_FOUND_JOB, 409: error("The job is still running.")},
)
def retry_failed(
    job_id: str,
    db: DbSession,
    runner: Annotated[JobRunner, Depends(get_runner)],
) -> RetryResponse:
    """Re-queues only `FAILED` certificates of a finished job. `INVALID` ones are not retried (their input is wrong)."""
    job = get_job_or_404(db, job_id)
    try:
        requeued = job_service.prepare_retry(db, job)
    except job_service.JobNotFinishedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    if requeued:
        runner.submit(job.id)
        db.refresh(job)
    return RetryResponse(job_id=job.id, status=job.status, requeued=requeued)


def _iter_file(file, chunk_size: int = 64 * 1024):
    try:
        while chunk := file.read(chunk_size):
            yield chunk
    finally:
        file.close()
