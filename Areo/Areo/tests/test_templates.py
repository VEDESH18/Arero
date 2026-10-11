from datetime import date

import pytest
from pypdf import PdfReader

from app.database import SessionLocal
from app.models import Job
from app.services import worker
from app.services.certificate_generator import TEMPLATES, CertificateData, generate_certificate_pdf
from tests.conftest import API, create_job, make_payload


def test_list_templates(client):
    response = client.get(f"{API}/templates")

    assert response.status_code == 200
    templates = response.json()
    assert [t["id"] for t in templates] == [
        "classic", "modern", "minimal", "elegant", "corporate", "achievement", "midnight", "academic",
    ]
    assert all(t["name"] and t["description"] for t in templates)
    assert templates[0]["preview_url"].endswith(f"{API}/templates/classic/preview")


@pytest.mark.parametrize("template_id", list(TEMPLATES))
def test_preview_each_template(client, template_id):
    response = client.get(f"{API}/templates/{template_id}/preview")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline")
    assert response.content.startswith(b"%PDF")


def test_preview_unknown_template_returns_404(client):
    assert client.get(f"{API}/templates/fancy/preview").status_code == 404


@pytest.mark.parametrize("template_id", list(TEMPLATES))
def test_every_template_renders_recipient_details_and_survives_long_text(tmp_path, template_id):
    data = CertificateData(
        title="Certificate of Completion",
        recipient_name="Asha Rao",
        course_name="Intro to Python",
        issuer_name="Acme Academy",
        issue_date=date(2026, 1, 15),
        certificate_id="cert-1",
    )
    normal, long = tmp_path / "normal.pdf", tmp_path / "long.pdf"

    generate_certificate_pdf(data, normal, template_id)
    generate_certificate_pdf(
        CertificateData(**{**data.__dict__, "recipient_name": "Maximilian " * 30, "issuer_name": "Academy " * 20}),
        long,
        template_id,
    )

    text = PdfReader(str(normal)).pages[0].extract_text()
    assert "Asha Rao" in text and "Intro to Python" in text
    assert long.read_bytes().startswith(b"%PDF")


def test_job_defaults_to_classic_template(client):
    job_id = create_job(client).json()["job_id"]
    assert client.get(f"{API}/jobs/{job_id}").json()["template"] == "classic"


def test_job_uses_selected_template(client, monkeypatch):
    used = []
    real = worker.generate_certificate_pdf

    def spy(data, output_path, template_id="classic"):
        used.append(template_id)
        real(data, output_path, template_id)

    monkeypatch.setattr(worker, "generate_certificate_pdf", spy)

    job_id = create_job(client, make_payload(template="modern")).json()["job_id"]

    assert client.get(f"{API}/jobs/{job_id}").json()["template"] == "modern"
    assert used == ["modern", "modern", "modern"]
    with SessionLocal() as db:
        assert db.get(Job, job_id).template_id == "modern"


def test_unknown_template_is_rejected(client):
    response = create_job(client, make_payload(template="fancy"))

    assert response.status_code == 422
    assert "unknown template" in response.text
