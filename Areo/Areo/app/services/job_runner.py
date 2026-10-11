"""How background work is executed.

The API only depends on the `JobRunner` interface. Today it is an in-process
thread pool; moving to Celery/RQ would mean writing another class whose
`submit`/`submit_emails` enqueue `process_job`/`deliver_job_emails` on the
external queue.
"""

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Protocol

from app.services.email_delivery import MailerFactory, deliver_job_emails
from app.services.mailer import EmailNotConfiguredError, Mailer
from app.services.render_context import RenderContext
from app.services.storage import LocalStorage
from app.services.worker import process_job

logger = logging.getLogger(__name__)


class JobRunner(Protocol):
    def submit(self, job_id: str) -> None: ...

    def submit_emails(self, job_id: str) -> None: ...

    def shutdown(self) -> None: ...


def _email_disabled() -> Mailer:
    raise EmailNotConfiguredError("Email is not configured")


class ThreadPoolRunner:
    """Runs work on a bounded pool of background threads inside the API process."""

    def __init__(
        self,
        storage: LocalStorage,
        max_workers: int,
        mailer_factory: MailerFactory = _email_disabled,
        context: RenderContext | None = None,
    ) -> None:
        self._storage = storage
        self._mailer_factory = mailer_factory
        self._context = context or RenderContext()
        self._stop = threading.Event()
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="cert-worker")

    def submit(self, job_id: str) -> None:
        future = self._executor.submit(process_job, job_id, self._storage, self._stop.is_set, self._context)
        future.add_done_callback(_log_crash)

    def submit_emails(self, job_id: str) -> None:
        future = self._executor.submit(
            deliver_job_emails, job_id, self._storage, self._mailer_factory, self._stop.is_set
        )
        future.add_done_callback(_log_crash)

    def shutdown(self) -> None:
        # Running work stops between items; queued work is dropped. Its state
        # stays in the DB and is resumed on next startup.
        self._stop.set()
        self._executor.shutdown(wait=True, cancel_futures=True)


class SyncRunner:
    """Runs work immediately in the caller's thread. Used by tests for determinism."""

    def __init__(
        self, storage: LocalStorage, mailer_factory: MailerFactory = _email_disabled, context: RenderContext | None = None
    ) -> None:
        self._storage = storage
        self._mailer_factory = mailer_factory
        self._context = context or RenderContext()

    def submit(self, job_id: str) -> None:
        process_job(job_id, self._storage, context=self._context)

    def submit_emails(self, job_id: str) -> None:
        deliver_job_emails(job_id, self._storage, self._mailer_factory)

    def shutdown(self) -> None:
        pass


def _log_crash(future: Future) -> None:
    if not future.cancelled() and future.exception() is not None:
        logger.error("Background task crashed", exc_info=future.exception())
