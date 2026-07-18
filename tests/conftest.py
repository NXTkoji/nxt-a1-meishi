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

    yield test_client

    app.dependency_overrides.pop(get_db, None)
    asyncio.run(engine.dispose())
