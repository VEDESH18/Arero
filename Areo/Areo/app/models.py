"""SQLAlchemy models.

Job-level counters (succeeded/failed/...) are NOT stored on the job row; they
are computed from certificate rows with a GROUP BY. That keeps a single source
of truth and avoids counter drift when several threads update certificates.
"""

import enum
import secrets
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


class JobStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_ERRORS = "COMPLETED_WITH_ERRORS"
    FAILED = "FAILED"


class CertificateStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    INVALID = "INVALID"


class Validity(str, enum.Enum):
    """Whether an issued certificate still stands. Only VALID is used today; REVOKED is reserved."""

    VALID = "VALID"
    REVOKED = "REVOKED"


def new_verification_code() -> str:
    """Unguessable public code for the QR / verify URL (96 random bits, URL-safe)."""
    return secrets.token_urlsafe(12)


class EmailStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    SENDING = "SENDING"
    SENT = "SENT"
    FAILED = "FAILED"


class Job(Base):
    __tablename__ = "certificate_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False, length=32), default=JobStatus.PENDING, index=True
    )

    # Shared certificate information (same for every recipient in the job).
    title: Mapped[str] = mapped_column(String(200))
    course_name: Mapped[str] = mapped_column(String(200))
    issuer_name: Mapped[str] = mapped_column(String(200))
    issue_date: Mapped[date] = mapped_column(Date)
    template_id: Mapped[str] = mapped_column(String(50), default="classic")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    certificates: Mapped[list["Certificate"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="Certificate.row_index"
    )


class Certificate(Base):
    __tablename__ = "certificates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("certificate_jobs.id"), index=True)
    row_index: Mapped[int] = mapped_column(Integer)  # position in the original request

    # Recipient data. Nullable because INVALID rows may be missing fields.
    recipient_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recipient_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    course_name: Mapped[str | None] = mapped_column(String(200), nullable=True)  # per-recipient override
    completion_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    status: Mapped[CertificateStatus] = mapped_column(
        Enum(CertificateStatus, native_enum=False, length=32),
        default=CertificateStatus.PENDING,
        index=True,
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)

    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)  # relative to storage dir

    # Public verification: printed as a QR code. Never changes, even when the PDF is regenerated.
    # Nullable only so the startup migration can add it to old tables before backfilling.
    verification_code: Mapped[str | None] = mapped_column(
        String(32), unique=True, index=True, nullable=True, default=new_verification_code
    )
    validity: Mapped[Validity] = mapped_column(
        Enum(Validity, native_enum=False, length=16), default=Validity.VALID, server_default=Validity.VALID.value
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Email delivery of the generated PDF. NULL = never requested.
    email_status: Mapped[EmailStatus | None] = mapped_column(
        Enum(EmailStatus, native_enum=False, length=16), nullable=True, index=True
    )
    email_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    emailed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    job: Mapped[Job] = relationship(back_populates="certificates")


class CustomTemplate(Base):
    """A company's own design: colours, font, wording, logo and optional signature.

    Image files live under UPLOAD_DIR with random names; only those names are stored here.
    """

    __tablename__ = "custom_templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)  # reserved for future auth
    name: Mapped[str] = mapped_column(String(100))
    primary_color: Mapped[str] = mapped_column(String(7))
    secondary_color: Mapped[str] = mapped_column(String(7))
    font: Mapped[str] = mapped_column(String(32))
    title_text: Mapped[str] = mapped_column(String(120))
    body_text: Mapped[str] = mapped_column(Text)
    border_style: Mapped[str] = mapped_column(String(16))
    layout: Mapped[str] = mapped_column(String(16))
    logo_file: Mapped[str] = mapped_column(String(64))
    signature_file: Mapped[str | None] = mapped_column(String(64), nullable=True)
    signer_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    signer_title: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
