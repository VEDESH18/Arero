import io
from datetime import date

import pytest
from PIL import Image
from pypdf import PdfReader

from app.services import worker
from app.services.certificate_generator import TEMPLATES, CertificateData, generate_certificate_pdf
from app.services.templates.custom import CustomTemplateSpec
from tests.conftest import API, create_job, make_payload

real_generate = worker.generate_certificate_pdf


def image_bytes(fmt: str = "PNG", size: tuple[int, int] = (300, 100), color: str = "#1F3B6F", noise: bool = False) -> bytes:
    image = Image.new("RGB", size, color)
    if noise:
        image = Image.frombytes("RGB", size, bytes((i * 7919) % 251 for i in range(size[0] * size[1] * 3)))
    buffer = io.BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()


def form(**overrides) -> dict:
    return {
        "name": "Acme Blue",
        "primary_color": "#1F3B6F",
        "secondary_color": "#C9A227",
        "font": "Times-Roman",
        "title_text": "Certificate of Excellence",
        "body_text": "This certifies that {{name}} completed {{course}} with {{issuer}} on {{date}}.",
        "border_style": "double",
        "layout": "landscape",
        "signer_name": "Dr. Maya Singh",
        "signer_title": "Head of Training",
        **overrides,
    }


def upload(client, data: dict | None = None, logo=("logo.png", None, "image/png"), signature=None):
    name, content, content_type = logo
    files = {"logo": (name, image_bytes() if content is None else content, content_type)}
    if signature is not None:
        files["signature"] = signature
    return client.post(f"{API}/templates", data=data or form(), files=files)


def create_template(client, **kwargs) -> dict:
    response = upload(client, **kwargs)
    assert response.status_code == 201, response.text
    return response.json()


def _pdf_text(pdf: bytes) -> str:
    return PdfReader(io.BytesIO(pdf)).pages[0].extract_text()


# ---------- Upload ----------


def test_upload_custom_template_with_valid_image(client, settings):
    body = create_template(client, signature=("sig.jpg", image_bytes("JPEG", (200, 60)), "image/jpeg"))

    assert body["type"] == "custom"
    assert body["name"] == "Acme Blue"
    assert body["primary_color"] == "#1F3B6F"
    assert body["font"] == "Times-Roman"
    assert body["has_logo"] is True and body["has_signature"] is True
    assert body["owner_id"] is None
    stored = sorted(p.name for p in (settings.upload_dir).iterdir())
    assert len(stored) == 2
    assert all(len(name) == 36 and name.endswith(".png") for name in stored)  # random names, never "logo.png"


def test_list_includes_builtin_and_custom_templates(client):
    custom = create_template(client)

    templates = client.get(f"{API}/templates").json()

    assert [t["type"] for t in templates] == ["builtin"] * len(TEMPLATES) + ["custom"]
    assert templates[-1]["id"] == custom["id"]
    assert templates[-1]["preview_url"].endswith(f"/templates/{custom['id']}/preview")


def test_get_template_details(client):
    custom = create_template(client)

    assert client.get(f"{API}/templates/{custom['id']}").json()["body_text"] == form()["body_text"]
    builtin = client.get(f"{API}/templates/classic").json()
    assert builtin["type"] == "builtin" and builtin["primary_color"] is None
    assert client.get(f"{API}/templates/nope").status_code == 404


def test_short_hex_colours_are_normalised(client):
    body = create_template(client, data=form(primary_color="#abc"))
    assert body["primary_color"] == "#AABBCC"


def test_fake_image_renamed_to_png_is_rejected(client, settings):
    response = upload(client, logo=("logo.png", b"<?php echo 'not an image'; ?>" * 10, "image/png"))

    assert response.status_code == 422
    assert "not a valid PNG or JPEG" in response.json()["detail"]
    assert not settings.upload_dir.exists() or not any(settings.upload_dir.iterdir())


