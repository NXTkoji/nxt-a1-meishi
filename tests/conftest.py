"""Shared fixtures for backend tests.

IMPORTANT: `client_with_test_db` uses `TestClient(app)` *without* the
`with` context-manager form. Entering it as a context manager triggers
FastAPI's lifespan handler, which runs Alembic migrations against the
real production database configured in `.env` (`~/.nxt-a1/meishi.db`).
Using it as a plain object skips lifespan entirely, so tests never touch
production data — everything runs against a throwaway temp SQLite file.

Background tasks are the other way out: TestClient runs them inline, and
contact_sync opens sessions from the live engine itself (not via get_db).
`client_with_test_db` redirects that too, and the autouse `_isolate_from_production`
fixture blanks credentials and blocks real network access for every test.
See tests/test_isolation.py.
"""
import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.db.models import Base
from app.db.session import get_db
from app.main import app
from app.services import contact_sync


@pytest.fixture(autouse=True)
def _isolate_from_production(monkeypatch):
    """Keep every test away from real services, whatever .env contains.

    - Credentials are blank, so code paths that need them skip or fail fast.
      Tests that exercise those paths set a fake value themselves.
    - Real outbound HTTP raises. Tests fake HTTP with httpx.MockTransport or
      by patching functions; neither goes through the network transports
      patched here, so they are unaffected.
    """
    for name in ("google_refresh_token", "odoo_password", "anthropic_api_key"):
        monkeypatch.setattr(settings, name, "")

    def _blocked(self, request):
        raise RuntimeError(f"Tests must not make real network calls: {request.url}")

    async def _blocked_async(self, request):
        _blocked(self, request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _blocked)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _blocked_async)


@pytest.fixture
def client_with_test_db(tmp_path, monkeypatch):
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
    # Background sync tasks open their own sessions from the live engine;
    # point them at the test DB too (monkeypatch restores it at teardown)
    monkeypatch.setattr(contact_sync, "AsyncSessionLocal", session_maker)
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


@pytest.fixture
def captured_statements(monkeypatch):
    """Collect the SQLAlchemy Core statement objects a request executes.

    Usage:
        stmts = captured_statements()
        client.get("/api/v2/cards")
        # stmts is now populated; filter it for the statement under test.

    Returns the live list, so it fills up as the request runs.

    Why objects and not SQL text: the ORDER BY tests assert on
    `statement._order_by_clauses`, which distinguishes a top-level ORDER BY from one
    buried in a subquery — something a substring match on the compiled SQL cannot do.
    They also pick their statement out by `column_descriptions[0]["entity"]`, so a
    query for Card rows is told apart from its eager-load without matching SQL text.

    Only the plumbing is shared. Each test keeps its own entity filter and its own
    assertion: that is where the reasoning about which statement matters, and why,
    actually lives.

    `monkeypatch` undoes the patch at teardown, so AsyncSession.execute is restored
    even when the test fails mid-request.
    """
    def _capture():
        captured = []
        original_execute = AsyncSession.execute

        async def _spy_execute(self, statement, *args, **kwargs):
            captured.append(statement)
            return await original_execute(self, statement, *args, **kwargs)

        monkeypatch.setattr(AsyncSession, "execute", _spy_execute)
        return captured

    return _capture
