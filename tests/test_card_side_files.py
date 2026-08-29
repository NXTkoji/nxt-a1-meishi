"""
Regression tests for permanent-image lifecycle on the card-side endpoints.

Nothing in the app used to delete a permanent image, and both side endpoints
touched the filesystem before their row was committed. That leaked files two
ways: delete_card_side removed the row but left the .jpg forever (add_card_side
hands out max(side_order)+1, so a freed slot is never reused), and a failed
insert in add_card_side stranded the file it had already written.
"""
import asyncio
import io

import pytest
from PIL import Image
from sqlalchemy import select

from app.config import settings
from app.db.models import Card, CardSide, Person

CARD_EXT = "cccccccc-dddd-eeee-ffff-000000000000"


def _jpeg(color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (600, 360), color).save(buf, format="JPEG", quality=90)
    return buf.getvalue()


@pytest.fixture
def card_with_two_sides(client_with_test_db, tmp_path, monkeypatch):
    """A committed card holding two sides, with both images on disk."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "api_key", "")
    monkeypatch.setattr("app.routers.v2.cards.auto_sync_card", lambda card_id: None)

    async def _seed():
        async with client_with_test_db.session_maker() as db:
            person = Person(external_id="person-for-side-tests")
            db.add(person)
            await db.flush()
            card = Card(external_id=CARD_EXT, person_id=person.id)
            db.add(card)
            await db.flush()
            for order, color in ((0, (200, 20, 20)), (1, (20, 20, 200))):
                prepared = image_store_prepare(order, color)
                db.add(CardSide(
                    card_id=card.id,
                    side_order=order,
                    image_path=prepared.relative_path,
                    image_filename=prepared.filename,
                    image_hash=prepared.sha256,
                    width_px=prepared.width_px,
                    height_px=prepared.height_px,
                ))
                from app.services import image_store
                image_store.write_prepared(prepared)
            await db.commit()

    def image_store_prepare(order, color):
        from app.services import image_store
        return image_store.prepare_permanent_bytes(CARD_EXT, order, _jpeg(color))

    asyncio.run(_seed())
    return client_with_test_db


def _files() -> set[str]:
    return {str(p.relative_to(settings.images_path)) for p in settings.images_path.rglob("*.jpg")}


def test_delete_side_removes_its_file(card_with_two_sides):
    """Deleting a side must not leave the image behind forever."""
    assert _files() == {f"{CARD_EXT}/0.jpg", f"{CARD_EXT}/1.jpg"}

    res = card_with_two_sides.delete(f"/api/v2/cards/{CARD_EXT}/sides/1")
    assert res.status_code == 204, res.text

    assert _files() == {f"{CARD_EXT}/0.jpg"}


def test_delete_only_side_keeps_the_file(card_with_two_sides):
    """The guard against deleting a card's last side must not delete its file."""
    assert card_with_two_sides.delete(f"/api/v2/cards/{CARD_EXT}/sides/1").status_code == 204
    res = card_with_two_sides.delete(f"/api/v2/cards/{CARD_EXT}/sides/0")
    assert res.status_code == 400
    assert _files() == {f"{CARD_EXT}/0.jpg"}


def test_add_side_writes_no_file_when_the_insert_fails(card_with_two_sides, monkeypatch):
    """A failed side insert must not strand the image it was about to store."""
    before = _files()

    async def _boom(self, *a, **kw):
        raise RuntimeError("simulated insert failure")

    monkeypatch.setattr("sqlalchemy.ext.asyncio.AsyncSession.commit", _boom)

    with pytest.raises(RuntimeError):
        card_with_two_sides.post(
            f"/api/v2/cards/{CARD_EXT}/sides",
            files={"file": ("new.jpg", _jpeg((20, 200, 20)), "image/jpeg")},
        )

    assert _files() == before


