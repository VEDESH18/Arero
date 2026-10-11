import io
import threading
import zipfile

from sqlalchemy import select, update

from app.database import SessionLocal
from app.models import Certificate, CertificateStatus, Job, JobStatus
from app.services import certificate_generator, job_service, worker
from app.services.storage import LocalStorage
from tests.conftest import API, NoopRunner, create_job, make_payload

real_generate = certificate_generator.generate_certificate_pdf

NEW_RECIPIENTS = [
    {"name": "Dina Patel", "email": "dina@example.com"},
    {"name": "Eli Moreno", "email": "eli@example.com"},
]


def _patch(client, job_id, recipients):
    return client.patch(f"{API}/jobs/{job_id}/recipients", json={"recipients": recipients})


def _certificates(client, job_id):
    return client.get(f"{API}/jobs/{job_id}/certificates").json()["items"]


def test_add_recipients_to_completed_job_generates_only_the_new_ones(client):
    job_id = create_job(client).json()["job_id"]
    before = {c["id"]: c for c in _certificates(client, job_id)}

    response = _patch(client, job_id, NEW_RECIPIENTS)

    assert response.status_code == 202
    body = response.json()
    assert body["accepted"] == 2
    assert body["rejected"] == 0
    assert body["counts"]["total"] == 5
    assert body["status_url"].endswith(f"{API}/jobs/{job_id}")

    job = client.get(f"{API}/jobs/{job_id}").json()
    assert job["status"] == "COMPLETED"
    assert job["counts"]["succeeded"] == 5
    after = _certificates(client, job_id)
    assert [c["row_index"] for c in after] == [0, 1, 2, 3, 4]
    assert [c["recipient_name"] for c in after[3:]] == ["Dina Patel", "Eli Moreno"]
    for cert in after:
        assert cert["attempts"] == 1
        if cert["id"] in before:  # existing certificates were not regenerated
            assert cert["generated_at"] == before[cert["id"]]["generated_at"]


def test_duplicate_email_against_existing_recipients_is_rejected(client):
    job_id = create_job(client).json()["job_id"]

    body = _patch(
        client,
        job_id,
        [
            {"name": "Dina Patel", "email": "dina@example.com"},
            {"name": "Asha Again", "email": "ASHA@example.com"},  # already in the job (row 0)
            {"name": "Dina Twice", "email": "dina@example.com"},  # duplicate within this batch
            {"name": "", "email": "not-an-email"},
        ],
    ).json()

    assert body["accepted"] == 1
    assert body["rejected"] == 3
    errors = {r["row_index"]: r["errors"] for r in body["rejected_recipients"]}
    assert errors[1] == ["email: already a recipient of this job (row 0)"]
    assert errors[2] == ["email: duplicate of recipient at row 0"]
    assert any(e.startswith("email:") for e in errors[3])
    job = client.get(f"{API}/jobs/{job_id}").json()
    assert job["status"] == "COMPLETED_WITH_ERRORS"  # the rejected rows are reported on the job
    assert job["counts"]["succeeded"] == 4 and job["counts"]["invalid"] == 3


def test_patch_unknown_job_returns_404(client):
    assert _patch(client, "does-not-exist", NEW_RECIPIENTS).status_code == 404


def test_patch_with_only_invalid_recipients_changes_nothing(client):
    job_id = create_job(client).json()["job_id"]

    response = _patch(client, job_id, [{"name": "Asha", "email": "asha@example.com"}, {"name": "x", "email": "bad"}])

    assert response.status_code == 422
    assert len(response.json()["detail"]["invalid_recipients"]) == 2
    job = client.get(f"{API}/jobs/{job_id}").json()
    assert job["status"] == "COMPLETED"
    assert job["counts"]["total"] == 3


def test_patch_requires_at_least_one_recipient(client):
    job_id = create_job(client).json()["job_id"]
    assert _patch(client, job_id, []).status_code == 422


def test_patch_beyond_total_cap_returns_409(settings, make_client):
    settings.max_total_recipients_per_job = 4
    client = make_client()
    job_id = create_job(client).json()["job_id"]

    response = _patch(client, job_id, NEW_RECIPIENTS)

    assert response.status_code == 409
    assert "limit of 4" in response.json()["detail"]
    assert client.get(f"{API}/jobs/{job_id}").json()["counts"]["total"] == 3


def test_patch_batch_larger_than_request_cap_returns_422(client):
    job_id = create_job(client).json()["job_id"]
    too_many = [{"name": f"P{i}", "email": f"p{i}@example.com"} for i in range(51)]  # cap is 50 in tests

    assert _patch(client, job_id, too_many).status_code == 422


def test_failed_job_reopens_and_gets_the_right_final_status(client, monkeypatch):
    def always_fail(data, output_path, template_id="classic"):
        raise OSError("disk full")

    monkeypatch.setattr(worker, "generate_certificate_pdf", always_fail)
    job_id = create_job(client).json()["job_id"]
    assert client.get(f"{API}/jobs/{job_id}").json()["status"] == "FAILED"

    monkeypatch.setattr(worker, "generate_certificate_pdf", real_generate)
    _patch(client, job_id, NEW_RECIPIENTS)

    job = client.get(f"{API}/jobs/{job_id}").json()
    assert job["status"] == "COMPLETED_WITH_ERRORS"
    assert job["counts"]["succeeded"] == 2 and job["counts"]["failed"] == 3


