from sqlalchemy import select

from app.database import SessionLocal
from app.models import Certificate, CertificateStatus, Job
from tests.conftest import API, NoopRunner, create_job


def test_create_job_returns_202_with_job_id_and_counts(client):
    response = create_job(client)

    assert response.status_code == 202
    body = response.json()
    assert body["job_id"]
    assert body["total"] == 3
    assert body["accepted"] == 3
    assert body["invalid"] == 0
    assert body["invalid_recipients"] == []
    assert body["status_url"].endswith(f"{API}/jobs/{body['job_id']}")


def test_create_job_persists_job_and_one_row_per_recipient(make_client):
    runner = NoopRunner()
    client = make_client(lambda storage, settings: runner)

    body = create_job(client).json()

    with SessionLocal() as db:
        job = db.get(Job, body["job_id"])
        assert job is not None
        assert job.course_name == "Intro to Python"
        certs = db.scalars(select(Certificate).where(Certificate.job_id == job.id).order_by(Certificate.row_index)).all()
        assert [c.recipient_email for c in certs] == ["asha@example.com", "ben@example.com", "chen@example.com"]
        assert all(c.status == CertificateStatus.PENDING for c in certs)
        assert certs[2].course_name == "Advanced Python"  # per-recipient override kept
    assert runner.submitted == [body["job_id"]]  # handed to the background runner

