import io
from datetime import date

import pytest
from pypdf import PdfReader
from sqlalchemy import select, text, update

from app.database import SessionLocal, init_db
from app.models import Certificate, Validity
from app.services import certificate_generator, worker
from app.services.certificate_generator import TEMPLATES, CertificateData, generate_certificate_pdf
from app.services.verification import verification_url
from tests.conftest import API, NoopRunner, create_job, make_payload

real_generate = certificate_generator.generate_certificate_pdf


def _certificates(client, job_id):
    return client.get(f"{API}/jobs/{job_id}/certificates").json()["items"]


def _pdf_links(pdf_bytes: bytes) -> list[str]:
    page = PdfReader(io.BytesIO(pdf_bytes)).pages[0]
    return [a.get_object()["/A"]["/URI"] for a in page.get("/Annots", []) if "/A" in a.get_object()]


# ---------- Codes ----------


def test_every_certificate_gets_a_unique_unguessable_code(client):
    job_id = create_job(client).json()["job_id"]

    codes = [c["verification_code"] for c in _certificates(client, job_id)]

    assert len(set(codes)) == 3
    assert all(len(code) >= 16 for code in codes)
    cert = _certificates(client, job_id)[0]
    assert cert["verification_url"] == f"http://127.0.0.1:8000{API}/verify/{cert['verification_code']}/page"


def test_code_is_kept_when_a_certificate_is_retried(client, monkeypatch):
    def fail_ben(data, output_path, template_id="classic"):
        if data.recipient_name == "Ben Carter":
            raise RuntimeError("boom")
        real_generate(data, output_path, template_id)

    monkeypatch.setattr(worker, "generate_certificate_pdf", fail_ben)
    job_id = create_job(client).json()["job_id"]
    before = {c["id"]: c["verification_code"] for c in _certificates(client, job_id)}

    monkeypatch.setattr(worker, "generate_certificate_pdf", real_generate)
    client.post(f"{API}/jobs/{job_id}/retry")

    after = {c["id"]: c["verification_code"] for c in _certificates(client, job_id)}
    assert after == before


# ---------- JSON endpoint ----------


def test_verify_valid_code_returns_public_details_without_email(client):
    recipients = [{"name": "Asha Rao", "email": "asha@example.com", "completion_date": "2026-01-10"}]
    job_id = create_job(client, make_payload(recipients=recipients)).json()["job_id"]
    code = _certificates(client, job_id)[0]["verification_code"]

    response = client.get(f"{API}/verify/{code}")

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["recipient_name"] == "Asha Rao"
    assert body["course_name"] == "Intro to Python"
    assert body["issuer"] == "Acme Academy"
    assert body["completion_date"] == "2026-01-10"
    assert body["issued_at"]
    assert "asha@example.com" not in response.text
    assert "email" not in body


def test_verify_uses_per_recipient_course_override(client):
    job_id = create_job(client).json()["job_id"]
    chen = next(c for c in _certificates(client, job_id) if c["recipient_name"] == "Chen Li")

    assert client.get(f"{API}/verify/{chen['verification_code']}").json()["course_name"] == "Advanced Python"


@pytest.mark.parametrize("code", ["doesNotExist12345", "short", "has spaces in it!!", "a" * 200])
def test_verify_unknown_or_malformed_code_returns_404(client, code):
    response = client.get(f"{API}/verify/{code}")

    assert response.status_code == 404
    assert response.json() == {"valid": False, "message": "Certificate not found"}


def test_verify_certificate_not_yet_generated_is_not_found(make_client):
    client = make_client(lambda storage, settings: NoopRunner())
    job_id = create_job(client).json()["job_id"]
    code = _certificates(client, job_id)[0]["verification_code"]

    assert client.get(f"{API}/verify/{code}").status_code == 404


def test_revoked_certificate_is_not_valid(client):
    job_id = create_job(client).json()["job_id"]
    code = _certificates(client, job_id)[0]["verification_code"]
    with SessionLocal() as db:
        db.execute(update(Certificate).where(Certificate.verification_code == code).values(validity=Validity.REVOKED))
        db.commit()

    assert client.get(f"{API}/verify/{code}").status_code == 404


# ---------- HTML page ----------


