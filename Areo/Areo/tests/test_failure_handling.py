from datetime import datetime, timezone

from sqlalchemy import update

from app.database import SessionLocal
from app.models import Certificate, CertificateStatus, Job, JobStatus
from app.services import certificate_generator, worker
from tests.conftest import API, NoopRunner, create_job, make_payload

real_generate = certificate_generator.generate_certificate_pdf


def _fail_for(name: str):
    def fake_generate(data, output_path, template_id="classic"):
        if data.recipient_name == name:
            raise RuntimeError("font rendering exploded")
        real_generate(data, output_path, template_id)

    return fake_generate


def test_one_failure_does_not_stop_other_certificates(client, settings, monkeypatch):
    monkeypatch.setattr(worker, "generate_certificate_pdf", _fail_for("Ben Carter"))

    job_id = create_job(client).json()["job_id"]

    job = client.get(f"{API}/jobs/{job_id}").json()
    assert job["status"] == "COMPLETED_WITH_ERRORS"
    assert job["counts"]["succeeded"] == 2
    assert job["counts"]["failed"] == 1
    # The job status itself names the failed recipient and the reason.
    assert [(f["recipient_name"], f["status"]) for f in job["failed_certificates"]] == [("Ben Carter", "FAILED")]
    assert "font rendering exploded" in job["failed_certificates"][0]["error"]

    failed = client.get(f"{API}/jobs/{job_id}/certificates", params={"status": "FAILED"}).json()["items"]
    assert len(failed) == 1
    assert failed[0]["recipient_name"] == "Ben Carter"
    assert "font rendering exploded" in failed[0]["error"]
    assert failed[0]["download_url"] is None

    # Recipients after the failing one were still generated.
    succeeded = client.get(f"{API}/jobs/{job_id}/certificates", params={"status": "SUCCEEDED"}).json()["items"]
    assert {c["recipient_name"] for c in succeeded} == {"Asha Rao", "Chen Li"}
    # No half-written file is left behind for the failed one.
    assert not (settings.storage_dir / job_id / f"{failed[0]['id']}.pdf").exists()
    assert not list((settings.storage_dir / job_id).glob("*.tmp"))


def test_job_is_failed_when_every_certificate_fails(client, monkeypatch):
    def always_fail(data, output_path, template_id="classic"):
        raise OSError("disk full")

    monkeypatch.setattr(worker, "generate_certificate_pdf", always_fail)

    job_id = create_job(client).json()["job_id"]

    job = client.get(f"{API}/jobs/{job_id}").json()
    assert job["status"] == "FAILED"
    assert job["counts"]["failed"] == 3


def test_retry_regenerates_only_failed_certificates(client, monkeypatch):
    monkeypatch.setattr(worker, "generate_certificate_pdf", _fail_for("Ben Carter"))
    job_id = create_job(client).json()["job_id"]
    before = {c["id"]: c for c in client.get(f"{API}/jobs/{job_id}/certificates").json()["items"]}

    monkeypatch.setattr(worker, "generate_certificate_pdf", real_generate)  # the problem is fixed
    response = client.post(f"{API}/jobs/{job_id}/retry")

    assert response.status_code == 200
    assert response.json()["requeued"] == 1
    assert client.get(f"{API}/jobs/{job_id}").json()["status"] == "COMPLETED"
    after = {c["id"]: c for c in client.get(f"{API}/jobs/{job_id}/certificates").json()["items"]}
    for cert_id, cert in after.items():
        expected_attempts = 2 if cert["recipient_name"] == "Ben Carter" else 1
        assert cert["attempts"] == expected_attempts
        if cert["recipient_name"] != "Ben Carter":
            assert cert["generated_at"] == before[cert_id]["generated_at"]  # not regenerated


def test_retry_is_rejected_while_job_is_running(make_client):
    client = make_client(lambda storage, settings: NoopRunner())
    job_id = create_job(client).json()["job_id"]

    response = client.post(f"{API}/jobs/{job_id}/retry")

    assert response.status_code == 409


def test_startup_recovers_job_interrupted_mid_processing(make_client):
    # First "process": accept the job but never run it, then simulate a crash
    # half-way: one certificate done, one stuck in PROCESSING, one PENDING.
    first = make_client(lambda storage, settings: NoopRunner())
    job_id = create_job(first, make_payload()).json()["job_id"]
    with SessionLocal() as db:
        certs = db.query(Certificate).filter_by(job_id=job_id).order_by(Certificate.row_index).all()
        certs[0].status = CertificateStatus.SUCCEEDED
        certs[0].generated_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        certs[1].status = CertificateStatus.PROCESSING
        db.execute(update(Job).where(Job.id == job_id).values(status=JobStatus.PROCESSING))
        db.commit()
    first.__exit__(None, None, None)

    # Second "process" starts with the real (sync) runner and resumes the job.
    second = make_client()

    job = second.get(f"{API}/jobs/{job_id}").json()
    assert job["status"] == "COMPLETED"
    items = second.get(f"{API}/jobs/{job_id}/certificates").json()["items"]
    assert items[0]["attempts"] == 0  # already SUCCEEDED before the crash: not regenerated
    assert items[1]["attempts"] == 1
    assert items[2]["attempts"] == 1
