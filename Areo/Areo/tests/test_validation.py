import pytest

from tests.conftest import API, create_job, make_payload


def test_empty_recipient_list_is_rejected(client):
    response = create_job(client, make_payload(recipients=[]))
    assert response.status_code == 422


def test_too_many_recipients_is_rejected(client):
    # The test settings cap jobs at 50 recipients.
    recipients = [{"name": f"Person {i}", "email": f"p{i}@example.com"} for i in range(51)]
    response = create_job(client, make_payload(recipients=recipients))

    assert response.status_code == 422
    assert "at most 50" in response.json()["detail"]


@pytest.mark.parametrize(
    "certificate_change",
    [
        {"course_name": ""},
        {"issuer_name": None},
        {"issue_date": "not-a-date"},
        {"issue_date": "2999-01-01"},
    ],
)
def test_invalid_certificate_block_is_rejected(client, certificate_change):
    response = create_job(client, make_payload(**certificate_change))
    assert response.status_code == 422


def test_missing_certificate_block_is_rejected(client):
    response = client.post(f"{API}/jobs", json={"recipients": [{"name": "A", "email": "a@example.com"}]})
    assert response.status_code == 422


def test_invalid_recipients_are_reported_and_valid_ones_accepted(client):
    recipients = [
        {"name": "Asha Rao", "email": "asha@example.com"},
        {"name": "   ", "email": "blank@example.com"},  # blank name
        {"name": "Bad Email", "email": "not-an-email"},  # bad email
        {"name": "Dup", "email": "ASHA@example.com"},  # duplicate (case-insensitive)
        {"name": "Future", "email": "f@example.com", "completion_date": "2999-01-01"},
        "just a string",  # not an object
        {"name": "Ben Carter", "email": "ben@example.com"},
    ]

    response = create_job(client, make_payload(recipients=recipients))

    assert response.status_code == 202
    body = response.json()
    assert body["total"] == 7
    assert body["accepted"] == 2
    assert body["invalid"] == 5
    errors = {row["row_index"]: " ".join(row["errors"]) for row in body["invalid_recipients"]}
    assert set(errors) == {1, 2, 3, 4, 5}
    assert "name" in errors[1]
    assert "email" in errors[2]
    assert "duplicate" in errors[3]
    assert "future" in errors[4]
    assert "object" in errors[5]

    # Invalid rows are stored too, so the job status shows them.
    listing = client.get(f"{API}/jobs/{body['job_id']}/certificates", params={"status": "INVALID"}).json()
    assert listing["total"] == 5
    assert all(item["error"] for item in listing["items"])


def test_all_recipients_invalid_returns_422_and_creates_nothing(client):
    recipients = [{"name": "", "email": "x"}, {"email": "y@example.com"}]

    response = create_job(client, make_payload(recipients=recipients))

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert [row["row_index"] for row in detail["invalid_recipients"]] == [0, 1]
