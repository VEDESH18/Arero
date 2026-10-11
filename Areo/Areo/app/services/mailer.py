"""SMTP mailer.

One mailer is used per batch of emails: it opens a single SMTP connection
lazily and reuses it for every message. If a send fails, the connection is
dropped and the next message reconnects, so one bad message (or a dropped
connection) never blocks the rest of the batch.

Anything with `send(message)` and context-manager methods can stand in for
`SmtpMailer`; tests use a fake that records messages instead of sending them.
"""

import smtplib
import ssl
from email.message import EmailMessage
from typing import Protocol, Self

from app.config import Settings


class Mailer(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(self, *exc_info: object) -> None: ...

    def send(self, message: EmailMessage) -> None: ...


class EmailNotConfiguredError(RuntimeError):
    pass


class SmtpMailer:
    def __init__(self, settings: Settings) -> None:
        if not settings.email_configured:
            raise EmailNotConfiguredError("SMTP_HOST and SMTP_FROM must be set to send email")
        self._settings = settings
        self._connection: smtplib.SMTP | None = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._disconnect()

    def send(self, message: EmailMessage) -> None:
        if "From" not in message:
            message["From"] = self._settings.smtp_from
        try:
            self._connect().send_message(message)
        except Exception:
            self._disconnect()  # start fresh for the next message
            raise

    def _connect(self) -> smtplib.SMTP:
        if self._connection is not None:
            return self._connection
        s = self._settings
        context = ssl.create_default_context()
        if s.smtp_use_ssl:
            conn: smtplib.SMTP = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout, context=context)
        else:
            conn = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout)
            if s.smtp_starttls:
                conn.starttls(context=context)
        password = s.smtp_password.get_secret_value() if s.smtp_password else ""
        if s.smtp_username and password:
            conn.login(s.smtp_username, password)
        self._connection = conn
        return conn

    def _disconnect(self) -> None:
        if self._connection is None:
            return
        try:
            self._connection.quit()
        except Exception:
            pass  # already broken; nothing to clean up
        self._connection = None
