"""Business logic for jobs: creation, adding recipients, status/progress, retry and crash recovery.

Route handlers stay thin and translate the exceptions defined here into HTTP
responses.

Concurrency: adding recipients (PATCH) and finishing a job (`finalize_job`)
both start with `lock_job`, so they never interleave. Either the worker sees
the new PENDING rows and keeps going, or the job has already finished and
PATCH puts it back to PENDING and re-queues it. No certificate is lost or
processed twice.
"""

from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import Certificate, CertificateStatus, EmailStatus, Job, JobStatus, utcnow
from app.schemas import InvalidRecipient, JobCreateRequest, StatusCounts, unknown_template_message
from app.services.custom_templates import custom_template_exists
from app.services.templates import TEMPLATES
from app.services.validation import InvalidRecipientRow, ValidRecipient, validate_recipients

ERROR_SEPARATOR = "; "
UNFINISHED = (CertificateStatus.PENDING, CertificateStatus.PROCESSING)
FINISHED = (JobStatus.COMPLETED, JobStatus.COMPLETED_WITH_ERRORS, JobStatus.FAILED)

# ---------- Domain errors (mapped to HTTP codes in the API layer) ----------


class TooManyRecipientsError(Exception):
    def __init__(self, count: int, limit: int) -> None:
        super().__init__(f"A job may contain at most {limit} recipients (got {count}).")


class NoValidRecipientsError(Exception):
    def __init__(
        self, invalid: list[InvalidRecipient], message: str = "None of the recipients are valid; no job was created."
    ) -> None:
        super().__init__(message)
        self.invalid = invalid


class JobNotFinishedError(Exception):
    def __init__(self) -> None:
        super().__init__("Job is still running; retry is only possible once it has finished.")


class UnknownTemplateError(Exception):
    def __init__(self, template_id: str) -> None:
        super().__init__(unknown_template_message(template_id))


class JobFullError(Exception):
    """The job cannot accept more recipients (409)."""

    def __init__(self, existing: int, adding: int, limit: int) -> None:
        super().__init__(
            f"Job already has {existing} recipient(s); adding {adding} would exceed the limit of {limit} per job."
        )


@dataclass
class CreatedJob:
    job: Job
    accepted: int
    invalid: list[InvalidRecipient]


@dataclass
class AddedRecipients:
    job: Job
    accepted: int
    invalid: list[InvalidRecipient]
    needs_submit: bool  # hand the job to the runner (it had finished, or never started)


# ---------- Helpers ----------

def _as_str(value: object, max_len: int) -> str | None:
    """Keep whatever the client sent for an invalid row (if it's a string) for reporting."""
    return value[:max_len] if isinstance(value, str) else None


def _valid_certificate(row: ValidRecipient, row_index: int) -> Certificate:
    r = row.data
    return Certificate(
        row_index=row_index,
        recipient_name=r.name,
        recipient_email=str(r.email),
        external_id=r.external_id,
        course_name=r.course_name,
        completion_date=r.completion_date,
        status=CertificateStatus.PENDING,
    )


def _invalid_certificate(row: InvalidRecipientRow, row_index: int) -> Certificate:
    raw = row.raw if isinstance(row.raw, dict) else {}
    return Certificate(
        row_index=row_index,
        recipient_name=_as_str(raw.get("name"), 255),
        recipient_email=_as_str(raw.get("email"), 320),
        external_id=_as_str(raw.get("external_id"), 100),
        status=CertificateStatus.INVALID,
        error=ERROR_SEPARATOR.join(row.errors),
    )


def lock_job(db: Session, job_id: str) -> bool:
    """Take the job's write lock for the rest of the current transaction. Returns False if the job is gone.

    A no-op UPDATE is the portable way to do this: PostgreSQL locks the row until
    commit, and SQLite takes its database write lock, which also makes every
    following read in this transaction see the latest committed data.
    """
    return db.execute(update(Job).where(Job.id == job_id).values(status=Job.status)).rowcount == 1


# ---------- Creation ----------


def create_job(db: Session, request: JobCreateRequest, max_recipients: int) -> CreatedJob:
    if len(request.recipients) > max_recipients:
        raise TooManyRecipientsError(len(request.recipients), max_recipients)

    info = request.certificate
    if info.template not in TEMPLATES and not custom_template_exists(db, info.template):
        raise UnknownTemplateError(info.template)

    valid, invalid_rows = validate_recipients(request.recipients)
    invalid = [InvalidRecipient(row_index=row.row_index, errors=row.errors) for row in invalid_rows]
    if not valid:
        raise NoValidRecipientsError(invalid)

    job = Job(
        title=info.title,
        course_name=info.course_name,
        issuer_name=info.issuer_name,
        issue_date=info.issue_date,
        template_id=info.template,
    )
    for row in valid:
        job.certificates.append(_valid_certificate(row, row.row_index))
    for row in invalid_rows:
        job.certificates.append(_invalid_certificate(row, row.row_index))

    db.add(job)
    db.commit()

    return CreatedJob(job=job, accepted=len(valid), invalid=invalid)


