"""Pydantic request/response models.

`recipients` is deliberately typed as a list of raw objects: each recipient is
validated individually in `services/validation.py`, so one bad row does not
reject the whole request (partial acceptance). `WithJsonSchema` still shows
the full recipient shape in Swagger.

The `description`/`examples` on fields only feed the Swagger docs.
"""

import re
from datetime import date, datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    WithJsonSchema,
    field_validator,
)

from app.models import CertificateStatus, EmailStatus, JobStatus
from app.services.certificate_generator import DEFAULT_TEMPLATE, TEMPLATES
from app.services.templates.custom import FONTS, PLACEHOLDER_PATTERN, PLACEHOLDERS

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]

# All timestamps are stored in UTC; SQLite drops the tzinfo, so re-attach it on output.
UtcDatetime = Annotated[datetime, AfterValidator(lambda v: v if v.tzinfo else v.replace(tzinfo=timezone.utc))]


def _not_in_future(value: date | None) -> date | None:
    if value is not None and value > date.today():
        raise ValueError("date cannot be in the future")
    return value


# ---------- Requests ----------


class CertificateInfo(BaseModel):
    """Information shared by every certificate in the job."""

    model_config = ConfigDict(extra="forbid")

    course_name: NonEmptyStr = Field(description="Course or event name printed on every certificate.", examples=["Intro to Python"])
    issuer_name: NonEmptyStr = Field(description="Organisation issuing the certificates.", examples=["Acme Academy"])
    title: NonEmptyStr = Field("Certificate of Completion", description="Heading of the certificate.")
    issue_date: date = Field(default_factory=date.today, description="Defaults to today. Cannot be in the future.")
    template: str = Field(
        DEFAULT_TEMPLATE,
        description=f"Design to use: a built-in one ({', '.join(TEMPLATES)}) or the `id` of a custom template. "
        "See **GET /api/v1/templates** for previews.",
        examples=["modern"],
    )

    _check_issue_date = field_validator("issue_date")(_not_in_future)

    @field_validator("template")
    @classmethod
    def _known_template(cls, value: str) -> str:
        # Built-in names are checked here; custom ids (UUIDs) are checked against the database when the job is created.
        if value not in TEMPLATES and not _UUID.fullmatch(value):
            raise ValueError(unknown_template_message(value))
        return value


_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def unknown_template_message(value: str) -> str:
    return f"unknown template '{value}'; choose one of: {', '.join(TEMPLATES)}, or a custom template id"


class RecipientIn(BaseModel):
    """One recipient. Validated per row, never at request level."""

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] = Field(
        description="Printed on the certificate. 1–100 characters.", examples=["Asha Rao"]
    )
    email: EmailStr = Field(description="Must be valid and unique within the job.", examples=["asha@example.com"])
    external_id: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = Field(
        None, description="Your own reference, e.g. a student ID.", examples=["S-001"]
    )
    course_name: NonEmptyStr | None = Field(None, description="Overrides the job's course name for this recipient only.")
    completion_date: date | None = Field(None, description="Printed as 'Completed on …'. Cannot be in the future.")

    _check_completion_date = field_validator("completion_date")(_not_in_future)


RecipientItem = Annotated[Any, WithJsonSchema(RecipientIn.model_json_schema())]


class JobCreateRequest(BaseModel):
    certificate: CertificateInfo
    recipients: list[RecipientItem] = Field(
        min_length=1,
        description="One entry per person. Invalid entries are reported and skipped; the rest are generated.",
    )


# ---------- Responses ----------


class ErrorResponse(BaseModel):
    detail: Any = Field(description="Human-readable explanation of the error.")


class InvalidRecipient(BaseModel):
    row_index: int = Field(description="Position in the submitted `recipients` list (0-based).")
    errors: list[str]


class JobCreatedResponse(BaseModel):
    job_id: str
    status: JobStatus
    total: int = Field(description="Recipients submitted.")
    accepted: int = Field(description="Valid recipients queued for generation.")
    invalid: int = Field(description="Recipients rejected by validation (see `invalid_recipients`).")
    invalid_recipients: list[InvalidRecipient]
    status_url: str = Field(description="Poll this URL to follow progress.")
    certificates_url: str


