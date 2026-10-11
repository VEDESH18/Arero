"""Background processing of one job.

Key properties:
- Each certificate is generated in its own try/except and its result is
  committed in its own short transaction, so one failure never affects others.
- The worker always opens its own DB sessions (never the request's session).
- Only PENDING certificates are processed, so re-running a job (retry, crash
  recovery, or recipients added later) never regenerates SUCCEEDED certificates.
- Each certificate is attempted at most once per run; recipients added while
  the job is running are picked up before the job is finalized.
"""

import logging
from collections.abc import Callable

from sqlalchemy import select, update

from app.database import SessionLocal
from app.models import Certificate, CertificateStatus, Job, JobStatus, new_verification_code, utcnow
from app.services.certificate_generator import CertificateData, generate_certificate_pdf
from app.services.custom_templates import resolve_template
from app.services.job_service import finalize_job
from app.services.render_context import RenderContext
from app.services.storage import LocalStorage
from app.services.verification import verification_url

logger = logging.getLogger(__name__)

MAX_ERROR_LENGTH = 1000


def process_job(
    job_id: str,
    storage: LocalStorage,
    should_stop: Callable[[], bool] = lambda: False,
    context: RenderContext | None = None,
) -> None:
    context = context or RenderContext()
    if not _claim_job(job_id):
        logger.info("Job %s is not PENDING (already claimed or finished); skipping", job_id)
        return

    logger.info("Job %s started", job_id)
    attempted: set[str] = set()
    while True:
        # Recipients can be added (PATCH) while the job runs, so look for new work
        # until finalize_job confirms, under the job lock, that nothing new arrived.
        batch = [i for i in _pending_certificate_ids(job_id) if i not in attempted]
        if not batch:
            with SessionLocal() as db:
                status = finalize_job(db, job_id, attempted)
            if status is not None:
                break
            continue

        for certificate_id in batch:
            if should_stop():
                # Leave the job PROCESSING; startup recovery will resume it.
                logger.warning("Job %s interrupted by shutdown; will resume on next start", job_id)
                return
            attempted.add(certificate_id)
            try:
                _process_certificate(certificate_id, storage, context)
            except Exception:  # e.g. a DB error: log it and carry on with the next certificate
                logger.exception("Unexpected error while processing certificate %s", certificate_id)

    logger.info("Job %s finished with status %s", job_id, status.value)


def _claim_job(job_id: str) -> bool:
    """Atomically move PENDING -> PROCESSING, so two workers can never run the same job."""
    with SessionLocal() as db:
        result = db.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == JobStatus.PENDING)
            .values(status=JobStatus.PROCESSING, started_at=utcnow(), completed_at=None)
        )
        db.commit()
        return result.rowcount == 1


def _pending_certificate_ids(job_id: str) -> list[str]:
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(Certificate.id)
                .where(Certificate.job_id == job_id, Certificate.status == CertificateStatus.PENDING)
                .order_by(Certificate.row_index)
            ).all()
        )


def _process_certificate(certificate_id: str, storage: LocalStorage, context: RenderContext) -> None:
    with SessionLocal() as db:
        cert = db.get(Certificate, certificate_id)
        if cert is None or cert.status != CertificateStatus.PENDING:
            return

        cert.status = CertificateStatus.PROCESSING
        cert.attempts += 1
        if cert.verification_code is None:  # rows from before the migration, if any slipped through
            cert.verification_code = new_verification_code()
        db.commit()

        job = cert.job
        relative_path = storage.relative_path_for(job.id, cert.id)
        data = CertificateData(
            title=job.title,
            recipient_name=cert.recipient_name or "",
            course_name=cert.course_name or job.course_name,
            issuer_name=job.issuer_name,
            issue_date=job.issue_date,
            certificate_id=cert.id,
            completion_date=cert.completion_date,
            verification_code=cert.verification_code,
            verification_url=verification_url(context.public_base_url, cert.verification_code),
        )

        try:
            # Write to a temp file and rename only on success, so a crash never
            # leaves a half-written PDF behind a SUCCEEDED row.
            # Resolved per certificate: a broken custom template (e.g. a missing logo) fails only this one.
            template = resolve_template(db, job.template_id, context.upload_dir)
            generate_certificate_pdf(data, storage.temp_path_for(relative_path), template)
            storage.commit(relative_path)
        except Exception as exc:
            storage.discard_temp(relative_path)
            cert.status = CertificateStatus.FAILED
            cert.error = f"{type(exc).__name__}: {exc}"[:MAX_ERROR_LENGTH]
            logger.warning("Certificate %s (job %s, row %s) failed: %s", cert.id, job.id, cert.row_index, cert.error)
        else:
            cert.status = CertificateStatus.SUCCEEDED
            cert.file_path = relative_path
            cert.generated_at = utcnow()
            cert.error = None
        db.commit()