# ---------- Adding recipients to an existing job ----------


def add_recipients(
    db: Session, job: Job, raw_recipients: list, max_per_request: int, max_per_job: int
) -> AddedRecipients:
    """Validate and append recipients to `job`, all inside the job lock.

    New rows continue the job's row numbering (so ZIP file names continue too)
    and are checked for duplicate emails against the whole job. Only the new
    rows are PENDING; existing certificates are never touched.
    """
    if len(raw_recipients) > max_per_request:
        raise TooManyRecipientsError(len(raw_recipients), max_per_request)

    try:
        lock_job(db, job.id)
        db.refresh(job)  # the status as of now, under the lock

        existing = db.execute(
            select(Certificate.row_index, Certificate.recipient_email, Certificate.status).where(
                Certificate.job_id == job.id
            )
        ).all()
        if len(existing) + len(raw_recipients) > max_per_job:
            raise JobFullError(len(existing), len(raw_recipients), max_per_job)

        existing_emails = {
            email.lower(): row_index
            for row_index, email, status in existing
            if email and status != CertificateStatus.INVALID
        }
        valid, invalid_rows = validate_recipients(raw_recipients, existing_emails)
        invalid = [InvalidRecipient(row_index=row.row_index, errors=row.errors) for row in invalid_rows]
        if not valid:
            raise NoValidRecipientsError(invalid, "None of the new recipients are valid; nothing was added.")

        next_row = max((row_index for row_index, _, _ in existing), default=-1) + 1
        new_certificates = [_valid_certificate(row, next_row + row.row_index) for row in valid]
        new_certificates += [_invalid_certificate(row, next_row + row.row_index) for row in invalid_rows]
        for cert in new_certificates:
            cert.job_id = job.id
        db.add_all(new_certificates)

        # PROCESSING: the running worker picks the new rows up before it finishes.
        needs_submit = job.status != JobStatus.PROCESSING
        if job.status in FINISHED:
            job.status = JobStatus.PENDING  # the worker will finish it again with the right final status
            job.completed_at = None
        db.commit()
    except Exception:
        db.rollback()
        raise

    return AddedRecipients(job=job, accepted=len(valid), invalid=invalid, needs_submit=needs_submit)


# ---------- Status / progress ----------


def get_status_counts(db: Session, job_id: str) -> StatusCounts:
    return counts_for_jobs(db, [job_id])[job_id]


def counts_for_jobs(db: Session, job_ids: list[str]) -> dict[str, StatusCounts]:
    """Certificate counts per status for several jobs in ONE grouped query (no N+1)."""
    rows = db.execute(
        select(Certificate.job_id, Certificate.status, func.count())
        .where(Certificate.job_id.in_(job_ids))
        .group_by(Certificate.job_id, Certificate.status)
    ).all()
    by_job: dict[str, dict[CertificateStatus, int]] = {job_id: {} for job_id in job_ids}
    for job_id, status, count in rows:
        by_job[job_id][status] = count
    return {job_id: _to_counts(by_status) for job_id, by_status in by_job.items()}


def _to_counts(by_status: dict[CertificateStatus, int]) -> StatusCounts:
    return StatusCounts(
        total=sum(by_status.values()),
        pending=by_status.get(CertificateStatus.PENDING, 0),
        processing=by_status.get(CertificateStatus.PROCESSING, 0),
        succeeded=by_status.get(CertificateStatus.SUCCEEDED, 0),
        failed=by_status.get(CertificateStatus.FAILED, 0),
        invalid=by_status.get(CertificateStatus.INVALID, 0),
    )


def progress_percent(counts: StatusCounts) -> float:
    """Share of certificates that reached a final state (SUCCEEDED, FAILED or INVALID)."""
    if counts.total == 0:
        return 0.0
    processed = counts.succeeded + counts.failed + counts.invalid
    return round(100 * processed / counts.total, 1)


def final_job_status(counts: StatusCounts) -> JobStatus:
    if counts.succeeded == counts.total:
        return JobStatus.COMPLETED
    if counts.succeeded == 0:
        return JobStatus.FAILED
    return JobStatus.COMPLETED_WITH_ERRORS


