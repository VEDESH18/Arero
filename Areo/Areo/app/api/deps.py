"""Shared FastAPI dependencies and response helpers."""

import re

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Certificate, CertificateStatus, Job
from app.schemas import CertificateFailure, CertificateOut
from app.services.job_runner import JobRunner
from app.services.storage import LocalStorage
from app.services.verification import verification_url


def get_runner(request: Request) -> JobRunner:
    return request.app.state.runner


def get_storage(request: Request) -> LocalStorage:
    return request.app.state.storage


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_job_or_404(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return job


def get_certificate_or_404(db: Session, certificate_id: str) -> Certificate:
    cert = db.get(Certificate, certificate_id)
    if cert is None:
        raise HTTPException(status_code=404, detail=f"Certificate {certificate_id} not found")
    return cert


def safe_filename(text: str | None, fallback: str = "certificate") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", text or "").strip("_")[:60]
    return cleaned or fallback


def to_certificate_out(cert: Certificate, request: Request) -> CertificateOut:
    download_url = None
    if cert.status == CertificateStatus.SUCCEEDED:
        download_url = str(request.url_for("download_certificate", certificate_id=cert.id))
    return CertificateOut(
        id=cert.id,
        job_id=cert.job_id,
        row_index=cert.row_index,
        recipient_name=cert.recipient_name,
        recipient_email=cert.recipient_email,
        external_id=cert.external_id,
        course_name=cert.course_name,
        completion_date=cert.completion_date,
        status=cert.status,
        error=cert.error,
        attempts=cert.attempts,
        generated_at=cert.generated_at,
        download_url=download_url,
        email_status=cert.email_status,
        email_error=cert.email_error,
        emailed_at=cert.emailed_at,
        verification_code=cert.verification_code,
        verification_url=(
            verification_url(request.app.state.settings.public_base_url, cert.verification_code)
            if cert.verification_code
            else None
        ),
    )


def to_failure_out(cert: Certificate, request: Request) -> CertificateFailure:
    return CertificateFailure(
        certificate_id=cert.id,
        row_index=cert.row_index,
        recipient_name=cert.recipient_name,
        recipient_email=cert.recipient_email,
        status=cert.status,
        error=cert.error,
        certificate_url=str(request.url_for("get_certificate", certificate_id=cert.id)),
    )
