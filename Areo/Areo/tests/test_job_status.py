from tests.conftest import API, NoopRunner, create_job, make_payload


def test_completed_job_status_and_progress(client):
    job_id = create_job(client).json()["job_id"]

    body = client.get(f"{API}/jobs/{job_id}").json()

    assert body["status"] == "COMPLETED"
    assert body["counts"] == {"total": 3, "pending": 0, "processing": 0, "succeeded": 3, "failed": 0, "invalid": 0}
    assert body["progress_percent"] == 100.0
    assert body["failed_certificates"] == []
    assert body["started_at"] and body["completed_at"]


def test_job_with_invalid_rows_is_completed_with_errors(client):
    recipients = [
        {"name": "Asha", "email": "asha@example.com"},
        {"name": "Ben", "email": "ben@example.com"},
        {"name": "Bad", "email": "nope"},
    ]
    job_id = create_job(client, make_payload(recipients=recipients)).json()["job_id"]

    body = client.get(f"{API}/jobs/{job_id}").json()

    assert body["status"] == "COMPLETED_WITH_ERRORS"
    assert body["counts"]["succeeded"] == 2
    assert body["counts"]["invalid"] == 1
    assert body["progress_percent"] == 100.0
    [failure] = body["failed_certificates"]
    assert failure["row_index"] == 2
    assert failure["status"] == "INVALID"
    assert "email" in failure["error"]


def test_pending_job_reports_zero_progress(make_client):
    client = make_client(lambda storage, settings: NoopRunner())
    job_id = create_job(client).json()["job_id"]

    body = client.get(f"{API}/jobs/{job_id}").json()

    assert body["status"] == "PENDING"
    assert body["counts"]["pending"] == 3
    assert body["progress_percent"] == 0.0
    assert body["started_at"] is None


def test_partial_progress_counts_invalid_rows_as_processed(make_client):
    client = make_client(lambda storage, settings: NoopRunner())
    recipients = [{"name": "A", "email": "a@example.com"}, {"name": "", "email": "b@example.com"}]
    job_id = create_job(client, make_payload(recipients=recipients)).json()["job_id"]

    body = client.get(f"{API}/jobs/{job_id}").json()

    assert body["progress_percent"] == 50.0


def test_unknown_job_returns_404(client):
    response = client.get(f"{API}/jobs/does-not-exist")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


def test_background_thread_runner_completes_job(make_client):
    """End-to-end with the real ThreadPoolRunner (not the sync test runner)."""
    import time

    from app.main import default_runner_factory

    client = make_client(default_runner_factory)
    job_id = create_job(client).json()["job_id"]

    deadline = time.monotonic() + 15
    status = None
    while time.monotonic() < deadline:
        status = client.get(f"{API}/jobs/{job_id}").json()["status"]
        if status == "COMPLETED":
            break
        time.sleep(0.05)
    assert status == "COMPLETED"


def test_list_jobs_newest_first_with_counts(client):
    first = create_job(client).json()["job_id"]
    second = create_job(
        client, make_payload(recipients=[{"name": "A", "email": "a@example.com"}, {"name": "", "email": "x"}])
    ).json()["job_id"]

    body = client.get(f"{API}/jobs").json()

    assert body["total"] == 2
    assert [j["job_id"] for j in body["items"]] == [second, first]
    newest = body["items"][0]
    assert newest["status"] == "COMPLETED_WITH_ERRORS"
    assert newest["counts"]["succeeded"] == 1 and newest["counts"]["invalid"] == 1
    assert newest["progress_percent"] == 100.0
    assert newest["template"] == "classic"
    assert newest["status_url"].endswith(f"/jobs/{second}")


def test_list_jobs_filter_and_pagination(client):
    ids = [create_job(client).json()["job_id"] for _ in range(3)]
    create_job(client, make_payload(recipients=[{"name": "A", "email": "a@example.com"}, {"name": "", "email": "x"}]))

    completed = client.get(f"{API}/jobs", params={"status": "COMPLETED"}).json()
    page2 = client.get(f"{API}/jobs", params={"status": "COMPLETED", "page": 2, "page_size": 2}).json()

    assert completed["total"] == 3
    assert {j["job_id"] for j in completed["items"]} == set(ids)
    assert len(page2["items"]) == 1
    assert client.get(f"{API}/jobs", params={"status": "NOPE"}).status_code == 422


def test_list_jobs_when_empty(client):
    assert client.get(f"{API}/jobs").json() == {"items": [], "page": 1, "page_size": 20, "total": 0}