def finalize_job(db: Session, job_id: str, attempted: set[str] | None = None) -> JobStatus | None:
    """Set the job's final status from its certificate counts.

    With `attempted` (the certificate ids this run already worked on), returns
    None without finishing if PENDING certificates appeared that the worker has
    not seen yet (added by PATCH meanwhile), so the worker carries on with them.
    """
    lock_job(db, job_id)
    if attempted is not None:
        pending = db.scalars(
            select(Certificate.id).where(Certificate.job_id == job_id, Certificate.status == CertificateStatus.PENDING)
        ).all()
        if any(certificate_id not in attempted for certificate_id in pending):
            db.rollback()
            return None

    # Safety net: anything still unfinished at this point could not be generated.
    db.execute(
        update(Certificate)
        .where(Certificate.job_id == job_id, Certificate.status.in_(UNFINISHED))
        .values(status=CertificateStatus.FAILED, error="Generation did not complete")
    )
    status = final_job_status(get_status_counts(db, job_id))
    db.execute(update(Job).where(Job.id == job_id).values(status=status, completed_at=utcnow()))
    db.commit()
    return status


# ---------- Listing ----------


def list_jobs(db: Session, status: JobStatus | None, page: int, page_size: int) -> tuple[list[Job], int]:
    """Jobs newest first, optionally filtered by status."""
    query = select(Job)
    if status is not None:
        query = query.where(Job.status == status)

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    jobs = db.scalars(query.order_by(Job.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return list(jobs), total


def list_certificates(
    db: Session,
    job_id: str,
    status: CertificateStatus | None,
    page: int,
    page_size: int,
    email_status: EmailStatus | None = None,
) -> tuple[list[Certificate], int]:
    query = select(Certificate).where(Certificate.job_id == job_id)
    if status is not None:
        query = query.where(Certificate.status == status)
    if email_status is not None:
        query = query.where(Certificate.email_status == email_status)

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    items = db.scalars(
        query.order_by(Certificate.row_index).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return list(items), total


def unsuccessful_certificates(db: Session, job_id: str) -> list[Certificate]:
    """FAILED and INVALID certificates, so the job status shows exactly what went wrong."""
    return list(
        db.scalars(
            select(Certificate)
            .where(
                Certificate.job_id == job_id,
                Certificate.status.in_((CertificateStatus.FAILED, CertificateStatus.INVALID)),
            )
            .order_by(Certificate.row_index)
        ).all()
    )


def succeeded_certificates(db: Session, job_id: str) -> list[Certificate]:
    return list(
        db.scalars(
            select(Certificate)
            .where(Certificate.job_id == job_id, Certificate.status == CertificateStatus.SUCCEEDED)
            .order_by(Certificate.row_index)
        ).all()
    )


# ---------- Retry / recovery ----------


def prepare_retry(db: Session, job: Job) -> int:
    """Reset FAILED certificates to PENDING so the worker picks them up again.

    Returns the number of certificates re-queued. INVALID rows are never retried:
    their input is wrong, so regenerating would fail the same way.
    """
    if job.status in (JobStatus.PENDING, JobStatus.PROCESSING):
        raise JobNotFinishedError()

    requeued = db.execute(
        update(Certificate)
        .where(Certificate.job_id == job.id, Certificate.status == CertificateStatus.FAILED)
        .values(status=CertificateStatus.PENDING, error=None)
    ).rowcount
    if requeued:
        job.status = JobStatus.PENDING
        job.completed_at = None
    db.commit()
    return requeued


def recover_incomplete_jobs(db: Session) -> list[str]:
    """After a crash/restart, put interrupted work back into the queue.

    Certificates stuck in PROCESSING go back to PENDING; SUCCEEDED ones are
    untouched, so nothing is generated twice. Jobs that are PENDING (including
    finished jobs re-opened by adding recipients) are returned to be re-queued.
    """
    interrupted_jobs = select(Job.id).where(Job.status == JobStatus.PROCESSING)
    db.execute(
        update(Certificate)
        .where(Certificate.job_id.in_(interrupted_jobs), Certificate.status == CertificateStatus.PROCESSING)
        .values(status=CertificateStatus.PENDING)
    )
    db.execute(update(Job).where(Job.status == JobStatus.PROCESSING).values(status=JobStatus.PENDING))
    db.commit()
    return list(db.scalars(select(Job.id).where(Job.status == JobStatus.PENDING).order_by(Job.created_at)).all())
