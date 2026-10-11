# Bulk Certificate Generator

A backend API that accepts a certificate request for many recipients at once, validates every recipient,
generates a PDF certificate for each valid one from a chosen predefined template **in the background**,
tracks progress, and lets clients download certificates individually or as a ZIP.

Also: add recipients to an existing job (`PATCH`), a **QR code on every certificate** that opens a public
verification page, and **custom company templates** with your own logo, colours, wording and signature.

**Stack:** Python 3.11+ · FastAPI · SQLAlchemy 2.0 · SQLite (PostgreSQL-ready) · ReportLab · Pillow · pytest

---

## Architecture

```text
 Client
   │  POST /api/v1/jobs  (certificate info + N recipients)
   ▼
┌─────────────────────────── FastAPI process ───────────────────────────┐
│                                                                       │
│  api/jobs.py ──► services/job_service.create_job                      │
│                   ├─ validation.validate_recipients  (per row)        │
│                   ├─ INSERT job + 1 certificate row per recipient     │
│                   │    valid → PENDING, invalid → INVALID (+ errors)  │
│                   └─ COMMIT ──► JobRunner.submit(job_id) ──► 202      │
│                                         │                             │
│                     ThreadPoolExecutor (WORKER_THREADS)               │
│                                         ▼                             │
│  services/worker.process_job(job_id)    (own DB sessions)             │
│     claim job (PENDING → PROCESSING, atomic UPDATE)                   │
│     for each PENDING certificate:                                     │
│        mark PROCESSING → render PDF to .tmp → rename                  │
│        → SUCCEEDED  |  on exception → FAILED + error message          │
│        (one short transaction per certificate)                        │
│     finalize: COMPLETED / COMPLETED_WITH_ERRORS / FAILED              │
│                                                                       │
└──────────┬───────────────────────────────────────────┬────────────────┘
           ▼                                           ▼
   SQLite / PostgreSQL                         STORAGE_DIR/<job_id>/<certificate_id>.pdf
   (source of truth for all state)

 Client polls GET /jobs/{id}, lists /jobs/{id}/certificates, downloads PDFs or a ZIP.
```

### Project layout

```text
app/
  main.py                      FastAPI app factory + lifespan (DB init, runner, crash recovery)
  config.py                    Settings from env / .env (pydantic-settings)
  database.py                  Engine, SessionLocal, Base, get_db dependency
  migrations.py                Idempotent startup migrations (new columns, verification code backfill)
  models.py                    Job, Certificate, CustomTemplate tables, status enums
  schemas.py                   Pydantic request/response models
  api/
    deps.py                    Shared dependencies (runner, storage, 404 helpers)
    docs.py                    Swagger text: guide, section order, request examples
    jobs.py                    Submit, add recipients (PATCH), list, status, certificates, ZIP, retry
    certificates.py            Certificate metadata, PDF download
    templates.py               Built-in + custom templates: list, create (upload), details, preview, delete
    email.py                   POST /jobs/{id}/email: email certificates to recipients
    verify.py                  Public verify endpoints (JSON + HTML page), rate limited
  services/
    validation.py              Per-recipient validation → (valid, invalid-with-errors)
    job_service.py             Create job, add recipients, job lock, counts/progress, final status, retry, recovery
    job_runner.py              JobRunner interface, ThreadPoolRunner, SyncRunner
    worker.py                  process_job(): per-certificate failure isolation
    certificate_generator.py   Entry point: render a certificate to a file / a preview to bytes
    templates/
      __init__.py              TEMPLATES registry (id, name, description, render function)
      common.py                CertificateData + shared drawing helpers (fit text, borders, stars)
      classic.py … academic.py One module per design (8 in total)
      custom.py                Renderer for custom company templates
    custom_templates.py        Create/list/delete custom templates, resolve a job's template for the worker
    images.py                  ImageStore: verify uploads with Pillow, downscale, save under random names
    verification.py            Verification URL + public lookup (no email ever exposed)
    rate_limit.py              In-memory sliding-window limiter for the verify endpoints
    render_context.py          Settings the worker needs (PUBLIC_BASE_URL, UPLOAD_DIR)
    storage.py                 LocalStorage (atomic writes, path safety)
    mailer.py                  SmtpMailer: one reusable SMTP connection per batch (STARTTLS/SSL, login)
    email_delivery.py          Queue emails, send them in the background, per-recipient status
tests/                         pytest suite (142 tests)
```

---

## Setup

Requires **Python 3.11+**.

#### Windows (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env      # optional: defaults work without it
```

#### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env             # optional: defaults work without it
```