def test_add_side_stores_the_file_on_success(card_with_two_sides):
    res = card_with_two_sides.post(
        f"/api/v2/cards/{CARD_EXT}/sides",
        files={"file": ("new.jpg", _jpeg((20, 200, 20)), "image/jpeg")},
    )
    assert res.status_code == 201, res.text
    assert res.json()["side_order"] == 2
    assert _files() == {f"{CARD_EXT}/0.jpg", f"{CARD_EXT}/1.jpg", f"{CARD_EXT}/2.jpg"}

    async def _rows():
        async with card_with_two_sides.session_maker() as db:
            return (await db.execute(select(CardSide.side_order).order_by(CardSide.side_order))).scalars().all()

    assert asyncio.run(_rows()) == [0, 1, 2]


# ── promote ────────────────────────────────────────────────────────────────
# The store is laid out as {card_ext_id}/{side_order}.jpg. Promote used to
# renumber rows without renaming files, so image_filename stopped matching
# side_order and the name no longer meant what it looked like.

def _sides(client) -> list[tuple[int, str, str]]:
    async def _read():
        async with client.session_maker() as db:
            return (await db.execute(
                select(CardSide.side_order, CardSide.image_path, CardSide.image_hash)
                .order_by(CardSide.side_order)
            )).all()
    return [tuple(r) for r in asyncio.run(_read())]


def test_promote_renames_files_to_match_side_order(card_with_two_sides):
    before = {order: sha for order, _, sha in _sides(card_with_two_sides)}

    res = card_with_two_sides.post(f"/api/v2/cards/{CARD_EXT}/sides/1/promote")
    assert res.status_code == 204, res.text

    after = _sides(card_with_two_sides)
    # Every row's path must match its side_order...
    assert [(o, p) for o, p, _ in after] == [
        (0, f"{CARD_EXT}/0.jpg"), (1, f"{CARD_EXT}/1.jpg")
    ]
    # ...and the file set is unchanged, with no leftovers.
    assert _files() == {f"{CARD_EXT}/0.jpg", f"{CARD_EXT}/1.jpg"}
    # The promoted image really moved: side 0 now holds what side 1 held.
    assert {o: sha for o, _, sha in after}[0] == before[1]
    assert {o: sha for o, _, sha in after}[1] == before[0]


def test_promote_moves_pixels_not_just_names(card_with_two_sides):
    """The bytes on disk must follow the row, not stay put under a new name."""
    from app.services import image_store
    before_front = image_store.read_permanent_image(f"{CARD_EXT}/0.jpg")

    card_with_two_sides.post(f"/api/v2/cards/{CARD_EXT}/sides/1/promote")

    # What used to be the front is now side 1, byte for byte.
    assert image_store.read_permanent_image(f"{CARD_EXT}/1.jpg") == before_front


def test_promote_with_a_gap_leaves_no_stale_file(card_with_two_sides):
    """After a delete the orders are not contiguous; the freed name must go."""
    card_with_two_sides.post(
        f"/api/v2/cards/{CARD_EXT}/sides",
        files={"file": ("third.jpg", _jpeg((20, 200, 20)), "image/jpeg")},
    )
    assert _files() == {f"{CARD_EXT}/{n}.jpg" for n in (0, 1, 2)}

    # Delete the middle side → orders {0, 2}
    assert card_with_two_sides.delete(f"/api/v2/cards/{CARD_EXT}/sides/1").status_code == 204
    assert _files() == {f"{CARD_EXT}/0.jpg", f"{CARD_EXT}/2.jpg"}

    # Promote side 2 → orders {0, 1}; 2.jpg is freed and must not linger.
    assert card_with_two_sides.post(f"/api/v2/cards/{CARD_EXT}/sides/2/promote").status_code == 204
    assert [(o, p) for o, p, _ in _sides(card_with_two_sides)] == [
        (0, f"{CARD_EXT}/0.jpg"), (1, f"{CARD_EXT}/1.jpg")
    ]
    assert _files() == {f"{CARD_EXT}/0.jpg", f"{CARD_EXT}/1.jpg"}


def test_promoting_the_front_is_a_no_op(card_with_two_sides):
    before = _sides(card_with_two_sides)
    assert card_with_two_sides.post(f"/api/v2/cards/{CARD_EXT}/sides/0/promote").status_code == 204
    assert _sides(card_with_two_sides) == before
    assert _files() == {f"{CARD_EXT}/0.jpg", f"{CARD_EXT}/1.jpg"}
