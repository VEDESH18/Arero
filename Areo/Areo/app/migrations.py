"""Small, idempotent startup migrations (no Alembic in this project).

`Base.metadata.create_all` creates missing *tables* but never alters existing
ones, so columns added after a database was first created are added here.
Every step checks the live schema first, so running it on every startup is
safe, and the SQL used (ADD COLUMN, CREATE UNIQUE INDEX IF NOT EXISTS) works on
both SQLite and PostgreSQL.
"""

import logging

from sqlalchemy import inspect, select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.models import Certificate, new_verification_code

logger = logging.getLogger(__name__)

# (table, column, DDL type) for columns added after the first release.
ADDED_COLUMNS = [
    ("certificates", "verification_code", "VARCHAR(32)"),
    ("certificates", "validity", "VARCHAR(16) NOT NULL DEFAULT 'VALID'"),
]

BACKFILL_BATCH = 500


def run_migrations(engine: Engine) -> None:
    _add_missing_columns(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_certificates_verification_code "
                "ON certificates (verification_code)"
            )
        )
    _backfill_verification_codes(engine)


def _add_missing_columns(engine: Engine) -> None:
    inspector = inspect(engine)
    for table, column, ddl in ADDED_COLUMNS:
        existing = {c["name"] for c in inspector.get_columns(table)}
        if column in existing:
            continue
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
        logger.info("Migration: added column %s.%s", table, column)


def _backfill_verification_codes(engine: Engine) -> None:
    """Give certificates created before the verification feature their own code."""
    total = 0
    with Session(engine) as db:
        while True:
            ids = db.scalars(
                select(Certificate.id).where(Certificate.verification_code.is_(None)).limit(BACKFILL_BATCH)
            ).all()
            if not ids:
                break
            for certificate_id in ids:
                db.execute(
                    update(Certificate)
                    .where(Certificate.id == certificate_id)
                    .values(verification_code=new_verification_code())
                )
            db.commit()
            total += len(ids)
    if total:
        logger.info("Migration: backfilled verification codes for %d certificate(s)", total)