def test_zip_numbering_continues_after_patch(client):
    job_id = create_job(client).json()["job_id"]
    _patch(client, job_id, NEW_RECIPIENTS)

    archive = zipfile.ZipFile(io.BytesIO(client.get(f"{API}/jobs/{job_id}/download").content))

    assert archive.namelist() == [
        "0001_Asha_Rao.pdf",
        "0002_Ben_Carter.pdf",
        "0003_Chen_Li.pdf",
        "0004_Dina_Patel.pdf",
        "0005_Eli_Moreno.pdf",
    ]


def test_email_after_patch_only_emails_new_recipients(client, fake_mailer):
    job_id = create_job(client).json()["job_id"]
    client.post(f"{API}/jobs/{job_id}/email")
    fake_mailer.sent.clear()

    _patch(client, job_id, NEW_RECIPIENTS)
    response = client.post(f"{API}/jobs/{job_id}/email")

    assert response.json()["queued"] == 2
    assert response.json()["already_sent"] == 3
    assert sorted(m["To"] for m in fake_mailer.sent) == ["dina@example.com", "eli@example.com"]


def test_patch_while_job_is_running_is_picked_up_by_the_running_worker(make_client, settings, monkeypatch):
    runner = NoopRunner()
    client = make_client(lambda storage, _settings: runner)
    job_id = create_job(client).json()["job_id"]

    first_started, release = threading.Event(), threading.Event()

    def slow_generate(data, output_path, template_id="classic"):
        if not first_started.is_set():
            first_started.set()
            assert release.wait(10)
        real_generate(data, output_path, template_id)

    monkeypatch.setattr(worker, "generate_certificate_pdf", slow_generate)
    thread = threading.Thread(target=worker.process_job, args=(job_id, LocalStorage(settings.storage_dir)))
    thread.start()
    assert first_started.wait(10)  # the worker is now mid-job, holding certificate 0

    response = _patch(client, job_id, NEW_RECIPIENTS)

    release.set()
    thread.join(10)
    assert not thread.is_alive()
    assert response.status_code == 202
    assert response.json()["status"] == "PROCESSING"
    assert runner.submitted == [job_id]  # not re-submitted: the running worker took the new rows
    job = client.get(f"{API}/jobs/{job_id}").json()
    assert job["status"] == "COMPLETED"
    assert job["counts"]["succeeded"] == 5
    assert all(c["attempts"] == 1 for c in _certificates(client, job_id))  # nothing processed twice


def test_recipients_added_just_before_finalize_are_not_lost(client, monkeypatch):
    """PATCH commits after the worker's last look for work but before it finalizes."""
    job_id = create_job(client, make_payload(recipients=[{"name": "Asha Rao", "email": "asha@example.com"}])).json()[
        "job_id"
    ]
    with SessionLocal() as db:  # make the job runnable again by hand
        db.execute(update(Job).where(Job.id == job_id).values(status=JobStatus.PENDING))
        db.execute(update(Certificate).where(Certificate.job_id == job_id).values(status=CertificateStatus.PENDING))
        db.commit()

    real_finalize = job_service.finalize_job
    calls = []

    def finalize_with_late_patch(db, job_id_, attempted=None):
        if not calls:
            with SessionLocal() as other:
                job_service.add_recipients(other, other.get(Job, job_id_), NEW_RECIPIENTS, 50, 5000)
        calls.append(attempted)
        return real_finalize(db, job_id_, attempted)

    monkeypatch.setattr(worker, "finalize_job", finalize_with_late_patch)
    worker.process_job(job_id, client.app.state.storage)

    assert len(calls) == 2  # first finalize saw new work and declined; second one finished
    job = client.get(f"{API}/jobs/{job_id}").json()
    assert job["status"] == "COMPLETED"
    assert job["counts"]["succeeded"] == 3


def test_crash_recovery_picks_up_recipients_added_by_patch(make_client):
    first = make_client()
    job_id = create_job(first).json()["job_id"]
    first.__exit__(None, None, None)

    # PATCH lands, but the process dies before the runner gets to it.
    second = make_client(lambda storage, settings: NoopRunner())
    assert _patch(second, job_id, NEW_RECIPIENTS).json()["status"] == "PENDING"
    second.__exit__(None, None, None)

    third = make_client()  # startup recovery re-queues the job

    job = third.get(f"{API}/jobs/{job_id}").json()
    assert job["status"] == "COMPLETED"
    assert job["counts"]["succeeded"] == 5
    with SessionLocal() as db:
        attempts = db.scalars(select(Certificate.attempts).where(Certificate.job_id == job_id)).all()
    assert sorted(attempts) == [1, 1, 1, 1, 1]
