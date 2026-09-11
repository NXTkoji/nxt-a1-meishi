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


def test_search_matches_linked_occasion(client_with_test_db):
    """Typing an occasion name finds its cards while the link is live."""
    _seed(client_with_test_db, occasion_name="RI Convention", n_cards=3)

    resp = client_with_test_db.get("/api/v2/cards", params={"q": "Convention"})
    assert resp.status_code == 200
    assert len(resp.json()) == 3


def test_search_matches_orphaned_label(client_with_test_db):
    """The same query returns the same cards after the occasion is deleted."""
    occ_id, _ = _seed(client_with_test_db, occasion_name="RI Convention", n_cards=3)

    before = client_with_test_db.get("/api/v2/cards", params={"q": "Convention"}).json()
    assert len(before) == 3

    client_with_test_db.delete(f"/api/v2/occasions/{occ_id}")

    after = client_with_test_db.get("/api/v2/cards", params={"q": "Convention"}).json()
    assert len(after) == 3
    # CardListItem exposes `id`, not `external_id` — use the key that actually exists.
    assert {c["id"] for c in after} == {c["id"] for c in before}


def test_search_does_not_match_unrelated_occasion(client_with_test_db):
    _seed(client_with_test_db, occasion_name="RI Convention", n_cards=2)

    resp = client_with_test_db.get("/api/v2/cards", params={"q": "Rotary"})
    assert resp.json() == []


def test_search_count_and_facets_include_occasion_matches(client_with_test_db):
    """count and facets must agree with list_cards on what ?q= matches.

    _apply_card_filters is the single owner of the q= rule for list_cards,
    count_cards and card_facets — this guards the occasion branch against
    reaching only one of the three.
    """
    _seed(client_with_test_db, occasion_name="RI Convention", n_cards=3)

    count_resp = client_with_test_db.get("/api/v2/cards/count", params={"q": "Convention"})
    assert count_resp.status_code == 200
    assert count_resp.json()["total"] == 3

    facets_resp = client_with_test_db.get("/api/v2/cards/facets", params={"q": "Convention"})
    assert facets_resp.status_code == 200
    assert sum(f["count"] for f in facets_resp.json()) == 3


def test_legacy_card_falls_back_to_label(client_with_test_db):
    """The DTO fed to the Google Contacts sync survives an occasion deletion."""
    holder = {}

    async def _run():
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        from app.db.models import Card, CardMyCompany, Person
        from app.services.legacy_card import build_legacy_card

        async for db in app.dependency_overrides[get_db]():
            card = await db.scalar(
                select(Card)
                .where(Card.id == holder["card_id"])
                .options(
                    selectinload(Card.occasion),
                    selectinload(Card.my_company_links).selectinload(CardMyCompany.my_company),
                    selectinload(Card.person).selectinload(Person.names),
                    selectinload(Card.person).selectinload(Person.contact_details),
                    selectinload(Card.person).selectinload(Person.positions),
                    # build_legacy_card walks person.relationships_from even for a
                    # person with none — an empty list still needs to be loaded, or
                    # accessing the attribute triggers a lazy load outside the
                    # greenlet and raises MissingGreenlet.
                    selectinload(Card.person).selectinload(Person.relationships_from),
                )
            )
            legacy = build_legacy_card(
                card, card.person, card.person.contact_details, card.person.positions
            )
            holder["occasion_name"] = legacy.occasion_name
            break

    occ_id, card_ids = _seed(client_with_test_db, occasion_name="RI Convention", n_cards=1)
    holder["card_id"] = card_ids[0]

    client_with_test_db.delete(f"/api/v2/occasions/{occ_id}")
    asyncio.run(_run())

    assert holder["occasion_name"] == "RI Convention"