### Configuration (`.env` or environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./certificates.db` | Any SQLAlchemy URL. PostgreSQL: `postgresql+psycopg://user:pass@host/db` (+ `pip install "psycopg[binary]"`) |
| `STORAGE_DIR` | `./storage` | Where PDFs are written |
| `WORKER_THREADS` | `4` | Jobs processed concurrently |
| `MAX_RECIPIENTS_PER_JOB` | `1000` | Per request: `POST /jobs` or one `PATCH …/recipients` batch (422 above it) |
| `MAX_TOTAL_RECIPIENTS_PER_JOB` | `5000` | Per job, including recipients added later (409 above it) |
| `PUBLIC_BASE_URL` | `http://127.0.0.1:8000` | Address encoded in each certificate's QR code. **Set it before generating**: it is printed into the PDF |
| `VERIFY_RATE_LIMIT_PER_MINUTE` | `30` | Requests per minute per client IP on `/verify/*`; `0` disables |
| `UPLOAD_DIR` | `./uploads` | Where custom template logos/signatures are stored (random file names) |
| `MAX_UPLOAD_BYTES` | `2097152` | Max size of one uploaded image (2 MB; 413 above it) |
| `MAX_IMAGE_DIMENSION` | `4000` | Images wider/taller than this (px) are rejected |
| `IMAGE_DOWNSCALE_TO` | `1000` | Accepted images are shrunk to fit this box (px) |
| `LOG_LEVEL` | `INFO` | Logging level |
| `SMTP_HOST`, `SMTP_PORT` | *(unset)*, `587` | Mail server. Email is disabled until `SMTP_HOST` and `SMTP_FROM` are set |
| `SMTP_USERNAME`, `SMTP_PASSWORD` | *(unset)* | Login, if the server needs one (Gmail: an App Password) |
| `SMTP_STARTTLS` / `SMTP_USE_SSL` | `true` / `false` | Port 587 uses STARTTLS; port 465 uses `SMTP_USE_SSL=true` |
| `SMTP_FROM` | *(unset)* | Sender, e.g. `Acme Academy <certificates@acme.example>` |

## Run the application

```bash
uvicorn app.main:app --reload
```

- API: <http://127.0.0.1:8000>
- Interactive Swagger UI: <http://127.0.0.1:8000/swagger> (`/` and `/docs` redirect there; ReDoc at `/redoc`)
  - Sections are numbered in the order you use them: **1. Templates → 2. Jobs → 3. Certificates**.
  - Every endpoint is already in *Try it out* mode; **POST /api/v1/jobs** has an **Examples** dropdown
    (basic, with a template, with invalid rows).
- Health check: `GET /health`

Tables are created automatically on startup.

