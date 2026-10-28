import logging
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import BACKEND_DIR
from app.core.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("/db")
def database_health(db: Session = Depends(get_db)) -> dict[str, str]:
    try:
        db.execute(text("SELECT 1")).scalar_one()
    except SQLAlchemyError as exc:
        # Log only the exception type: driver messages can include host and user names.
        logger.warning("Database health check failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable",
        ) from None
    return {"status": "ok", "database": "reachable"}


@lru_cache
def expected_schema_revision() -> str:
    """The Alembic head shipped with this code (read from the migration scripts; no database access)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return ScriptDirectory.from_config(config).get_current_head()


@router.get("/ready", summary="Readiness: database reachable and schema at this release's migration head")
def readiness(db: Session = Depends(get_db)) -> dict[str, str]:
    """For load balancers and deploy checks. Liveness is /api/health (process only). 503 until the database answers
    and its alembic_version equals the head shipped with this code, so a release never takes traffic against an
    unmigrated (or newer) schema. Reports check names and states only: no revision ids, hosts or error text."""
    try:
        current = db.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
    except SQLAlchemyError as exc:
        db.rollback()
        try:
            db.execute(text("SELECT 1")).scalar_one()
            checks = {"database": "reachable", "schema": "not_migrated"}
        except SQLAlchemyError:
            checks = {"database": "unavailable", "schema": "unknown"}
        logger.warning("Readiness check failed: %s", type(exc).__name__)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail={"status": "not_ready", **checks}) from None
    if current != [expected_schema_revision()]:
        logger.warning("Readiness check failed: schema revision differs from this release")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail={"status": "not_ready", "database": "reachable", "schema": "revision_mismatch"})
    return {"status": "ready", "database": "reachable", "schema": "current"}
