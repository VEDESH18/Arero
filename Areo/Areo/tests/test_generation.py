from datetime import date

import pytest
from pypdf import PdfReader

from app.services.certificate_generator import CertificateData, generate_certificate_pdf


def _data(**overrides) -> CertificateData:
    values = dict(
        title="Certificate of Completion",
        recipient_name="Asha Rao",
        course_name="Intro to Python",
        issuer_name="Acme Academy",
        issue_date=date(2026, 1, 15),
        certificate_id="cert-1234",
    )
    values.update(overrides)
    return CertificateData(**values)


def _text(path) -> str:
    return PdfReader(str(path)).pages[0].extract_text()


def test_generates_pdf_with_recipient_details(tmp_path):
    output = tmp_path / "cert.pdf"

    generate_certificate_pdf(_data(completion_date=date(2026, 1, 10)), output)

    assert output.read_bytes().startswith(b"%PDF")
    text = _text(output)
    for expected in ("Asha Rao", "Intro to Python", "Acme Academy", "cert-1234", "15 January 2026", "10 January 2026"):
        assert expected in text


def test_very_long_name_does_not_crash(tmp_path):
    output = tmp_path / "long.pdf"

    generate_certificate_pdf(_data(recipient_name="Maximilian " * 40), output)

    assert output.read_bytes().startswith(b"%PDF")
    assert "Maximilian" in _text(output)


def test_empty_name_raises(tmp_path):
    with pytest.raises(ValueError):
        generate_certificate_pdf(_data(recipient_name="  "), tmp_path / "x.pdf")


def test_job_generates_one_pdf_per_valid_recipient(client, settings):
    from tests.conftest import API, create_job

    job_id = create_job(client).json()["job_id"]

    items = client.get(f"{API}/jobs/{job_id}/certificates").json()["items"]
    assert len(items) == 3
    pdfs = list((settings.storage_dir / job_id).glob("*.pdf"))
    assert len(pdfs) == 3
    assert not list((settings.storage_dir / job_id).glob("*.tmp"))  # temp files cleaned up
    # Per-recipient course override ends up on that recipient's certificate.
    chen = next(i for i in items if i["recipient_name"] == "Chen Li")
    assert "Advanced Python" in _text(settings.storage_dir / job_id / f"{chen['id']}.pdf")
