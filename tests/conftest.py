"""Shared fixtures for backend tests.

IMPORTANT: `client_with_test_db` uses `TestClient(app)` *without* the
`with` context-manager form. Entering it as a context manager triggers
FastAPI's lifespan handler, which runs Alembic migrations against the
real production database configured in `.env` (`~/.nxt-a1/meishi.db`).
Using it as a plain object skips lifespan entirely, so tests never touch
production data — everything runs against a throwaway temp SQLite file.
"""
import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Base
from app.db.session import get_db
from app.main import app


@pytest.fixture
def client_with_test_db(tmp_path):
    db_path = tmp_path / "test.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _create_schema():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create_schema())

    async def _override_get_db():
        async with session_maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    app.dependency_overrides[get_db] = _override_get_db
    test_client = TestClient(app)
    test_client.session_maker = session_maker
    # The engine is exposed alongside the session maker so tests that need to hook
    # SQLAlchemy events (see the query_counter fixture) do not have to reach into
    # async_sessionmaker internals — `session_maker.kw["bind"]` is private.
    test_client.engine = engine

    yield test_client

    app.dependency_overrides.pop(get_db, None)
    asyncio.run(engine.dispose())


@pytest.fixture
def query_counter():
    """Count the SQL statements a block of code actually sends to the test database.

    Usage:
        result, n = query_counter(client, lambda: client.get("/api/v2/cards"))

    Hooks SQLAlchemy's `before_cursor_execute` on the test engine, so it counts real
    statements reaching SQLite rather than ORM calls — an N+1 hidden behind a lazy
    relationship still shows up.

    Lives here rather than in one test module because every collection endpoint needs
    the same guard, and importing a module-private helper across test modules is worse
    than a fixture.
    """
    def _count(client, fn):
        # client.engine is set by client_with_test_db above.
        engine = client.engine
        counter = {"n": 0}

        def _on_execute(conn, cursor, statement, parameters, context, executemany):
            counter["n"] += 1

        # The async engine wraps a sync engine; events live on the sync one.
        event.listen(engine.sync_engine, "before_cursor_execute", _on_execute)
        try:
            result = fn()
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", _on_execute)
        return result, counter["n"]

    return _count
