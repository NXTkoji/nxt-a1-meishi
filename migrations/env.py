from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Import metadata so Alembic can autogenerate
from app.db.models import Base
from app.config import settings

config = context.config

# Override sqlalchemy.url from app settings
# Ensure data directories exist (Alembic doesn't import engine.py side effects)
settings.data_dir.mkdir(parents=True, exist_ok=True)

config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    # disable_existing_loggers=False is essential: migrations run inside the
    # uvicorn process at startup, and the default (True) would silence every
    # logger already created — uvicorn.error, uvicorn.access and all app.*
    # loggers. That is why 500 tracebacks never reached the log file.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

# Indexes created by migration a3b4c5d6e7f8 and declared there ONLY — never on the
# models, so that the DDL has a single source of truth (see that migration's header).
# The cost of that choice lands here: autogenerate diffs the live database against
# Base.metadata, so every one of those indexes looks like an index the models no
# longer want, and it emits drop_index for each one it can reflect.
#
# Measured before this hook existed: a plain `alembic revision --autogenerate` against
# the live schema produced seven drop_index calls — the whole set below except
# ix_cards_filing_date, which escaped only because SQLAlchemy cannot reflect
# expression-based indexes and skips it with a SAWarning. "Silently drops seven of
# eight" is far harder to spot than a clean all-or-nothing failure, hence this hook.
#
# Keyed on the exact names rather than a prefix or a naming rule, on purpose: a future
# index that IS declared on a model must still autogenerate normally, so anything not
# in this literal set is left entirely to Alembic. Adding another migration-only index
# means adding its name here as well.
_MIGRATION_ONLY_INDEXES = frozenset(
    {
        "ix_cards_person_id",
        "ix_cards_occasion_id",
        "ix_cards_deleted_at",
        "ix_person_names_person_current",
        "ix_contact_details_person_type",
        "ix_positions_person_id",
        "ix_card_sync_history_card_id",
        # Not reflectable today (it is an expression index), so autogenerate never
        # sees it at all. Listed anyway so this set stays the complete inventory and
        # a future SQLAlchemy that can reflect it does not start proposing the drop.
        "ix_cards_filing_date",
    }
)


def include_object(object_, name, type_, reflected, compare_to):
    """Keep the migration-only indexes out of autogenerate, in both directions.

    Returning False means Alembic neither drops the reflected index nor re-proposes
    it as something to create. Every other object is left to the normal comparison.
    """
    if type_ == "index" and name in _MIGRATION_ONLY_INDEXES:
        return False
    return True


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
