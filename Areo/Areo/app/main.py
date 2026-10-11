"""FastAPI application factory and lifespan.

Startup: create tables, set up storage and the background runner, then resume
any jobs that were interrupted by a previous shutdown/crash.
Shutdown: stop the runner (interrupted work is resumed on next startup).
"""

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from app.api import certificates, email, jobs, templates, verify
from app.api.docs import API_DESCRIPTION, SWAGGER_UI_PARAMETERS, TAGS_METADATA
from app.config import Settings, get_settings
from app.database import SessionLocal, init_db
from app.services.job_runner import JobRunner, ThreadPoolRunner
from app.services.email_delivery import recover_queued_emails
from app.services.job_service import recover_incomplete_jobs
from app.services.images import ImageStore
from app.services.mailer import SmtpMailer
from app.services.rate_limit import RateLimiter
from app.services.render_context import RenderContext
from app.services.storage import LocalStorage

logger = logging.getLogger(__name__)

RunnerFactory = Callable[[LocalStorage, Settings], JobRunner]


def default_runner_factory(storage: LocalStorage, settings: Settings) -> JobRunner:
    return ThreadPoolRunner(
        storage,
        max_workers=settings.worker_threads,
        mailer_factory=lambda: SmtpMailer(settings),
        context=RenderContext.from_settings(settings),
    )


def create_app(settings: Settings | None = None, runner_factory: RunnerFactory | None = None) -> FastAPI:
    settings = settings or get_settings()
    runner_factory = runner_factory or default_runner_factory

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logging.basicConfig(
            level=settings.log_level.upper(),
            format="%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s",
        )
        init_db(settings.database_url)
        storage = LocalStorage(settings.storage_dir)
        runner = runner_factory(storage, settings)
        app.state.settings = settings
        app.state.storage = storage
        app.state.runner = runner
        app.state.verify_limiter = RateLimiter(settings.verify_rate_limit_per_minute)
        app.state.images = ImageStore(
            settings.upload_dir,
            max_bytes=settings.max_upload_bytes,
            max_dimension=settings.max_image_dimension,
            downscale_to=settings.image_downscale_to,
        )

        with SessionLocal() as db:
            resumed = recover_incomplete_jobs(db)
        for job_id in resumed:
            runner.submit(job_id)
        if resumed:
            logger.info("Resumed %d unfinished job(s) from a previous run", len(resumed))

        with SessionLocal() as db:
            email_jobs = recover_queued_emails(db) if settings.email_configured else []
        for job_id in email_jobs:
            runner.submit_emails(job_id)
        if email_jobs:
            logger.info("Resumed email delivery for %d job(s)", len(email_jobs))

        yield
        runner.shutdown()

    app = FastAPI(
        title="Bulk Certificate Generator",
        version="1.0.0",
        summary="Generate PDF certificates for many recipients in one request.",
        description=API_DESCRIPTION,
        openapi_tags=TAGS_METADATA,
        swagger_ui_parameters=SWAGGER_UI_PARAMETERS,
        lifespan=lifespan,
        docs_url="/swagger",
    )
    app.include_router(templates.router, prefix="/api/v1")
    app.include_router(jobs.router, prefix="/api/v1")
    app.include_router(certificates.router, prefix="/api/v1")
    app.include_router(email.router, prefix="/api/v1")
    app.include_router(verify.router, prefix="/api/v1")

    # Convenience: the bare URL and FastAPI's usual /docs both lead to Swagger UI.
    @app.get("/", include_in_schema=False)
    @app.get("/docs", include_in_schema=False)
    def to_swagger() -> RedirectResponse:
        return RedirectResponse(url="/swagger")

    @app.get("/health", tags=["System"], summary="Health check")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