class StatusCounts(BaseModel):
    total: int = 0
    pending: int = 0
    processing: int = 0
    succeeded: int = 0
    failed: int = 0
    invalid: int = 0


class CertificateFailure(BaseModel):
    """One recipient that did not get a certificate, and why."""

    certificate_id: str
    row_index: int
    recipient_name: str | None
    recipient_email: str | None
    status: CertificateStatus = Field(description="FAILED = generation error, INVALID = bad input.")
    error: str | None
    certificate_url: str


class JobSummary(BaseModel):
    """One row in the job list."""

    job_id: str
    status: JobStatus
    title: str
    course_name: str
    issuer_name: str
    template: str
    counts: StatusCounts
    progress_percent: float
    created_at: UtcDatetime
    completed_at: UtcDatetime | None
    status_url: str


class JobPage(BaseModel):
    items: list[JobSummary]
    page: int
    page_size: int
    total: int = Field(description="Jobs matching the filter, across all pages.")


class EmailCounts(BaseModel):
    """Email delivery of the job's generated certificates."""

    not_requested: int = Field(0, description="Generated, but email never requested.")
    queued: int = 0
    sending: int = 0
    sent: int = 0
    failed: int = Field(0, description="See `email_error` on each certificate (filter with `?email_status=FAILED`).")


class EmailQueuedResponse(BaseModel):
    job_id: str
    queued: int = Field(description="Emails queued now.")
    already_sent: int = Field(description="Recipients who already have their certificate (skipped unless `resend=true`).")
    status_url: str = Field(description="Follow delivery under `emails` in the job status.")


class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    title: str
    course_name: str
    issuer_name: str
    issue_date: date
    template: str
    counts: StatusCounts
    progress_percent: float = Field(description="(succeeded + failed + invalid) / total × 100.")
    failed_certificates: list[CertificateFailure] = Field(
        description="Every recipient who did NOT get a certificate, with the reason. Empty when all succeeded."
    )
    emails: EmailCounts
    created_at: UtcDatetime
    started_at: UtcDatetime | None
    completed_at: UtcDatetime | None
    certificates_url: str
    download_url: str = Field(description="ZIP of all certificates generated so far.")


class CertificateOut(BaseModel):
    id: str
    job_id: str
    row_index: int
    recipient_name: str | None
    recipient_email: str | None
    external_id: str | None
    course_name: str | None
    completion_date: date | None
    status: CertificateStatus
    error: str | None = Field(description="Why it failed or was invalid; null on success.")
    attempts: int = Field(description="How many times generation was attempted (retries increase this).")
    generated_at: UtcDatetime | None
    download_url: str | None = Field(description="PDF link; only set when status is SUCCEEDED.")
    email_status: EmailStatus | None = Field(description="null = not emailed yet; QUEUED, SENDING, SENT or FAILED.")
    email_error: str | None
    emailed_at: UtcDatetime | None
    verification_code: str | None = Field(None, description="Public code printed under the QR code.")
    verification_url: str | None = Field(None, description="Public page the QR code links to (only verifies once SUCCEEDED).")


class CertificatePage(BaseModel):
    items: list[CertificateOut]
    page: int
    page_size: int
    total: int = Field(description="Certificates matching the filter, across all pages.")


class AddRecipientsRequest(BaseModel):
    recipients: list[RecipientItem] = Field(
        min_length=1,
        description="Same shape as in **POST /api/v1/jobs**. Emails must also be new to this job.",
    )


class RecipientsAddedResponse(BaseModel):
    job_id: str
    status: JobStatus = Field(description="PENDING or PROCESSING: the new certificates are being generated.")
    accepted: int = Field(description="New recipients queued for generation.")
    rejected: int = Field(description="New recipients rejected by validation (see `rejected_recipients`).")
    rejected_recipients: list[InvalidRecipient] = Field(
        description="`row_index` is the position in the submitted `recipients` list (0-based)."
    )
    counts: StatusCounts = Field(description="The job's updated totals, including the new recipients.")
    status_url: str


class RetryResponse(BaseModel):
    job_id: str
    status: JobStatus
    requeued: int = Field(description="Number of FAILED certificates queued again.")


