"""Opt-in MySQL test mode: positive identification of a DISPOSABLE test database.

The test suite normally runs on in-memory SQLite. Setting WFAI_MYSQL_TEST_URL runs it against MySQL instead, but
only after every check below passes; otherwise the run stops before any test executes. This module never creates,
migrates, resets or drops anything: the operator prepares the database explicitly (see docs/DEPLOYMENT_READINESS.md,
"MySQL integration tests").

Required:
- WFAI_MYSQL_TEST_URL: mysql+pymysql://user:password@host:port/<database>, where <database> matches
  wfai_test_<suffix> or wfai_disposable_<suffix>. The host must be loopback unless WFAI_MYSQL_TEST_ALLOW_REMOTE=1.
- WFAI_MYSQL_TEST_MARKER: at least 16 characters, equal to the token in the database's marker table:
      CREATE TABLE wfai_disposable_marker (purpose VARCHAR(40) NOT NULL, token VARCHAR(64) NOT NULL);
      INSERT INTO wfai_disposable_marker VALUES ('disposable-test', '<the same token>');
- The schema already at the Alembic head (`alembic upgrade head`, run by the operator).
- No rows in `users`: a database holding accounts is never treated as disposable.

Isolation: each test runs inside one outer transaction that is rolled back (the application's commits become
savepoint releases), so tests leave no rows behind. Tests marked `mysql_commits` commit through separate connections
and delete exactly the rows they created.
"""

import hmac
import os
import re
from pathlib import Path

from sqlalchemy import Engine, create_engine, inspect, make_url, text

URL_ENV, MARKER_ENV, REMOTE_ENV = "WFAI_MYSQL_TEST_URL", "WFAI_MYSQL_TEST_MARKER", "WFAI_MYSQL_TEST_ALLOW_REMOTE"
DATABASE_PATTERN = re.compile(r"^wfai_(test|disposable)_[a-z0-9_]{1,40}$")
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
MARKER_TABLE, MARKER_PURPOSE = "wfai_disposable_marker", "disposable-test"


class NotDisposableError(Exception):
    """The configured database could not be positively identified as a disposable test database."""


def enabled() -> bool:
    return bool(os.environ.get(URL_ENV))


def alembic_head() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    return ScriptDirectory.from_config(config).get_current_head()


def verified_engine() -> Engine:
    """Return an engine for the configured database, or raise NotDisposableError. Read-only checks only."""
    url = make_url(os.environ[URL_ENV])
    if url.drivername != "mysql+pymysql":
        raise NotDisposableError(f"{URL_ENV} must use the mysql+pymysql driver")
    if not url.database or not DATABASE_PATTERN.fullmatch(url.database):
        raise NotDisposableError("database name must match wfai_test_<suffix> or wfai_disposable_<suffix>")
    if url.host not in LOOPBACK and os.environ.get(REMOTE_ENV) != "1":
        raise NotDisposableError(f"non-loopback host refused (set {REMOTE_ENV}=1 to allow)")
    marker = os.environ.get(MARKER_ENV, "")
    if len(marker) < 16:
        raise NotDisposableError(f"{MARKER_ENV} must be set (at least 16 characters)")

    from app.core.database import ISOLATION_LEVEL  # test with the isolation level the application runs with

    engine = create_engine(url, isolation_level=ISOLATION_LEVEL, pool_size=10, max_overflow=10, pool_pre_ping=True,
                           hide_parameters=True)
    try:
        with engine.connect() as conn:
            tables = set(inspect(conn).get_table_names())
            if MARKER_TABLE not in tables:
                raise NotDisposableError(f"marker table {MARKER_TABLE} not found")
            rows = conn.execute(text(f"SELECT purpose, token FROM {MARKER_TABLE}")).all()
            if len(rows) != 1 or rows[0][0] != MARKER_PURPOSE or not hmac.compare_digest(rows[0][1], marker):
                raise NotDisposableError("marker row does not match")
            if "alembic_version" not in tables:
                raise NotDisposableError("schema not migrated: run `alembic upgrade head` explicitly first")
            current = conn.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
            if current != [alembic_head()]:
                raise NotDisposableError("schema is not at the Alembic head: run `alembic upgrade head` explicitly")
            if conn.execute(text("SELECT COUNT(*) FROM users")).scalar_one():
                raise NotDisposableError("the users table is not empty; refusing a database that holds accounts")
    except NotDisposableError:
        engine.dispose()
        raise
    except Exception as exc:  # connection or permission problems: report the type only (no URL or credentials)
        engine.dispose()
        raise NotDisposableError(f"could not verify the database ({type(exc).__name__})") from None
    return engine