def test_real_image_in_another_format_is_rejected(client):
    response = upload(client, logo=("logo.png", image_bytes("GIF"), "image/png"))

    assert response.status_code == 422
    assert "only PNG or JPEG" in response.json()["detail"]


def test_oversize_file_is_rejected(settings, make_client):
    settings.max_upload_bytes = 5_000
    client = make_client()

    response = upload(client, logo=("logo.png", image_bytes(size=(400, 400), noise=True), "image/png"))

    assert response.status_code == 413


def test_image_with_too_many_pixels_is_rejected(settings, make_client):
    settings.max_image_dimension = 200
    client = make_client()

    response = upload(client, logo=("logo.png", image_bytes(size=(300, 100)), "image/png"))

    assert response.status_code == 422
    assert "maximum is 200px" in response.json()["detail"]


def test_large_image_is_downscaled(settings, make_client):
    settings.image_downscale_to = 50
    client = make_client()

    create_template(client)

    [stored] = list(settings.upload_dir.iterdir())
    with Image.open(stored) as image:
        assert max(image.size) == 50 and image.format == "PNG"


def test_signature_is_validated_too_and_nothing_is_left_behind(client, settings):
    response = upload(client, signature=("sig.png", b"GIF89a fake", "image/png"))

    assert response.status_code == 422
    assert "signature" in response.json()["detail"]
    assert not any(settings.upload_dir.iterdir())  # the already-saved logo was removed again


def test_user_file_name_is_never_used_on_disk(client, settings, tmp_path):
    create_template(client, logo=("../../evil.png", None, "image/png"))

    assert not (tmp_path / "evil.png").exists()
    assert all(p.parent == settings.upload_dir for p in settings.upload_dir.iterdir())


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("primary_color", "blue", "should match pattern"),
        ("secondary_color", "#12345", "should match pattern"),
        ("font", "Comic Sans", "Helvetica"),
        ("body_text", "Hello {{nmae}}, well done", "unknown placeholder"),
        ("body_text", "Well done everyone", "{{name}}"),
        ("title_text", "{{name}} {{oops", "malformed placeholder"),
        ("border_style", "wavy", "none"),
        ("layout", "square", "landscape"),
    ],
)
def test_invalid_fields_are_rejected_with_clear_422(client, field, value, message):
    response = upload(client, data=form(**{field: value}))

    assert response.status_code == 422
    [error] = response.json()["detail"]
    assert error["loc"] == ["body", field]
    assert message in error["msg"]


def test_logo_is_required(client):
    response = client.post(f"{API}/templates", data=form())
    assert response.status_code == 422


# ---------- Preview and rendering ----------


def test_preview_custom_template_has_text_and_sample_qr(client):
    custom = create_template(client)

    response = client.get(f"{API}/templates/{custom['id']}/preview")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    text = _pdf_text(response.content)
    assert "Certificate of Excellence" in text
    assert "Jane Doe" in text and "Sample Course" in text
    assert "Dr. Maya Singh" in text and "Scan to verify" in text


def test_portrait_layout(client):
    custom = create_template(client, data=form(layout="portrait", border_style="none"))

    page = PdfReader(io.BytesIO(client.get(f"{API}/templates/{custom['id']}/preview").content)).pages[0]

    assert float(page.mediabox.width) < float(page.mediabox.height)


def _spec(tmp_path, **overrides) -> CustomTemplateSpec:
    logo = tmp_path / "logo.png"
    logo.write_bytes(image_bytes())
    values = dict(
        id="t1", name="T", primary_color="#1F3B6F", secondary_color="#C9A227", font="Helvetica",
        title_text="Certificate", body_text="Awarded to {{name}} for {{course}}.", border_style="simple",
        layout="landscape", logo_path=logo,
    )
    values.update(overrides)
    return CustomTemplateSpec(**values)


def _data(**overrides) -> CertificateData:
    values = dict(
        title="x", recipient_name="Asha Rao", course_name="Intro to Python", issuer_name="Acme Academy",
        issue_date=date(2026, 1, 15), certificate_id="cert-1",
    )
    values.update(overrides)
    return CertificateData(**values)