class TemplateOut(BaseModel):
    id: str = Field(description="Use this value as `certificate.template`.")
    type: Literal["builtin", "custom"] = Field("builtin", description="Built into the app, or uploaded by a company.")
    name: str
    description: str
    preview_url: str = Field(description="Open in a browser to see a sample certificate.")


class TemplateDetail(TemplateOut):
    """A template; the branding fields are only set for custom templates."""

    primary_color: str | None = None
    secondary_color: str | None = None
    font: str | None = None
    title_text: str | None = None
    body_text: str | None = None
    border_style: str | None = None
    layout: str | None = None
    has_logo: bool = False
    has_signature: bool = False
    signer_name: str | None = None
    signer_title: str | None = None
    owner_id: str | None = None
    created_at: UtcDatetime | None = None


HexColor = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$"),
    AfterValidator(lambda v: ("#" + "".join(ch * 2 for ch in v[1:]) if len(v) == 4 else v).upper()),
]


def _check_placeholders(text: str) -> str:
    unknown = sorted({m.group(1) for m in PLACEHOLDER_PATTERN.finditer(text)} - set(PLACEHOLDERS))
    if unknown:
        allowed = ", ".join("{{" + p + "}}" for p in PLACEHOLDERS)
        raise ValueError(f"unknown placeholder(s) {', '.join('{{' + u + '}}' for u in unknown)}; allowed: {allowed}")
    if "{{" in PLACEHOLDER_PATTERN.sub("", text) or "}}" in PLACEHOLDER_PATTERN.sub("", text):
        raise ValueError("malformed placeholder: write placeholders as {{name}}, {{course}}, {{issuer}} or {{date}}")
    return text


class CustomTemplateForm(BaseModel):
    """Text fields of **POST /api/v1/templates** (sent as multipart form fields next to the images)."""

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] = Field(
        description="Shown in the template list.", examples=["Acme Academy Blue"]
    )
    primary_color: HexColor = Field(description="Hex colour for title, name and border, e.g. `#1F3B6F`.", examples=["#1F3B6F"])
    secondary_color: HexColor = Field(description="Hex colour for accents, e.g. `#C9A227`.", examples=["#C9A227"])
    font: Literal[tuple(FONTS)] = Field("Helvetica", description=f"One of: {', '.join(FONTS)}.")  # type: ignore[valid-type]
    title_text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)] = Field(
        "Certificate of Completion", description="Heading. May use placeholders."
    )
    body_text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)] = Field(
        "This is to certify that {{name}} has successfully completed {{course}}, awarded by {{issuer}} on {{date}}.",
        description="Must contain {{name}} (printed large). Placeholders: {{name}}, {{course}}, {{issuer}}, {{date}}.",
    )
    border_style: Literal["none", "simple", "double"] = "double"
    layout: Literal["landscape", "portrait"] = "landscape"
    signer_name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = Field(
        None, examples=["Dr. Maya Singh"]
    )
    signer_title: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = Field(
        None, examples=["Head of Training"]
    )

    _check_title = field_validator("title_text")(_check_placeholders)

    @field_validator("body_text")
    @classmethod
    def _body_has_name(cls, value: str) -> str:
        _check_placeholders(value)
        if not re.search(r"\{\{\s*name\s*\}\}", value):
            raise ValueError("must contain the {{name}} placeholder")
        return value

    @field_validator("signer_name", "signer_title")
    @classmethod
    def _empty_is_none(cls, value: str | None) -> str | None:
        return value or None


# ---------- Public verification ----------


class VerificationResult(BaseModel):
    """A genuine, issued certificate. Never includes the recipient's email."""

    valid: bool = Field(True, examples=[True])
    recipient_name: str = Field(examples=["Asha Rao"])
    course_name: str = Field(examples=["Intro to Python"])
    issuer: str = Field(examples=["Acme Academy"])
    completion_date: date | None = Field(examples=["2026-09-28"])
    issued_at: UtcDatetime | None = Field(description="When the PDF was generated.")


class VerificationNotFound(BaseModel):
    valid: bool = Field(False, examples=[False])
    message: str = Field("Certificate not found", examples=["Certificate not found"])
