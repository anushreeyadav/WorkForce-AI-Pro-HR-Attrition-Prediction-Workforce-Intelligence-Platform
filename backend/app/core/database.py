from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

# The write paths serialize with a row lock (SELECT ... FOR UPDATE) and then read the rows they check, e.g. leave
# overlap and balance, the last-admin guard, payroll calculation. Under MySQL's default REPEATABLE READ those later
# plain reads use the snapshot taken BEFORE the lock was granted, so a second concurrent request misses the first one's
# committed rows: on MySQL 8.0 that double-booked leave and returned 500s (tests/test_mysql_concurrency.py). READ
# COMMITTED makes every statement see the latest committed data. Requires binlog_format=ROW (MySQL 8.0 default).
ISOLATION_LEVEL = "READ COMMITTED"


@lru_cache
def get_engine() -> Engine:
    """Create the engine on first use; no connection is opened until a query runs."""
    settings = get_settings()
    return create_engine(
        settings.database_url,
        isolation_level=ISOLATION_LEVEL,
        pool_size=5,
        max_overflow=5,
        pool_timeout=10,
        pool_recycle=1800,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
        hide_parameters=True,
    )


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a session that is always closed (and rolled back if uncommitted)."""
    with get_session_factory()() as session:
        yield session
