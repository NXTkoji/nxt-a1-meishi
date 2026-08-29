"""
Regression tests for the confirm → card_sides write.

Background: staged scan_session_images carry no uniqueness guarantee on
(temp_card_id, side_order), but card_sides has uq_card_side_order. The frontend
used to hand out the next side_order as `images.length`, which collides whenever
a group's orders are not the contiguous run 0..n-1 (e.g. after swapping
front/back, or after moving an image out of a two-sided group). Confirm then
died with a bare 500, and — because the destination file is {side_order}.jpg —
the second side had already overwritten the first on disk.
"""
import asyncio
import io

import pytest
from PIL import Image

from app.config import settings
from app.db.models import Card, CardSide, ScanSession, ScanSessionImage

SID = "11111111-2222-3333-4444-555555555555"
TEMP_CARD = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _jpeg(color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (600, 360), color).save(buf, format="JPEG", quality=90)
    return buf.getvalue()


@pytest.fixture
def staged_session(client_with_test_db, tmp_path, monkeypatch):
    """
    A session holding one two-sided card whose staged images BOTH sit at
    side_order 1 — the exact shape that used to 500.
    """
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "api_key", "")
    # Don't reach out to Google People in the post-response background task.
    monkeypatch.setattr("app.routers.v2.sessions.auto_sync_card", lambda card_id: None)

    temp_dir = settings.temp_path / SID
    temp_dir.mkdir(parents=True)
    (temp_dir / "front.jpg").write_bytes(_jpeg((220, 30, 30)))
    (temp_dir / "back.jpg").write_bytes(_jpeg((30, 30, 220)))

    async def _seed():
        async with client_with_test_db.session_maker() as db:
            session = ScanSession(external_id=SID, status="review")
            db.add(session)
            await db.flush()
            for name in ("front.jpg", "back.jpg"):
                db.add(ScanSessionImage(
                    session_id=session.id,
                    image_path=f"{SID}/{name}",
                    image_filename=name,
                    temp_card_id=TEMP_CARD,
                    side_order=1,          # ← the collision
                ))
            await db.commit()

    asyncio.run(_seed())
    return client_with_test_db


def _draft() -> dict:
    return {
        "cards": [{
            "temp_card_id": TEMP_CARD,
            "parsed": {"names": [{"language": "en", "full_name": {"value": "Side Order Test"}}]},
        }]
    }


def test_confirm_renumbers_duplicate_side_orders(staged_session):
    """Duplicate staged side_orders must be renumbered, not rejected."""
    res = staged_session.post(f"/api/v2/sessions/{SID}/confirm", json=_draft())
    assert res.status_code == 200, res.text

    async def _read():
        async with staged_session.session_maker() as db:
            from sqlalchemy import select
            rows = (await db.execute(
                select(CardSide.side_order, CardSide.image_path, CardSide.image_hash)
                .order_by(CardSide.side_order)
            )).all()
            return rows

    rows = asyncio.run(_read())
    assert [r[0] for r in rows] == [0, 1]
    # Distinct source images must survive as distinct files, not one overwrite.
    assert len({r[2] for r in rows}) == 2
    for _, rel_path, _ in rows:
        assert (settings.images_path / rel_path).exists()


def test_confirm_writes_no_images_when_the_transaction_fails(staged_session, monkeypatch):
    """A failed confirm must leave no orphaned files in permanent storage."""
    async def _boom(db, session, draft, pending_images):
        raise RuntimeError("simulated failure after staging")

    monkeypatch.setattr("app.routers.v2.sessions._confirm_one_card", _boom)

    with pytest.raises(RuntimeError):
        staged_session.post(f"/api/v2/sessions/{SID}/confirm", json=_draft())

    assert not list(settings.images_path.rglob("*.jpg"))

    async def _count_cards():
        async with staged_session.session_maker() as db:
            from sqlalchemy import func, select
            return await db.scalar(select(func.count()).select_from(Card))

    assert asyncio.run(_count_cards()) == 0
