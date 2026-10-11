"""Text and examples for the Swagger UI (/swagger). Docs only; no behaviour lives here."""

from typing import Any

from app.schemas import ErrorResponse

API_DESCRIPTION = """
Generate PDF certificates for many recipients in **one request**.

### How it works
1. **Pick a design**: **GET /api/v1/templates**, then open a `preview_url` to see it.
   Or upload your own logo and branding with **POST /api/v1/templates** and use the returned `id`.
2. **Submit a job**: **POST /api/v1/jobs** with the certificate details and the recipient list.
   You get `202 Accepted` and a `job_id` straight away; certificates are generated in the background.
   Late additions: **PATCH /api/v1/jobs/{job_id}/recipients** adds more people to the same job.
3. **Track progress**: **GET /api/v1/jobs/{job_id}** until `status` is `COMPLETED`,
   `COMPLETED_WITH_ERRORS` or `FAILED`. `failed_certificates` lists anyone who didn't get one, and why.
4. **Download**: one PDF with **GET /api/v1/certificates/{id}/download**, or everything as a ZIP
   with **GET /api/v1/jobs/{job_id}/download**.
5. **Email** *(optional)*: **POST /api/v1/jobs/{job_id}/email** sends every recipient their own PDF.
6. **Verify** *(public)*: every PDF carries a QR code that opens **GET /api/v1/verify/{code}/page**.

Invalid recipients are reported and skipped; they never block the valid ones.

*Tip:* every endpoint below is already in "Try it out" mode. Edit the example and press **Execute**.
"""

TAGS_METADATA = [
    {"name": "1. Templates", "description": "Built-in designs and your own custom templates (logo, colours, wording). "
     "Preview one before choosing it."},
    {"name": "2. Jobs", "description": "Submit recipients in bulk, list jobs, track progress, download a ZIP, retry failures."},
    {"name": "3. Certificates", "description": "Look up or download an individual certificate, "
     "and the public **verify** endpoints the QR code on each PDF opens (no authentication)."},
    {"name": "4. Email", "description": "Send each recipient their certificate PDF by email (SMTP)."},
    {"name": "System", "description": "Service health."},
]

SWAGGER_UI_PARAMETERS = {
    "docExpansion": "list",           # show endpoints, keep details collapsed
    "defaultModelsExpandDepth": -1,   # hide the long "Schemas" section at the bottom
    "tryItOutEnabled": True,          # editable examples without clicking "Try it out"
    "displayRequestDuration": True,
}

CREATE_JOB_EXAMPLES: dict[str, dict[str, Any]] = {
    "basic": {
        "summary": "Basic: 3 recipients, default template",
        "value": {
            "certificate": {"course_name": "Intro to Python", "issuer_name": "Acme Academy"},
            "recipients": [
                {"name": "Asha Rao", "email": "asha@example.com"},
                {"name": "Ben Carter", "email": "ben@example.com"},
                {"name": "Chen Li", "email": "chen@example.com"},
            ],
        },
    },
    "template": {
        "summary": "Choose a template + optional fields",
        "value": {
            "certificate": {
                "title": "Certificate of Achievement",
                "course_name": "Data Science Bootcamp",
                "issuer_name": "Acme Academy",
                "issue_date": "2026-10-01",
                "template": "achievement",
            },
            "recipients": [
                {"name": "Asha Rao", "email": "asha@example.com", "external_id": "S-001", "completion_date": "2026-09-28"},
                {"name": "Ben Carter", "email": "ben@example.com", "course_name": "Data Science Bootcamp (Honours)"},
            ],
        },
    },
    "invalid_rows": {
        "summary": "Mixed: some recipients invalid (partial acceptance)",
        "value": {
            "certificate": {"course_name": "Intro to Python", "issuer_name": "Acme Academy", "template": "modern"},
            "recipients": [
                {"name": "Asha Rao", "email": "asha@example.com"},
                {"name": "", "email": "not-an-email"},
                {"name": "Asha Again", "email": "ASHA@example.com"},
                {"name": "Ben Carter", "email": "ben@example.com"},
            ],
        },
    },
}


ADD_RECIPIENTS_EXAMPLES: dict[str, dict[str, Any]] = {
    "add": {
        "summary": "Add two late registrations",
        "value": {
            "recipients": [
                {"name": "Dina Patel", "email": "dina@example.com"},
                {"name": "Eli Moreno", "email": "eli@example.com", "completion_date": "2026-09-30"},
            ]
        },
    },
    "duplicate": {
        "summary": "One new, one already in the job (rejected)",
        "value": {
            "recipients": [
                {"name": "Dina Patel", "email": "dina@example.com"},
                {"name": "Asha Again", "email": "asha@example.com"},
            ]
        },
    },
}


def error(description: str) -> dict[str, Any]:
    return {"model": ErrorResponse, "description": description}


NOT_FOUND_JOB = {404: error("No job with this id.")}
NOT_FOUND_CERTIFICATE = {404: error("No certificate with this id (or its file is missing).")}
PDF_RESPONSE = {200: {"content": {"application/pdf": {}}, "description": "The PDF file."}}
ZIP_RESPONSE = {200: {"content": {"application/zip": {}}, "description": "ZIP archive of PDFs."}}
