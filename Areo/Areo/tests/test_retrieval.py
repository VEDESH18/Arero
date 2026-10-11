import io
import zipfile

from app.services import certificate_generator, worker
from tests.conftest import API, NoopRunner, create_job, make_payload

real_generate = certificate_generator.generate_certificate_pdf


def _many_recipients(n: int) -> list[dict]:
    return [{"name": f"Person {i}", "email": f"p{i}@example.com"} for i in range(n)]


def test_list_certificates_is_paginated(client):
    job_id = create_job(client, make_payload(recipients=_many_recipients(7))).json()["job_id"]

    page1 = client.get(f"{API}/jobs/{job_id}/certificates", params={"page": 1, "page_size": 5}).json()
    page2 = client.get(f"{API}/jobs/{job_id}/certificates", params={"page": 2, "page_size": 5}).json()

    assert page1["total"] == page2["total"] == 7
    assert [i["row_index"] for i in page1["items"]] == [0, 1, 2, 3, 4]
    assert [i["row_index"] for i in page2["items"]] == [5, 6]


def test_page_size_is_capped(client):
    job_id = create_job(client).json()["job_id"]
    response = client.get(f"{API}/jobs/{job_id}/certificates", params={"page_size": 1000})
    assert response.status_code == 422


def test_list_certificates_filters_by_status(client):
    recipients = [{"name": "A", "email": "a@example.com"}, {"name": "B", "email": "bad"}]
    job_id = create_job(client, make_payload(recipients=recipients)).json()["job_id"]

    succeeded = client.get(f"{API}/jobs/{job_id}/certificates", params={"status": "SUCCEEDED"}).json()
    invalid = client.get(f"{API}/jobs/{job_id}/certificates", params={"status": "INVALID"}).json()

    assert [i["recipient_name"] for i in succeeded["items"]] == ["A"]
    assert succeeded["items"][0]["download_url"]
    assert [i["recipient_name"] for i in invalid["items"]] == ["B"]
    assert invalid["items"][0]["download_url"] is None


def test_get_single_certificate_metadata(client):
    job_id = create_job(client).json()["job_id"]
    cert = client.get(f"{API}/jobs/{job_id}/certificates").json()["items"][0]

    response = client.get(f"{API}/certificates/{cert['id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["recipient_email"] == "asha@example.com"
    assert body["status"] == "SUCCEEDED"


def test_download_certificate_pdf(client):
    job_id = create_job(client).json()["job_id"]
    cert = client.get(f"{API}/jobs/{job_id}/certificates").json()["items"][0]

    response = client.get(cert["download_url"])

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "attachment" in response.headers["content-disposition"]
    assert "Asha_Rao" in response.headers["content-disposition"]
    assert response.content.startswith(b"%PDF")


def test_download_of_pending_certificate_returns_409(make_client):
    client = make_client(lambda storage, settings: NoopRunner())
    job_id = create_job(client).json()["job_id"]
    cert = client.get(f"{API}/jobs/{job_id}/certificates").json()["items"][0]

    response = client.get(f"{API}/certificates/{cert['id']}/download")

    assert response.status_code == 409
    assert "PENDING" in response.json()["detail"]


def test_unknown_certificate_returns_404(client):
    assert client.get(f"{API}/certificates/nope").status_code == 404
    assert client.get(f"{API}/certificates/nope/download").status_code == 404


def test_zip_contains_exactly_the_succeeded_certificates(client, monkeypatch):
    monkeypatch.setattr(worker, "generate_certificate_pdf", _fail_for("Ben Carter"))
    recipients = [
        {"name": "Asha Rao", "email": "asha@example.com"},
        {"name": "Ben Carter", "email": "ben@example.com"},  # generation fails
        {"name": "Chen Li", "email": "chen@example.com"},
        {"name": "Invalid", "email": "nope"},  # invalid input
    ]
    job_id = create_job(client, make_payload(recipients=recipients)).json()["job_id"]

    response = client.get(f"{API}/jobs/{job_id}/download")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = sorted(archive.namelist())
        assert names == ["0001_Asha_Rao.pdf", "0003_Chen_Li.pdf"]
        assert all(archive.read(n).startswith(b"%PDF") for n in names)


def test_zip_download_with_nothing_generated_returns_409(make_client):
    client = make_client(lambda storage, settings: NoopRunner())
    job_id = create_job(client).json()["job_id"]

    assert client.get(f"{API}/jobs/{job_id}/download").status_code == 409


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_swagger_ui_is_served_and_root_and_docs_redirect_to_it(client):
    swagger = client.get("/swagger")
    assert swagger.status_code == 200
    assert "swagger-ui" in swagger.text

    for path in ("/", "/docs"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"] == "/swagger"


def _fail_for(name: str):
    def fake_generate(data, output_path, template_id="classic"):
        if data.recipient_name == name:
            raise RuntimeError("boom")
        real_generate(data, output_path, template_id)

    return fake_generate


def test_swagger_docs_are_organised(client):
    spec = client.get("/openapi.json").json()

    assert [t["name"] for t in spec["tags"]] == ["1. Templates", "2. Jobs", "3. Certificates", "4. Email", "System"]
    create = spec["paths"]["/api/v1/jobs"]["post"]
    assert create["summary"] == "Submit a bulk certificate job"
    assert set(create["requestBody"]["content"]["application/json"]["examples"]) == {"basic", "template", "invalid_rows"}
    # Every endpoint has a plain-English summary.
    for path, methods in spec["paths"].items():
        for method, operation in methods.items():
            assert operation.get("summary"), f"{method.upper()} {path} has no summary"
