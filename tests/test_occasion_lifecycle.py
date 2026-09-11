"""Occasion lifecycle: deleting an occasion must not lose the occasion name.

These are data-backed tests, not signature smoke tests — the delete path has
real cards riding on it (48 on one occasion in the live database).
"""
import asyncio

import pytest

from app.db.session import get_db
from app.main import app


def _seed(client, occasion_name="RI Convention", n_cards=3, label=None):
    """Create one person, one occasion, and n cards linked to it.

    Returns (occasion_id, [card_ids]).
    """
    holder = {}

    async def _run():
        from app.db.models import Card, Occasion, Person

        async for db in app.dependency_overrides[get_db]():
            person = Person(external_id="p-occ")
            db.add(person)
            await db.flush()

            occ = Occasion(name=occasion_name)
            db.add(occ)
            await db.flush()

            ids = []
            for i in range(n_cards):
                card = Card(
                    external_id=f"c-occ-{i}",
                    person_id=person.id,
                    occasion_id=occ.id,
                    occasion_label=label,
                )
                db.add(card)
                await db.flush()
                ids.append(card.id)

            holder["occasion_id"] = occ.id
            holder["card_ids"] = ids
            await db.commit()
            break

    asyncio.run(_run())
    return holder["occasion_id"], holder["card_ids"]


def _load_cards(client, card_ids):
    """Re-read cards from a fresh session so we see committed state."""
    holder = {}

    async def _run():
        from sqlalchemy import select

        from app.db.models import Card

        async for db in app.dependency_overrides[get_db]():
            rows = (await db.execute(select(Card).where(Card.id.in_(card_ids)))).scalars().all()
            holder["rows"] = [
                {"id": c.id, "occasion_id": c.occasion_id, "occasion_label": c.occasion_label}
                for c in rows
            ]
            break

    asyncio.run(_run())
    return holder["rows"]


def test_delete_stamps_label_onto_cards(client_with_test_db):
    occ_id, card_ids = _seed(client_with_test_db)

    resp = client_with_test_db.delete(f"/api/v2/occasions/{occ_id}")
    assert resp.status_code == 204

    rows = _load_cards(client_with_test_db, card_ids)
    assert len(rows) == len(card_ids)
    for row in rows:
        assert row["occasion_id"] is None
        assert row["occasion_label"] == "RI Convention"


def test_delete_does_not_delete_cards(client_with_test_db):
    occ_id, card_ids = _seed(client_with_test_db, n_cards=5)

    client_with_test_db.delete(f"/api/v2/occasions/{occ_id}")

    rows = _load_cards(client_with_test_db, card_ids)
    assert len(rows) == 5


def test_delete_preserves_existing_label(client_with_test_db):
    """A card that already carries a label from an earlier deletion is not overwritten."""
    occ_id, card_ids = _seed(
        client_with_test_db, occasion_name="Second Event", label="Original Event"
    )

    client_with_test_db.delete(f"/api/v2/occasions/{occ_id}")

    rows = _load_cards(client_with_test_db, card_ids)
    assert len(rows) == len(card_ids)
    for row in rows:
        assert row["occasion_label"] == "Original Event"


def test_list_returns_card_count(client_with_test_db):
    occ_id, _ = _seed(client_with_test_db, n_cards=4)

    resp = client_with_test_db.get("/api/v2/occasions")
    assert resp.status_code == 200

    row = next(o for o in resp.json() if o["id"] == occ_id)
    assert row["card_count"] == 4


def test_card_count_excludes_soft_deleted_cards(client_with_test_db):
    from datetime import datetime

    occ_id, card_ids = _seed(client_with_test_db, n_cards=3)

    async def _soft_delete_one():
        from app.db.models import Card

        async for db in app.dependency_overrides[get_db]():
            card = await db.get(Card, card_ids[0])
            card.deleted_at = datetime.utcnow()
            await db.commit()
            break

    asyncio.run(_soft_delete_one())

    resp = client_with_test_db.get("/api/v2/occasions")
    row = next(o for o in resp.json() if o["id"] == occ_id)
    assert row["card_count"] == 2