def test_long_text_is_shrunk_wrapped_or_truncated(tmp_path):
    spec = _spec(tmp_path, body_text="Before {{name}} " + "and a very long sentence " * 80, layout="portrait")
    output = tmp_path / "long.pdf"

    generate_certificate_pdf(_data(recipient_name="Maximilian " * 30, issuer_name="Academy " * 40), output, spec)

    assert output.read_bytes().startswith(b"%PDF")
    assert "Maximilian" in PdfReader(str(output)).pages[0].extract_text()


def test_user_text_is_drawn_literally(tmp_path):
    output = tmp_path / "special.pdf"

    generate_certificate_pdf(_data(recipient_name="O'Brien (Jr.) <b>\\x"), output, _spec(tmp_path))

    assert "O'Brien (Jr.) <b>\\x" in PdfReader(str(output)).pages[0].extract_text()


# ---------- Jobs with custom templates ----------


def test_job_created_with_custom_template(client, settings):
    custom = create_template(client)

    created = create_job(client, make_payload(template=custom["id"]))

    assert created.status_code == 202
    job_id = created.json()["job_id"]
    job = client.get(f"{API}/jobs/{job_id}").json()
    assert job["status"] == "COMPLETED"
    assert job["template"] == custom["id"]
    cert = client.get(f"{API}/jobs/{job_id}/certificates").json()["items"][0]
    text = _pdf_text(client.get(cert["download_url"]).content)
    assert "Certificate of Excellence" in text
    assert "Asha Rao" in text and "Intro to Python" in text and "Acme Academy" in text
    assert cert["verification_code"] in text


def test_builtin_template_names_still_work(client):
    job_id = create_job(client, make_payload(template="elegant")).json()["job_id"]
    assert client.get(f"{API}/jobs/{job_id}").json()["status"] == "COMPLETED"


def test_unknown_custom_template_id_is_rejected(client):
    response = create_job(client, make_payload(template="3f2b8c1e-0000-4000-8000-000000000000"))

    assert response.status_code == 422
    assert "unknown template" in response.text


def test_missing_logo_fails_only_the_affected_certificates(client, settings, monkeypatch):
    custom = create_template(client)
    calls = []

    def lose_logo_after_first(data, output_path, template_id="classic"):
        real_generate(data, output_path, template_id)
        calls.append(data.recipient_name)
        if len(calls) == 1:
            for stored in settings.upload_dir.iterdir():
                stored.unlink()

    monkeypatch.setattr(worker, "generate_certificate_pdf", lose_logo_after_first)

    job_id = create_job(client, make_payload(template=custom["id"])).json()["job_id"]

    job = client.get(f"{API}/jobs/{job_id}").json()
    assert job["status"] == "COMPLETED_WITH_ERRORS"
    assert job["counts"]["succeeded"] == 1 and job["counts"]["failed"] == 2
    assert all("Logo image for template 'Acme Blue' is missing" in f["error"] for f in job["failed_certificates"])


# ---------- Delete ----------


def test_template_in_use_cannot_be_deleted(client):
    custom = create_template(client)
    create_job(client, make_payload(template=custom["id"]))

    response = client.delete(f"{API}/templates/{custom['id']}")

    assert response.status_code == 409
    assert "used by 1 job" in response.json()["detail"]
    assert client.get(f"{API}/templates/{custom['id']}").status_code == 200


def test_unused_template_is_deleted_with_its_images(client, settings):
    custom = create_template(client, signature=("sig.png", image_bytes(), "image/png"))

    assert client.delete(f"{API}/templates/{custom['id']}").status_code == 204

    assert client.get(f"{API}/templates/{custom['id']}").status_code == 404
    assert not any(settings.upload_dir.iterdir())
    assert client.delete(f"{API}/templates/{custom['id']}").status_code == 404


def test_builtin_template_cannot_be_deleted(client):
    assert client.delete(f"{API}/templates/classic").status_code == 409
