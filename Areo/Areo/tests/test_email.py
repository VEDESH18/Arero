from sqlalchemy import update

from app.database import SessionLocal
from app.models import Certificate, EmailStatus
from app.services.mailer import SmtpMailer
from tests.conftest import API, NoopRunner, create_job, make_payload


def _email(client, job_id, **params):
    return client.post(f"{API}/jobs/{job_id}/email", params=params)


def test_each_recipient_gets_their_own_certificate(client, fake_mailer):
    job_id = create_job(client).json()["job_id"]

    response = _email(client, job_id)

    assert response.status_code == 202
    assert response.json()["queued"] == 3
    assert sorted(m["To"] for m in fake_mailer.sent) == ["asha@example.com", "ben@example.com", "chen@example.com"]
    asha = next(m for m in fake_mailer.sent if m["To"] == "asha@example.com")
    assert asha["Subject"] == "Your certificate: Intro to Python"
    assert "Hi Asha Rao" in asha.get_body(("plain",)).get_content()
    [attachment] = list(asha.iter_attachments())
    assert attachment.get_content_type() == "application/pdf"
    assert attachment.get_filename() == "Certificate_Asha_Rao.pdf"
    assert attachment.get_content().startswith(b"%PDF")

    job = client.get(f"{API}/jobs/{job_id}").json()
    assert job["emails"] == {"not_requested": 0, "queued": 0, "sending": 0, "sent": 3, "failed": 0}
    cert = client.get(f"{API}/jobs/{job_id}/certificates").json()["items"][0]
    assert cert["email_status"] == "SENT" and cert["emailed_at"]


def test_invalid_recipients_are_never_emailed(client, fake_mailer):
    recipients = [{"name": "Asha Rao", "email": "asha@example.com"}, {"name": "", "email": "bad"}]
    job_id = create_job(client, make_payload(recipients=recipients)).json()["job_id"]

    assert _email(client, job_id).json()["queued"] == 1
    assert [m["To"] for m in fake_mailer.sent] == ["asha@example.com"]


def test_one_failed_email_does_not_stop_the_others(client, fake_mailer):
    fake_mailer.fail_for = {"ben@example.com"}
    job_id = create_job(client).json()["job_id"]

    _email(client, job_id)

    assert sorted(m["To"] for m in fake_mailer.sent) == ["asha@example.com", "chen@example.com"]
    emails = client.get(f"{API}/jobs/{job_id}").json()["emails"]
    assert emails["sent"] == 2 and emails["failed"] == 1
    [failed] = client.get(f"{API}/jobs/{job_id}/certificates", params={"email_status": "FAILED"}).json()["items"]
    assert failed["recipient_email"] == "ben@example.com"
    assert "550 mailbox unavailable" in failed["email_error"]


def test_calling_again_only_sends_to_those_who_did_not_get_it(client, fake_mailer):
    fake_mailer.fail_for = {"ben@example.com"}
    job_id = create_job(client).json()["job_id"]
    _email(client, job_id)
    fake_mailer.sent.clear()
    fake_mailer.fail_for = set()  # the mailbox problem is fixed

    second = _email(client, job_id).json()

    assert second["queued"] == 1 and second["already_sent"] == 2
    assert [m["To"] for m in fake_mailer.sent] == ["ben@example.com"]
    assert client.get(f"{API}/jobs/{job_id}").json()["emails"]["sent"] == 3


def test_resend_emails_everyone_again(client, fake_mailer):
    job_id = create_job(client).json()["job_id"]
    _email(client, job_id)
    fake_mailer.sent.clear()

    assert _email(client, job_id).json()["queued"] == 0
    assert _email(client, job_id, resend="true").json()["queued"] == 3
    assert len(fake_mailer.sent) == 3


def test_email_rejected_while_job_is_still_running(make_client):
    client = make_client(lambda storage, settings: NoopRunner())
    job_id = create_job(client).json()["job_id"]

    response = _email(client, job_id)

    assert response.status_code == 409
    assert "still generating" in response.json()["detail"]


def test_email_unknown_job_returns_404(client):
    assert _email(client, "nope").status_code == 404


def test_email_returns_503_when_smtp_not_configured(client):
    job_id = create_job(client).json()["job_id"]
    client.app.state.settings = client.app.state.settings.model_copy(update={"smtp_host": None})

    response = _email(client, job_id)

    assert response.status_code == 503
    assert "SMTP_HOST" in response.json()["detail"]


def test_queued_emails_resume_after_restart(make_client, fake_mailer):
    first = make_client()
    job_id = create_job(first).json()["job_id"]
    # Simulate a crash during delivery: one email mid-send, the others still queued.
    with SessionLocal() as db:
        db.execute(update(Certificate).where(Certificate.job_id == job_id).values(email_status=EmailStatus.QUEUED))
        db.execute(
            update(Certificate).where(Certificate.job_id == job_id, Certificate.row_index == 0)
            .values(email_status=EmailStatus.SENDING)
        )
        db.commit()
    first.__exit__(None, None, None)

    second = make_client()

    assert second.get(f"{API}/jobs/{job_id}").json()["emails"]["sent"] == 3
    assert len(fake_mailer.sent) == 3


class _FakeSMTP:
    """Stands in for smtplib.SMTP to check SmtpMailer's protocol steps."""

    instances: list["_FakeSMTP"] = []

    def __init__(self, host, port, timeout):
        self.calls = [("connect", host, port)]
        _FakeSMTP.instances.append(self)

    def starttls(self, context):
        self.calls.append(("starttls",))

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, message):
        self.calls.append(("send", message["To"], message["From"]))

    def quit(self):
        self.calls.append(("quit",))


def test_smtp_mailer_uses_one_connection_with_starttls_and_login(settings, monkeypatch):
    from email.message import EmailMessage

    monkeypatch.setattr("app.services.mailer.smtplib.SMTP", _FakeSMTP)
    _FakeSMTP.instances = []
    settings = settings.model_copy(update={"smtp_port": 587, "smtp_username": "user", "smtp_password": "secret"})
    settings = settings.model_validate(settings.model_dump())  # re-wrap the password as SecretStr

    with SmtpMailer(settings) as mailer:
        for to in ("a@example.com", "b@example.com"):
            message = EmailMessage()
            message["To"] = to
            mailer.send(message)

    [conn] = _FakeSMTP.instances
    assert conn.calls == [
        ("connect", "smtp.test", 587),
        ("starttls",),
        ("login", "user", "secret"),
        ("send", "a@example.com", "Acme Academy <certificates@acme.test>"),
        ("send", "b@example.com", "Acme Academy <certificates@acme.test>"),
        ("quit",),
    ]
