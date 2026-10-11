"""Emailing generated certificates to their recipients.

Same shape as certificate generation (`worker.py`):
- The API marks certificates QUEUED and returns 202; this runs in the background.
- Each email is claimed atomically (QUEUED -> SENDING), sent, and its result
  committed on its own, so one bad address never stops the others.
- State lives in the DB, so a restart re-queues anything not yet sent.
"""

import logging
from collections.abc import Callable
from email.message import EmailMessage

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Certificate, CertificateStatus, EmailStatus, Job, JobStatus, utcnow
from app.schemas import EmailCounts
from app.services.mailer import Mailer
from app.services.storage import LocalStorage

logger = logging.getLogger(__name__)

MAX_ERROR_LENGTH = 1000
MailerFactory = Callable[[], Mailer]


# ---------- Called from the API ----------


class JobNotReadyForEmailError(Exception):
    pass


def queue_job_emails(db: Session, job: Job, resend: bool) -> tuple[int, int]:
    """Mark the job's generated certificates QUEUED. Returns (queued, already_sent).

    Without `resend`, certificates already SENT (or currently queued/sending) are
    left alone, so calling this twice never emails anyone twice.
    """
    if job.status in (JobStatus.PENDING, JobStatus.PROCESSING):
        raise JobNotReadyForEmailError("Job is still generating certificates; email them once it has finished.")

    generated = (Certificate.job_id == job.id) & (Certificate.status == CertificateStatus.SUCCEEDED)
    if not db.scalar(select(func.count()).where(generated)):
        raise JobNotReadyForEmailError("This job has no generated certificates to email.")

    skip = [EmailStatus.QUEUED, EmailStatus.SENDING] + ([] if resend else [EmailStatus.SENT])
    # NULL (never requested) needs its own check: SQL's NOT IN is never true for NULL.
    to_queue = Certificate.email_status.is_(None) | Certificate.email_status.not_in(skip)

    queued = db.execute(
        update(Certificate).where(generated, to_queue).values(email_status=EmailStatus.QUEUED, email_error=None)
    ).rowcount
    db.commit()
    already_sent = db.scalar(select(func.count()).where(generated, Certificate.email_status == EmailStatus.SENT)) or 0
    return queued, already_sent


def get_email_counts(db: Session, job_id: str) -> EmailCounts:
    rows = db.execute(
        select(Certificate.email_status, func.count())
        .where(Certificate.job_id == job_id, Certificate.status == CertificateStatus.SUCCEEDED)
        .group_by(Certificate.email_status)
    ).all()
    by_status = {status: count for status, count in rows}
    return EmailCounts(
        not_requested=by_status.get(None, 0),
        queued=by_status.get(EmailStatus.QUEUED, 0),
        sending=by_status.get(EmailStatus.SENDING, 0),
        sent=by_status.get(EmailStatus.SENT, 0),
        failed=by_status.get(EmailStatus.FAILED, 0),
    )


def recover_queued_emails(db: Session) -> list[str]:
    """After a restart: SENDING goes back to QUEUED; return the jobs that still have queued emails.

    A message that was mid-send when the process died may be sent twice
    (at-least-once delivery), which is better than silently never sending it.
    """
    db.execute(
        update(Certificate).where(Certificate.email_status == EmailStatus.SENDING).values(email_status=EmailStatus.QUEUED)
    )
    db.commit()
    return list(db.scalars(select(Certificate.job_id).where(Certificate.email_status == EmailStatus.QUEUED).distinct()).all())


# ---------- Background work ----------


def deliver_job_emails(
    job_id: str,
    storage: LocalStorage,
    mailer_factory: MailerFactory,
    should_stop: Callable[[], bool] = lambda: False,
) -> None:
    certificate_ids = _queued_certificate_ids(job_id)
    if not certificate_ids:
        return

    logger.info("Emailing %d certificate(s) for job %s", len(certificate_ids), job_id)
    with mailer_factory() as mailer:
        for certificate_id in certificate_ids:
            if should_stop():
                logger.warning("Email delivery for job %s interrupted by shutdown; will resume on next start", job_id)
                return
            try:
                _email_certificate(certificate_id, storage, mailer)
            except Exception:  # e.g. a DB error: log it and carry on
                logger.exception("Unexpected error while emailing certificate %s", certificate_id)

    with SessionLocal() as db:
        counts = get_email_counts(db, job_id)
    logger.info("Email delivery for job %s finished: %d sent, %d failed", job_id, counts.sent, counts.failed)


def _queued_certificate_ids(job_id: str) -> list[str]:
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(Certificate.id)
                .where(Certificate.job_id == job_id, Certificate.email_status == EmailStatus.QUEUED)
                .order_by(Certificate.row_index)
            ).all()
        )


def _email_certificate(certificate_id: str, storage: LocalStorage, mailer: Mailer) -> None:
    with SessionLocal() as db:
        # Atomic claim: if two deliveries overlap, only one of them sends this email.
        claimed = db.execute(
            update(Certificate)
            .where(Certificate.id == certificate_id, Certificate.email_status == EmailStatus.QUEUED)
            .values(email_status=EmailStatus.SENDING)
        ).rowcount
        db.commit()
        if not claimed:
            return

        cert = db.get(Certificate, certificate_id)
        try:
            pdf = storage.absolute_path(cert.file_path).read_bytes()
            mailer.send(build_message(cert, pdf))
        except Exception as exc:
            cert.email_status = EmailStatus.FAILED
            cert.email_error = f"{type(exc).__name__}: {exc}"[:MAX_ERROR_LENGTH]
            logger.warning("Email for certificate %s to %s failed: %s", cert.id, cert.recipient_email, cert.email_error)
        else:
            cert.email_status = EmailStatus.SENT
            cert.email_error = None
            cert.emailed_at = utcnow()
        db.commit()


def build_message(cert: Certificate, pdf: bytes) -> EmailMessage:
    job = cert.job
    course = cert.course_name or job.course_name
    message = EmailMessage()
    message["To"] = cert.recipient_email
    message["Subject"] = f"Your certificate: {course}"
    message.set_content(
        f"Hi {cert.recipient_name},\n\n"
        f"Congratulations on completing {course}!\n"
        f"Your certificate from {job.issuer_name} is attached as a PDF.\n\n"
        f"Certificate ID: {cert.id}\n\n"
        f"{job.issuer_name}\n"
    )
    filename = f"Certificate_{''.join(ch if ch.isalnum() else '_' for ch in cert.recipient_name or 'recipient')}.pdf"
    message.add_attachment(pdf, maintype="application", subtype="pdf", filename=filename)
    return message
