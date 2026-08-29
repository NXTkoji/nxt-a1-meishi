"""
Tests for the stale scan-session sweep.

Sessions only clean themselves up on a successful confirm, so abandoned scans
accumulated forever — 165 stuck sessions holding 238 MB of temp images by the
time this was written.
"""
import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.config import settings
from app.db.engine import AsyncSessionLocal
from app.db.models import ScanSession, ScanSessionImage
from app.services.session_janitor import sweep_stale_sessions


@pytest.fixture
def db_with_sessions(client_with_test_db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    settings.temp_path.mkdir(parents=True, exist_ok=True)
    return client_with_test_db


def _make_session(db, ext_id: str, status: str, age_days: int) -> ScanSession:
    session = ScanSession(
        external_id=ext_id,
        status=status,
        created_at=datetime.utcnow() - timedelta(days=age_days),
    )
    db.add(session)
    return session


def _temp_dir(ext_id: str):
    d = settings.temp_path / ext_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "img.jpg").write_bytes(b"not-really-a-jpeg")
    return d


def _sweep(session_maker, **kw) -> dict:
    async def _run():
        async with session_maker() as db:
            return await sweep_stale_sessions(db, **kw)
    return asyncio.run(_run())


def test_sweep_abandons_old_sessions_and_reclaims_their_images(db_with_sessions):
    for ext, status, age in [
        ("old-uploading", "uploading", 45),
        ("old-grouping", "grouping", 31),
        ("old-analyzing", "analyzing", 400),
    ]:
        _temp_dir(ext)

    async def _seed():
        async with db_with_sessions.session_maker() as db:
            for ext, status, age in [
                ("old-uploading", "uploading", 45),
                ("old-grouping", "grouping", 31),
                ("old-analyzing", "analyzing", 400),
            ]:
                _make_session(db, ext, status, age)
            await db.commit()
    asyncio.run(_seed())

    result = _sweep(db_with_sessions.session_maker, max_age_days=30)
    assert result["abandoned"] == 3

    async def _statuses():
        async with db_with_sessions.session_maker() as db:
            return dict((await db.execute(
                select(ScanSession.external_id, ScanSession.status))).all())

    assert set(asyncio.run(_statuses()).values()) == {"abandoned"}
    assert not list(settings.temp_path.iterdir())


def test_sweep_leaves_recent_and_finished_sessions_alone(db_with_sessions):
    for ext in ("recent", "finished-old"):
        _temp_dir(ext)

    async def _seed():
        async with db_with_sessions.session_maker() as db:
            _make_session(db, "recent", "grouping", 3)       # inside the window
            _make_session(db, "finished-old", "done", 500)   # terminal already
            await db.commit()
    asyncio.run(_seed())

    result = _sweep(db_with_sessions.session_maker, max_age_days=30)
    assert result["abandoned"] == 0

    async def _statuses():
        async with db_with_sessions.session_maker() as db:
            return dict((await db.execute(
                select(ScanSession.external_id, ScanSession.status))).all())

    assert asyncio.run(_statuses()) == {"recent": "grouping", "finished-old": "done"}
    # A recent session's images must survive; a done session's dir is not ours
    # to judge here — neither is a stray, both have rows.
    assert (settings.temp_path / "recent" / "img.jpg").exists()
    assert (settings.temp_path / "finished-old" / "img.jpg").exists()


def test_sweep_removes_temp_dirs_with_no_session_row(db_with_sessions):
    _temp_dir("no-session-row-at-all")
    _temp_dir("has-a-row")

    async def _seed():
        async with db_with_sessions.session_maker() as db:
            _make_session(db, "has-a-row", "grouping", 1)
            await db.commit()
    asyncio.run(_seed())

    result = _sweep(db_with_sessions.session_maker, max_age_days=30)
    assert result["stray_dirs_removed"] == 1
    assert not (settings.temp_path / "no-session-row-at-all").exists()
    assert (settings.temp_path / "has-a-row").exists()


def test_sweep_keeps_images_when_the_status_commit_fails(db_with_sessions, monkeypatch):
    """Images must not be destroyed for a session still marked active."""
    _temp_dir("old-uploading")

    async def _seed():
        async with db_with_sessions.session_maker() as db:
            _make_session(db, "old-uploading", "uploading", 90)
            await db.commit()
    asyncio.run(_seed())

    async def _boom(self, *a, **kw):
        raise RuntimeError("simulated commit failure")

    monkeypatch.setattr("sqlalchemy.ext.asyncio.AsyncSession.commit", _boom)

    with pytest.raises(RuntimeError):
        _sweep(db_with_sessions.session_maker, max_age_days=30)

    assert (settings.temp_path / "old-uploading" / "img.jpg").exists()