> Run a **single** server process (no `--workers N`). The background worker lives inside the API process;
> see [Design decisions](#design-decisions).

## Run the tests

```bash
pytest -v
```

Each test gets its own temporary SQLite database and storage folder. Most tests use a synchronous runner,
so a job has finished by the time `POST /jobs` returns, which keeps them deterministic. One test runs
the real thread pool end to end.

| Area required by the assignment | Test file |
|---|---|
| Creating a generation job | `tests/test_create_job.py` |
| Input validation | `tests/test_validation.py` |
| Certificate generation | `tests/test_generation.py` |
| Job status / progress | `tests/test_job_status.py` |
| Individual certificate failure, retry, crash recovery | `tests/test_failure_handling.py` |
| Retrieving certificates (list, filter, paginate, PDF, ZIP) | `tests/test_retrieval.py` |
| Templates (list, preview, every design renders, selection, unknown template) | `tests/test_templates.py` |
| Email (own PDF per recipient, failures isolated, no double-send, resend, 409/503, restart, SMTP steps) | `tests/test_email.py` |
| Adding recipients (PATCH): duplicates vs. existing, while running, before finalize, caps, ZIP numbering, email, recovery | `tests/test_add_recipients.py` |
| QR + verification: codes, JSON/HTML verify, privacy, escaping, rate limit, QR in every template, migration | `tests/test_verify.py` |
| Custom templates: upload, fake/oversize/large images, bad fields, preview, jobs, missing logo, delete | `tests/test_custom_templates.py` |

---

## Using the API

> On Windows use `curl.exe` in PowerShell (plain `curl` is an alias for `Invoke-WebRequest`), or use
> the Swagger UI at `/swagger`. Prefer `127.0.0.1` over `localhost` on Windows: `localhost` tries IPv6 first
> and adds a noticeable delay to every request.

### 0. Choose a template

Eight predefined designs are available. List them, then open a preview to see each one with sample data:

```bash
curl http://127.0.0.1:8000/api/v1/templates
# [{"id": "classic", "name": "Classic", "description": "Centered layout with a navy and gold double border.",
#   "preview_url": "http://127.0.0.1:8000/api/v1/templates/classic/preview"},
#  {"id": "modern", ...}, {"id": "minimal", ...}]

# Opens inline in a browser; with curl, save it:
curl -o classic_preview.pdf http://127.0.0.1:8000/api/v1/templates/classic/preview
```

| Template | Look |
|---|---|
| `classic` (default) | Centered, navy and gold double border |
| `modern` | Left-aligned, teal side band with issuer and date |
| `minimal` | Black-and-white serif, thin border, printer-friendly |
| `elegant` | Cream paper, gold double border with corner ornaments, italic serif |
| `corporate` | Dark header bar, left-aligned body, blue star badge, footer strip |
| `achievement` | Bold red title, gold seal with ribbons; for awards |
| `midnight` | Dark navy page, gold and white lettering; best on screen |
| `academic` | Diploma style: institution as heading, maroon border and seal |

Pass the id as `certificate.template` when submitting a job. The job status shows which template was used.
Every template prints a verification QR code in its bottom-right corner (see [section 9](#9-verify-a-certificate-public)).

#### Custom company templates (logo and branding)

Upload your own design as **multipart/form-data**. Only `name`, `primary_color`, `secondary_color` and `logo`
are required; the rest have defaults.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/templates \
  -F "name=Acme Academy Blue" \
  -F "primary_color=#1F3B6F" \
  -F "secondary_color=#C9A227" \
  -F "font=Times-Roman" \
  -F "title_text=Certificate of Excellence" \
  -F "body_text=This certifies that {{name}} completed {{course}} with {{issuer}} on {{date}}." \
  -F "border_style=double" \
  -F "layout=landscape" \
  -F "signer_name=Dr. Maya Singh" \
  -F "signer_title=Head of Training" \
  -F "logo=@logo.png" \
  -F "signature=@signature.png"
# -> 201 {"id": "3f2b8c1e-...", "type": "custom", "name": "Acme Academy Blue", "primary_color": "#1F3B6F", ...}

curl http://127.0.0.1:8000/api/v1/templates                     # built-in AND custom, each with "type"
curl http://127.0.0.1:8000/api/v1/templates/<template_id>       # details
curl -o custom_preview.pdf http://127.0.0.1:8000/api/v1/templates/<template_id>/preview
curl -X DELETE http://127.0.0.1:8000/api/v1/templates/<template_id>   # 204, or 409 while a job uses it
```

Then submit a job with `"template": "<template_id>"`. Requests using built-in names work unchanged.

| Field | Rules |
|---|---|
| `name` | 1–100 characters |
| `primary_color`, `secondary_color` | Hex: `#RRGGBB` or `#RGB` |
| `font` | `Helvetica` (default), `Times-Roman` or `Courier` (built-in PDF fonts) |
| `title_text` | ≤ 120 characters; placeholders allowed |
| `body_text` | ≤ 1000 characters; **must contain `{{name}}`** (printed large). Only `{{name}}`, `{{course}}`, `{{issuer}}`, `{{date}}` |
| `border_style` | `none`, `simple` or `double` (default) |
| `layout` | `landscape` (default) or `portrait` |
| `logo` (required), `signature` (optional) | Real PNG or JPEG, ≤ 2 MB, ≤ 4000 px per side; larger images are downscaled |
| `signer_name`, `signer_title` | Optional, ≤ 100 characters; shown under the signature line |

Unknown fonts, placeholders, bad colours or a file that isn't really a PNG/JPEG return **422** with the field
name; an image over the size limit returns **413**.

### 1. Submit a certificate generation request

`POST /api/v1/jobs`

```bash
curl -X POST http://127.0.0.1:8000/api/v1/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "certificate": {
      "title": "Certificate of Completion",
      "course_name": "Intro to Python",
      "issuer_name": "Acme Academy",
      "issue_date": "2026-10-01",
      "template": "modern"
    },
    "recipients": [
      {"name": "Asha Rao",   "email": "asha@example.com", "external_id": "S-001"},
      {"name": "Ben Carter", "email": "ben@example.com",  "completion_date": "2026-09-28"},
      {"name": "",           "email": "not-an-email"},
      {"name": "Asha Again", "email": "ASHA@example.com"}
    ]
  }'
```

#### Request fields

| Field | Required | Rules |
|---|---|---|
| `certificate.course_name`, `certificate.issuer_name` | yes | non-empty, ≤ 200 chars |
| `certificate.title` | no | default `"Certificate of Completion"` |
| `certificate.issue_date` | no | ISO date, not in the future; default today |
| `certificate.template` | no | `classic` (default), `modern` or `minimal`; see `GET /api/v1/templates`. Unknown → 422 |
| `recipients` | yes | 1 … `MAX_RECIPIENTS_PER_JOB` items |
| `recipients[].name` | yes | trimmed, 1–100 chars |
| `recipients[].email` | yes | valid email, unique within the job (case-insensitive) |
| `recipients[].external_id` | no | your own reference (student id, …), ≤ 100 chars |
| `recipients[].course_name` | no | overrides the course name for this recipient |
| `recipients[].completion_date` | no | ISO date, not in the future; printed on the certificate |

#### Response: `202 Accepted` (real output)

```json
{
  "job_id": "b799fa0f-f1c8-4834-9d59-a27492424fa9",
  "status": "PROCESSING",
  "total": 4,
  "accepted": 2,
  "invalid": 2,
  "invalid_recipients": [
    {
      "row_index": 2,
      "errors": [
        "name: String should have at least 1 character",
        "email: value is not a valid email address: An email address must have an @-sign."
      ]
    },
    { "row_index": 3, "errors": ["email: duplicate of recipient at row 0"] }
  ],
  "status_url": "http://127.0.0.1:8000/api/v1/jobs/b799fa0f-f1c8-4834-9d59-a27492424fa9",
  "certificates_url": "http://127.0.0.1:8000/api/v1/jobs/b799fa0f-f1c8-4834-9d59-a27492424fa9/certificates"
}
```

`row_index` is the 0-based position in your `recipients` array.

#### Error responses

| Case | Status | Body |
|---|---|---|
| Malformed body, missing/invalid `certificate` block, empty `recipients` | 422 | FastAPI validation `detail` list |
| More than `MAX_RECIPIENTS_PER_JOB` recipients | 422 | `{"detail": "A job may contain at most 1000 recipients (got 1500)."}` |
| **Every** recipient invalid | 422 | `{"detail": {"message": "...no job was created.", "invalid_recipients": [...]}}` |

### 2. List jobs

```bash
# Newest first, with counts and progress for each job
curl "http://127.0.0.1:8000/api/v1/jobs?page=1&page_size=20"

# Only jobs in one status (PENDING, PROCESSING, COMPLETED, COMPLETED_WITH_ERRORS, FAILED)
curl "http://127.0.0.1:8000/api/v1/jobs?status=COMPLETED_WITH_ERRORS"
```

```json
{
  "items": [
    {
      "job_id": "647db26a-2a46-4de3-a853-4e2422f538b6",
      "status": "COMPLETED_WITH_ERRORS",
      "title": "Certificate of Completion",
      "course_name": "Intro to Python",
      "issuer_name": "Acme Academy",
      "template": "modern",
      "counts": { "total": 4, "pending": 0, "processing": 0, "succeeded": 2, "failed": 0, "invalid": 2 },
      "progress_percent": 100.0,
      "created_at": "2026-10-10T07:12:03.120394Z",
      "completed_at": "2026-10-10T07:12:03.201554Z",
      "status_url": "http://127.0.0.1:8000/api/v1/jobs/647db26a-2a46-4de3-a853-4e2422f538b6"
    }
  ],
  "page": 1,
  "page_size": 20,
  "total": 1
}
```

Counts for the whole page come from one grouped query, not one query per job.

### 3. Check progress / result of one job

```bash
curl http://127.0.0.1:8000/api/v1/jobs/<job_id>
```

```json
{
  "job_id": "b799fa0f-f1c8-4834-9d59-a27492424fa9",
  "status": "COMPLETED_WITH_ERRORS",
  "title": "Certificate of Completion",
  "course_name": "Intro to Python",
  "issuer_name": "Acme Academy",
  "issue_date": "2026-10-01",
  "template": "modern",
  "counts": { "total": 4, "pending": 0, "processing": 0, "succeeded": 2, "failed": 0, "invalid": 2 },
  "progress_percent": 100.0,
  "failed_certificates": [
    {
      "certificate_id": "0c1d6f0e-...",
      "row_index": 2,
      "recipient_name": "",
      "recipient_email": "not-an-email",
      "status": "INVALID",
      "error": "name: String should have at least 1 character; email: value is not a valid email address: ...",
      "certificate_url": "http://127.0.0.1:8000/api/v1/certificates/0c1d6f0e-..."
    },
    {
      "certificate_id": "5b7a2c91-...",
      "row_index": 3,
      "recipient_name": "Asha Again",
      "recipient_email": "ASHA@example.com",
      "status": "INVALID",
      "error": "email: duplicate of recipient at row 0",
      "certificate_url": "http://127.0.0.1:8000/api/v1/certificates/5b7a2c91-..."
    }
  ],
  "created_at": "2026-10-09T18:23:22.016249Z",
  "started_at": "2026-10-09T18:23:22.028027Z",
  "completed_at": "2026-10-09T18:23:22.075588Z",
  "certificates_url": "http://127.0.0.1:8000/api/v1/jobs/b799fa0f-.../certificates",
  "download_url": "http://127.0.0.1:8000/api/v1/jobs/b799fa0f-.../download"
}
```

Poll until `status` is `COMPLETED`, `COMPLETED_WITH_ERRORS` or `FAILED`.

`counts` gives the totals and `failed_certificates` names every recipient that did **not** get a certificate
(`FAILED` = generation error, `INVALID` = bad input) together with the reason, so one call shows what went
wrong. The full list, including successes, is in the certificates endpoint below.

### 4. See which certificates succeeded or failed

```bash
# All certificates of the job (paginated, ordered by row_index)
curl "http://127.0.0.1:8000/api/v1/jobs/<job_id>/certificates?page=1&page_size=50"

# Only the failed / invalid / succeeded ones
curl "http://127.0.0.1:8000/api/v1/jobs/<job_id>/certificates?status=FAILED"
curl "http://127.0.0.1:8000/api/v1/jobs/<job_id>/certificates?status=INVALID"
curl "http://127.0.0.1:8000/api/v1/jobs/<job_id>/certificates?status=SUCCEEDED"
```

```json
{
  "items": [
    {
      "id": "8e11f43f-6f32-478d-8d65-0aa0a658ff10",
      "job_id": "b799fa0f-f1c8-4834-9d59-a27492424fa9",
      "row_index": 0,
      "recipient_name": "Asha Rao",
      "recipient_email": "asha@example.com",
      "external_id": "S-001",
      "course_name": null,
      "completion_date": null,
      "status": "SUCCEEDED",
      "error": null,
      "attempts": 1,
      "generated_at": "2026-10-09T18:23:22.053299Z",
      "download_url": "http://127.0.0.1:8000/api/v1/certificates/8e11f43f-.../download"
    }
  ],
  "page": 1,
  "page_size": 50,
  "total": 2
}
```

`page_size` is capped at 100. For `FAILED` and `INVALID` rows, `error` explains why.

### 5. Retrieve generated certificates

```bash
# Metadata of one certificate
curl http://127.0.0.1:8000/api/v1/certificates/<certificate_id>

# Download one PDF (-OJ saves it under the server-provided filename, e.g. Ben_Carter_8e11f43f.pdf)
curl -OJ http://127.0.0.1:8000/api/v1/certificates/<certificate_id>/download

# Download every generated certificate of a job as a ZIP (0001_Asha_Rao.pdf, 0002_Ben_Carter.pdf, ...)
curl -o certificates.zip http://127.0.0.1:8000/api/v1/jobs/<job_id>/download
```

- Downloading a certificate that is not `SUCCEEDED` returns **409** (`"Certificate is not available for download (status: INVALID)"`).
- The ZIP contains every certificate that has succeeded **so far**, so it also works while a job is running.
  It returns **409** if nothing has been generated yet.

### 6. Retry failed certificates (optional)

```bash
# Re-queue only the FAILED certificates of a finished job (409 while the job is still running)
curl -X POST http://127.0.0.1:8000/api/v1/jobs/<job_id>/retry
# -> {"job_id": "...", "status": "PENDING", "requeued": 1}
```

### 7. Email certificates to recipients (optional)

Set the SMTP values in `.env` (see `.env.example` for Gmail, Outlook and Mailtrap) and restart the server. Then:

```bash
# Send each recipient their own certificate PDF as an attachment (runs in the background)
curl -X POST http://127.0.0.1:8000/api/v1/jobs/<job_id>/email
# -> 202 {"job_id": "...", "queued": 2, "already_sent": 0, "status_url": "..."}

# Follow delivery in the job status
curl http://127.0.0.1:8000/api/v1/jobs/<job_id>
# -> "emails": {"not_requested": 0, "queued": 0, "sending": 0, "sent": 2, "failed": 0}

# Who did not receive it, and why
curl "http://127.0.0.1:8000/api/v1/jobs/<job_id>/certificates?email_status=FAILED"

# Calling it again only emails those who have not received it yet (never sent / failed).
# To email everyone again:
curl -X POST "http://127.0.0.1:8000/api/v1/jobs/<job_id>/email?resend=true"
```

Each email has the subject *"Your certificate: &lt;course&gt;"*, a short greeting, and the recipient's PDF attached.
Only `SUCCEEDED` certificates are emailed. The endpoint returns **409** while the job is still generating
(or if nothing was generated), and **503** if SMTP isn't configured.

> **Tip for trying it safely:** use a [Mailtrap](https://mailtrap.io) inbox (free) as the SMTP server, so no real
> person receives test emails.

### 8. Add recipients to an existing job

`PATCH /api/v1/jobs/{job_id}/recipients` takes recipients in the same shape as job creation:

```bash
curl -X PATCH http://127.0.0.1:8000/api/v1/jobs/<job_id>/recipients \
  -H "Content-Type: application/json" \
  -d '{
    "recipients": [
      {"name": "Dina Patel", "email": "dina@example.com"},
      {"name": "Asha Again", "email": "ASHA@example.com"}
    ]
  }'
# -> 202 {"job_id": "...", "status": "PENDING", "accepted": 1, "rejected": 1,
#         "rejected_recipients": [{"row_index": 1, "errors": ["email: already a recipient of this job (row 0)"]}],
#         "counts": {"total": 5, "pending": 1, "processing": 0, "succeeded": 3, "failed": 0, "invalid": 1},
#         "status_url": "..."}
```

- Same validation as `POST /jobs`, and an email already in the job (case-insensitive) is a duplicate.
- Only the new certificates are generated; existing ones are never regenerated. Rejected rows are stored as
  `INVALID`, exactly like at creation, so they show up in `failed_certificates`.
- A finished job (`COMPLETED`, `COMPLETED_WITH_ERRORS`, `FAILED`) goes back to `PENDING` and finishes again with
  the right final status. Calling it while the job is `PROCESSING` is safe: the running worker picks up the new rows.
- Row numbers continue, so the ZIP continues too (`0004_Dina_Patel.pdf` after `0003_…`).
- `POST …/email` without `resend=true` then emails only the new recipients.
- **404** unknown job · **409** the job would exceed `MAX_TOTAL_RECIPIENTS_PER_JOB` · **422** empty or too large
  batch, or no valid new recipient (nothing is added).

### 9. Verify a certificate (public)

Every certificate has an unguessable `verification_code` (shown in its details). The QR code on the PDF opens
`{PUBLIC_BASE_URL}/api/v1/verify/{code}/page`. No authentication is needed:

```bash
curl http://127.0.0.1:8000/api/v1/verify/<verification_code>
# -> 200 {"valid": true, "recipient_name": "Asha Rao", "course_name": "Intro to Python",
#         "issuer": "Acme Academy", "completion_date": null, "issued_at": "2026-10-10T09:12:44Z"}

curl http://127.0.0.1:8000/api/v1/verify/not-a-real-code
# -> 404 {"valid": false, "message": "Certificate not found"}

# The page the QR code opens: big green "VALID CERTIFICATE", or red "CERTIFICATE NOT FOUND"
curl http://127.0.0.1:8000/api/v1/verify/<verification_code>/page
```

- The recipient's email address is never shown.
- Only generated (`SUCCEEDED`) certificates verify. Each also has a `validity` field (`VALID`/`REVOKED`) so
  revocation can be added later; today everything that isn't valid answers "not found".
- More than `VERIFY_RATE_LIMIT_PER_MINUTE` requests per minute from one IP get **429** with `Retry-After`.
- To scan the QR from a phone, set `PUBLIC_BASE_URL` to an address the phone can reach (e.g. your PC's LAN IP,
  `http://192.168.1.20:8000`, and start uvicorn with `--host 0.0.0.0`) **before** generating certificates.

### Status reference

| Certificate status | Meaning |
|---|---|
| `PENDING` | Valid, waiting to be generated |
| `PROCESSING` | Being generated right now |
| `SUCCEEDED` | PDF generated, downloadable |
| `FAILED` | Generation raised an error (see `error`); can be retried |
| `INVALID` | Input failed validation (see `error`); never generated |

| Job status | Rule |
|---|---|
| `PENDING` | Accepted, waiting for a worker |
| `PROCESSING` | A worker is generating certificates |
| `COMPLETED` | Every certificate succeeded |
| `COMPLETED_WITH_ERRORS` | At least one succeeded and at least one failed or was invalid |
| `FAILED` | None succeeded |

`progress_percent` = (SUCCEEDED + FAILED + INVALID) / total × 100.

---

## Design decisions

### 1. Background processing instead of synchronous generation
A request may contain up to 1000 recipients. Generating PDFs inside the request would hold the HTTP
connection open for many seconds, risk proxy/client timeouts, and give the client no progress information.
Instead `POST /jobs` only validates and stores the request, returns **202 Accepted** with a `job_id`, and the
client polls `GET /jobs/{id}`. *Why 202 and not 201:* the resource (the job) exists, but the work it
represents is not done. 202 tells the client "accepted for processing, check back later".

Measured locally (SQLite, Windows laptop): `POST` with 1000 recipients returns in ~0.25 s; generation runs at
~60 certificates/s per job; status polls take ~5–10 ms while the job is running.

### 2. In-process thread pool instead of Celery/RQ
The assignment needs background work but not distributed infrastructure. A `ThreadPoolExecutor` owned by the
app (created in the FastAPI lifespan, size `WORKER_THREADS`) needs **no Redis/broker** and keeps setup to
`pip install`. The API only depends on a tiny `JobRunner` interface (`submit(job_id)`, `shutdown()`), so
switching to Celery/RQ means writing one new runner class whose `submit` enqueues `process_job`. Tests use
`SyncRunner` for deterministic results.

*Trade-off:* jobs share CPU with the API and cannot be spread across machines, and you must run a single API
process. This is acceptable because all state lives in the database (next point), so nothing is lost on a
restart.

### 3. The database is the source of truth, with crash recovery
Every state change (job and certificate) is committed to the DB; nothing important lives only in memory.
- On startup, `recover_incomplete_jobs` resets interrupted `PROCESSING` jobs/certificates to `PENDING` and
  re-submits every `PENDING` job.
- The worker only processes `PENDING` certificates, so already `SUCCEEDED` ones are **never regenerated**.
- A job is claimed with an atomic `UPDATE … WHERE status='PENDING'`, so two workers can never run the same job.
- On graceful shutdown the runner stops between certificates; the rest resumes on next start.

This was verified live: a server killed with `taskkill /F` while a 600-recipient job was at 184/600 resumed on
restart and completed all 600, regenerating only the one certificate that was mid-flight.

### 4. Two-level validation with partial acceptance
- **Request-level** problems (bad `certificate` block, empty list, too many recipients) → **422** and nothing is
  created, because the request as a whole is unusable.
- **Recipient-level** problems are checked **per row** (`validation.validate_recipients`). Invalid rows are
  stored as `INVALID` with readable errors and reported in the 202 response; valid rows are generated. A typo
  in one row out of 500 shouldn't force the client to resubmit everything. If **no** row is valid, a job
  would be pointless, so the API returns 422 with the per-row errors.
- `recipients` is typed as a list of raw objects in the request schema on purpose, so Pydantic doesn't reject
  the whole request because of one bad row.

### 5. Failure isolation per certificate
Each certificate is generated inside its own `try/except` and its result is committed in its **own short
transaction** (`worker._process_certificate`). An exception marks just that certificate `FAILED` with
`"<ExceptionType>: <message>"` and the loop continues. The PDF is rendered while no transaction is open, so
database locks are held only for milliseconds. Background threads always open their own sessions and never
share the request's session. `POST /jobs/{id}/retry` re-queues only `FAILED` certificates; `INVALID` ones are
not retried, because the same input would fail again.

### 6. Counts are computed, not stored
Job counters come from a `GROUP BY status` over the certificate rows (`job_service.get_status_counts`).
There is a single source of truth and no counter drift when threads update rows concurrently. With the index
on `certificates.job_id` this is cheap for jobs of this size.

### 7. PDF via ReportLab, templates as a registry of render functions
ReportLab is pure Python (no system dependencies like wkhtmltopdf/Chrome), fast, and produces small (~2–3 KB)
PDFs. The assignment requires one predefined template; as an optional extra there are eight. Each template is
a plain function `render(canvas, data)` in its own module under `app/services/templates/`, registered in the
`TEMPLATES` dict (`templates/__init__.py`) with an id, name and description. That one registry drives everything:
- `GET /templates` lists it, and `GET /templates/{id}/preview` renders it with sample data.
- The request schema validates `certificate.template` against it (unknown id → 422 before any job is created).
- The chosen id is stored on the job (`template_id`), and the worker passes it to `generate_certificate_pdf`.

Adding a design = add one module with a `render` function + one line in `TEMPLATES`; no API, DB or worker change.
Templates are code rather than uploaded files on purpose: the assignment excludes a template editor, and
code-defined layouts can be tested. Shared helpers in `templates/common.py` (`draw_fitted`) make long text shrink to fit and fall back
to an ellipsis in every template, so nothing overflows the page. The certificate ID is
printed in the footer, so a printed certificate can be traced back to its record.

### 8. Storage abstraction and atomic writes
`LocalStorage` hides the filesystem: files live at `STORAGE_DIR/<job_id>/<certificate_id>.pdf` and the DB stores
**relative** paths, so the storage root can move. PDFs are written to a `.tmp` file and atomically renamed only
on success, so a crash never leaves a half-written PDF behind a `SUCCEEDED` row. Swapping in S3 would mean
implementing the same small interface.

### 9. Limits
- `MAX_RECIPIENTS_PER_JOB` (default 1000) bounds request size and memory use; larger batches can be split.
- `page_size` ≤ 100 on listings.

### 10. Email delivery

Sending is slow (about a second per message) and can fail per recipient, so it follows the same pattern as
generation:
- `POST /jobs/{id}/email` only marks certificates `QUEUED` and returns **202**; a background task sends them.
- One SMTP connection is reused for the whole batch. If one send fails, that certificate is marked `FAILED` with
  the server's error, the connection is reset, and the next recipient continues (failure isolation again).
- Each email is claimed atomically (`QUEUED → SENDING`), so overlapping requests never email someone twice.
  Without `resend=true`, people already `SENT` are skipped, so the call is safe to repeat.
- State is in the DB. On restart, anything `QUEUED` (or interrupted mid-send) is resumed. A message cut off
  mid-send may be delivered twice (at-least-once), which is preferable to silently never sending it.
- The mailer is behind a tiny interface (`send(message)`), so tests use a fake that records messages, and a
  provider API (SendGrid, SES) could replace SMTP without touching the delivery logic.

### 11. Database
SQLite by default for zero-setup. The engine enables WAL mode so status reads don't block worker writes.
Everything goes through SQLAlchemy, so `DATABASE_URL` can point at PostgreSQL without code changes. Tables are
created with `create_all` on startup, then `app/migrations.py` adds columns introduced later
(`ALTER TABLE … ADD COLUMN`, `CREATE UNIQUE INDEX IF NOT EXISTS`, both valid on SQLite and PostgreSQL) and
backfills a verification code for certificates created before that feature. Each step checks the live schema
first, so it is safe on every start. A larger project would switch to **Alembic**.

### 12. Adding recipients safely while a job may be running
The risk is a race between `PATCH` and the worker finishing the job: if the worker decided "no more work" and
then PATCH inserted rows into a job it still saw as running, those rows would never be processed. Two rules
prevent it:
- **One job lock.** `PATCH` (insert rows, reopen the job) and `finalize_job` (check for work, set the final
  status) both start with `lock_job`, a no-op `UPDATE` on the job row. On PostgreSQL that holds a row lock until
  commit; on SQLite it takes the database write lock. So the two never interleave: either PATCH commits first
  and finalize sees the new rows and declines, or finalize commits first and PATCH sees a finished job, sets it
  back to `PENDING` and re-queues it.
- **The worker loops until finalize agrees.** It keeps fetching `PENDING` rows it hasn't attempted in this run,
  and only stops when `finalize_job` confirms, under the lock, that nothing new arrived. Every certificate is
  attempted at most once per run, and only `PENDING` ones, so nothing is processed twice.

Row numbering and the duplicate-email check read the job's rows inside the same lock, so two concurrent PATCH
requests can't take the same row numbers or add the same email twice. Crash recovery needs nothing new:
re-opened jobs are `PENDING`, which startup already re-queues.

### 13. Verification codes and QR codes
- Codes are `secrets.token_urlsafe(12)` (96 random bits), stored with a unique index. They are not derived from
  the certificate id, so knowing one certificate tells you nothing about others, and they are kept when a PDF is
  regenerated by retry.
- The QR is drawn with ReportLab's built-in `reportlab.graphics.barcode.qr` (no new dependency). It is also a
  clickable link in the PDF, so it can be checked on screen. Each built-in template declares a `QrPlacement`
  in an empty corner of its design (the Academic signatures were shifted slightly left to make room). Custom
  templates keep their bottom-right corner free for it.
- The verify endpoints are public and return the same "not found" for unknown, ungenerated or revoked codes. The
  HTML page escapes every value. Rate limiting is an in-memory sliding window per client IP. That suits this
  single-process app; with several processes, use Redis or a reverse-proxy rule (e.g. nginx `limit_req`).

### 14. Custom templates
- One configurable renderer (`templates/custom.py`) draws every custom template from stored settings, which
  is simpler and safer than accepting arbitrary HTML or layout code. It uses only built-in PDF fonts, so there are
  no font files to manage.
- Uploads are never trusted: Pillow decodes the real bytes (the extension and `Content-Type` are ignored),
  dimensions are checked before pixels are loaded, and the image is re-encoded as a fresh PNG under a random
  name in `UPLOAD_DIR`. Stored names are validated before any path is built, so path traversal is impossible.
- Text is drawn with plain canvas calls (no markup is interpreted), control characters are stripped, and every
  line is shrunk, wrapped or truncated to fit.
- The worker resolves the job's template **per certificate**. A missing logo then fails only the certificates
  rendered after it went missing, each with a clear error, and they can be retried once the file is back.
- Deleting a template that any job uses returns 409, because those jobs may still need it for retries or PATCH.
  `owner_id` is stored (nullable) so templates can be scoped to their owner once authentication exists.

---

## Known limitations and future improvements

- **Distributed queue:** move to Celery/RQ/Arq (a new `JobRunner`) to scale workers horizontally and run
  multiple API processes. Within a job, certificates could also be generated in parallel chunks.
- **Object storage:** S3/GCS implementation of the storage interface, with pre-signed download URLs.
- **Authentication and authorization:** jobs are currently readable by anyone who knows the UUID. Add API keys
  or OAuth and scope jobs to their owner.
- **Rate limiting:** only the public verify endpoints are limited, in memory. Use Redis or a proxy rule for
  several processes, and add limits to the other endpoints.
- **Revocation:** the `validity` column exists; an endpoint to revoke a certificate (and show "REVOKED" on the
  verify page) is the next step.
- **Alembic migrations** instead of the small startup migration module.
- **Notifications:** a webhook callback when a job finishes, or an option to email certificates automatically
  as soon as generation completes.
- **Email at scale:** a transactional email API (SendGrid, SES) with bounce handling and per-second rate limits.
- **Richer custom templates:** background images, freely positioned text, and uploaded TTF fonts.
- **Unicode fonts:** the built-in Helvetica covers Latin-1 (e.g. "José Müller" works). Names in other scripts
  (e.g. Devanagari, CJK) don't crash generation but don't render correctly. The fix is to register a TTF font
  such as Noto Sans in `templates/common.py`.
- **Idempotency:** an `Idempotency-Key` header so a client that retries a timed-out POST doesn't create a
  duplicate job.
- **Retention:** clean up old jobs and files after N days.