def test_verify_page_shows_valid_certificate(client):
    job_id = create_job(client).json()["job_id"]
    code = _certificates(client, job_id)[0]["verification_code"]

    response = client.get(f"{API}/verify/{code}/page")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "VALID CERTIFICATE" in response.text
    assert "Asha Rao" in response.text and "Intro to Python" in response.text
    assert 'name="viewport"' in response.text  # mobile friendly
    assert "asha@example.com" not in response.text


def test_verify_page_for_unknown_code_says_not_found(client):
    response = client.get(f"{API}/verify/doesNotExist12345/page")

    assert response.status_code == 404
    assert "CERTIFICATE NOT FOUND" in response.text
    assert "POSSIBLY FAKE" in response.text.upper()


def test_verify_page_escapes_user_text(client):
    recipients = [{"name": "<script>alert(1)</script>", "email": "x@example.com"}]
    job_id = create_job(client, make_payload(recipients=recipients)).json()["job_id"]
    code = _certificates(client, job_id)[0]["verification_code"]

    page = client.get(f"{API}/verify/{code}/page").text

    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


# ---------- Rate limiting ----------


def test_verify_endpoints_are_rate_limited(settings, make_client):
    settings.verify_rate_limit_per_minute = 3
    client = make_client()

    statuses = [client.get(f"{API}/verify/doesNotExist12345").status_code for _ in range(4)]

    assert statuses == [404, 404, 404, 429]
    limited = client.get(f"{API}/verify/doesNotExist12345/page")
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) >= 1


# ---------- QR code in the PDF ----------


def test_generated_pdf_contains_qr_linking_to_verify_page(client, settings):
    job_id = create_job(client).json()["job_id"]
    cert = _certificates(client, job_id)[0]

    pdf = client.get(cert["download_url"]).content

    assert _pdf_links(pdf) == [cert["verification_url"]]
    text_on_page = PdfReader(io.BytesIO(pdf)).pages[0].extract_text()
    assert "Scan to verify" in text_on_page
    assert cert["verification_code"] in text_on_page


@pytest.mark.parametrize("template_id", list(TEMPLATES))
def test_every_builtin_template_draws_the_qr(tmp_path, template_id):
    url = verification_url("https://certs.example.org", "AbCdEfGhIjKlMnOp")
    data = CertificateData(
        title="Certificate of Completion",
        recipient_name="Asha Rao",
        course_name="Intro to Python",
        issuer_name="Acme Academy",
        issue_date=date(2026, 1, 15),
        certificate_id="cert-1",
        verification_code="AbCdEfGhIjKlMnOp",
        verification_url=url,
    )
    output = tmp_path / "cert.pdf"

    generate_certificate_pdf(data, output, template_id)

    assert _pdf_links(output.read_bytes()) == ["https://certs.example.org/api/v1/verify/AbCdEfGhIjKlMnOp/page"]
    assert "Scan to verify" in PdfReader(str(output)).pages[0].extract_text()


def test_qr_uses_public_base_url_setting(settings, make_client):
    settings.public_base_url = "https://certs.acme.example/"
    client = make_client()
    job_id = create_job(client).json()["job_id"]
    cert = _certificates(client, job_id)[0]

    links = _pdf_links(client.get(cert["download_url"]).content)

    assert links == [f"https://certs.acme.example/api/v1/verify/{cert['verification_code']}/page"]


def test_template_preview_has_sample_qr(client):
    pdf = client.get(f"{API}/templates/classic/preview").content

    assert _pdf_links(pdf) == ["http://127.0.0.1:8000/api/v1/verify/SAMPLE-PREVIEW/page"]


# ---------- Migration ----------


def test_startup_migration_adds_columns_and_backfills_codes(client, settings):
    job_id = create_job(client).json()["job_id"]
    # Turn the database back into the pre-verification schema.
    with SessionLocal() as db:
        db.execute(text("DROP INDEX ix_certificates_verification_code"))
        db.execute(text("ALTER TABLE certificates DROP COLUMN verification_code"))
        db.execute(text("ALTER TABLE certificates DROP COLUMN validity"))
        db.commit()

    init_db(settings.database_url)  # what startup does

    with SessionLocal() as db:
        certs = db.scalars(select(Certificate).where(Certificate.job_id == job_id)).all()
        assert len(certs) == 3
        assert all(c.verification_code and c.validity == Validity.VALID for c in certs)
        assert len({c.verification_code for c in certs}) == 3
    code = certs[0].verification_code
    assert client.get(f"{API}/verify/{code}").status_code == 200
