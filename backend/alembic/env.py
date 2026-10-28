import logging
from logging.config import fileConfig

from alembic import context

import app.models  # noqa: F401  (registers every model on Base.metadata)
from app.models.base import Base, UTCDateTime

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

logger = logging.getLogger("alembic.env")

target_metadata = Base.metadata


def render_item(type_: str, obj: object, autogen_context) -> str | bool:
    # UTCDateTime is a Python-side wrapper over DATETIME (DATETIME(6) on MySQL); migrations should not import app code
    # for it.
    if type_ == "type" and isinstance(obj, UTCDateTime):
        autogen_context.imports.add("from sqlalchemy.dialects import mysql")
        return "sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql')"
    return False


def run_migrations_offline() -> None:
    """Emit MySQL DDL as SQL text. Needs no URL, credentials, or connection."""
    context.configure(
        dialect_name="mysql",
        target_metadata=target_metadata,
        literal_binds=True,
        render_item=render_item,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations using the application's engine and backend/.env settings."""
    from app.core.config import get_settings
    from app.core.database import get_engine

    settings = get_settings()
    # Host and database only: never log the user name, password, or full URL.
    logger.info("Target database: %s:%s/%s", settings.db_host, settings.db_port, settings.db_name)

    with get_engine().connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_item=render_item,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
