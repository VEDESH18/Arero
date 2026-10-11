"""Test fixtures: a fresh SQLite file DB and storage dir per test, plus a
synchronous runner so jobs are finished by the time POST /jobs returns."""

from collections.abc import Callable, Iterator
from email.message import EmailMessage
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.job_runner import JobRunner, SyncRunner
from app.services.render_context import RenderContext
from app.services.storage import LocalStorage

API = "/api/v1"


class NoopRunner:
    """Accepts jobs but never runs them, to observe the PENDING state."""

    def __init__(self) -> None:
        self.submitted: list[str] = []

    def submit(self, job_id: str) -> None:
        self.submitted.append(job_id)

    def submit_emails(self, job_id: str) -> None:
        pass

    def shutdown(self) -> None:
        pass


class FakeMailer:
    """Records messages instead of sending them. Addresses in `fail_for` raise, like a rejected recipient."""

    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []
        self.fail_for: set[str] = set()

    def __enter__(self) -> "FakeMailer":
        return self

    def __exit__(self, *exc_info: object) -> None:
        pass

    def send(self, message: EmailMessage) -> None:
        if message["To"] in self.fail_for:
            raise ConnectionError(f"550 mailbox unavailable: {message['To']}")
        self.sent.append(message)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # ignore a developer's local .env
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        storage_dir=tmp_path / "storage",
        upload_dir=tmp_path / "uploads",
        max_recipients_per_job=50,
        worker_threads=2,
        smtp_host="smtp.test",
        smtp_from="Acme Academy <certificates@acme.test>",
    )


@pytest.fixture
def fake_mailer() -> FakeMailer:
    return FakeMailer()


@pytest.fixture
def make_client(settings: Settings, fake_mailer: FakeMailer) -> Iterator[Callable[..., TestClient]]:
    """Build a client with a chosen runner. Entering the client runs the lifespan."""
    clients: list[TestClient] = []

    def _make(runner_factory: Callable[[LocalStorage, Settings], JobRunner] | None = None) -> TestClient:
        factory = runner_factory or (
            lambda storage, settings: SyncRunner(
                storage, mailer_factory=lambda: fake_mailer, context=RenderContext.from_settings(settings)
            )
        )
        client = TestClient(create_app(settings, factory))
        client.__enter__()
        clients.append(client)
        return client

    yield _make
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client) -> TestClient:
    return make_client()


def make_payload(recipients: list | None = None, **certificate_overrides) -> dict:
    certificate = {
        "title": "Certificate of Completion",
        "course_name": "Intro to Python",
        "issuer_name": "Acme Academy",
        "issue_date": "2026-01-15",
        **certificate_overrides,
    }
    if recipients is None:
        recipients = [
            {"name": "Asha Rao", "email": "asha@example.com", "external_id": "S-001"},
            {"name": "Ben Carter", "email": "ben@example.com"},
            {"name": "Chen Li", "email": "chen@example.com", "course_name": "Advanced Python"},
        ]
    return {"certificate": certificate, "recipients": recipients}


def create_job(client: TestClient, payload: dict | None = None, **kwargs):
    return client.post(f"{API}/jobs", json=payload or make_payload(), **kwargs)
