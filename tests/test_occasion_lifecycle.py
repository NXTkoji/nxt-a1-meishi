"""Occasion lifecycle: deleting an occasion must not lose the occasion name.

These are data-backed tests, not signature smoke tests — the delete path has
real cards riding on it (48 on one occasion in the live database).
"""
import asyncio

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


def test_delete_overwrites_older_label(client_with_test_db):
    """Deleting an occasion always stamps its name, replacing any older label.

    A card carrying a stale label from an earlier deletion (here "Original Event")
    but currently linked to a live occasion ("Second Event") must end up labelled
    with the occasion that was *just* deleted, not the older label.
    """
    occ_id, card_ids = _seed(
        client_with_test_db, occasion_name="Second Event", label="Original Event"
    )

    client_with_test_db.delete(f"/api/v2/occasions/{occ_id}")

    rows = _load_cards(client_with_test_db, card_ids)
    assert len(rows) == len(card_ids)
    for row in rows:
        assert row["occasion_label"] == "Second Event"


def test_list_returns_card_count(client_with_test_db):
    occ_id, _ = _seed(client_with_test_db, n_cards=4)

    resp = client_with_test_db.get("/api/v2/occasions")
    assert resp.status_code == 200

    row = next(o for o in resp.json() if o["id"] == occ_id)
    assert row["card_count"] == 4


def test_card_count_excludes_soft_deleted_cards(client_with_test_db):
    from datetime import UTC, datetime

    occ_id, card_ids = _seed(client_with_test_db, n_cards=3)

    async def _soft_delete_one():
        from app.db.models import Card

        async for db in app.dependency_overrides[get_db]():
            card = await db.get(Card, card_ids[0])
            # deleted_at is a naive-UTC column (see Card.deleted_at / app/db/models.py),
            # so store a naive datetime here too rather than an aware one.
            card.deleted_at = datetime.now(UTC).replace(tzinfo=None)
            await db.commit()
            break

    asyncio.run(_soft_delete_one())

    resp = client_with_test_db.get("/api/v2/occasions")
    row = next(o for o in resp.json() if o["id"] == occ_id)
    assert row["card_count"] == 2


def test_patch_rename_occasion_reports_correct_card_count(client_with_test_db):
    """PATCH (rename) must not misreport card_count as 0 for an occasion that has cards."""
    occ_id, _ = _seed(client_with_test_db, n_cards=2)

    resp = client_with_test_db.patch(
        f"/api/v2/occasions/{occ_id}", json={"name": "Renamed Event"}
    )
    assert resp.status_code == 200
    assert resp.json()["card_count"] == 2


def test_card_count_zero_for_unused_occasion(client_with_test_db):
    """A brand-new occasion with no cards reports card_count == 0 everywhere."""
    resp = client_with_test_db.post("/api/v2/occasions", json={"name": "Empty Event"})
    assert resp.status_code == 201
    assert resp.json()["card_count"] == 0

    occ_id = resp.json()["id"]
    list_resp = client_with_test_db.get("/api/v2/occasions")
    row = next(o for o in list_resp.json() if o["id"] == occ_id)
    assert row["card_count"] == 0


def test_delete_unknown_occasion_returns_404(client_with_test_db):
    resp = client_with_test_db.delete("/api/v2/occasions/999999")
    assert resp.status_code == 404


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


def test_search_ignores_stale_label_on_relinked_card(client_with_test_db):
    """A card's stale label must not leak into search once it has a live occasion.

    Seed a card whose occasion_label is "Old Gala" (left over from some earlier
    deletion) but which is currently linked to a *different*, live occasion. The
    stale label must not be searchable, and the live occasion's name must be.
    """
    occ_id, card_ids = _seed(
        client_with_test_db, occasion_name="RI Convention", n_cards=1, label="Old Gala"
    )

    stale = client_with_test_db.get("/api/v2/cards", params={"q": "Gala"}).json()
    assert stale == []

    live = client_with_test_db.get("/api/v2/cards", params={"q": "Convention"}).json()
    assert len(live) == 1


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


def test_patch_occasion_id_clears_stale_label(client_with_test_db):
    """Re-linking a card to a (different) live occasion clears its stale label.

    The label names an occasion the card is no longer linked to. Keeping it would let
    it resurface in search or on the detail page if the card is later unlinked, so any
    PATCH that sets occasion_id wipes it.
    """
    occ_id, card_ids = _seed(
        client_with_test_db, occasion_name="Second Event", n_cards=1, label="Original Event"
    )
    other = client_with_test_db.post("/api/v2/occasions", json={"name": "Third Event"}).json()

    resp = client_with_test_db.patch(
        "/api/v2/cards/c-occ-0", json={"occasion_id": other["id"]}
    )
    assert resp.status_code == 200

    rows = _load_cards(client_with_test_db, card_ids)
    assert rows[0]["occasion_label"] is None
    assert rows[0]["occasion_id"] == other["id"]


def test_patch_occasion_id_null_clears_stale_label(client_with_test_db):
    """Explicitly unlinking a card (occasion_id: null) also clears its label."""
    occ_id, card_ids = _seed(
        client_with_test_db, occasion_name="Second Event", n_cards=1, label="Original Event"
    )

    resp = client_with_test_db.patch("/api/v2/cards/c-occ-0", json={"occasion_id": None})
    assert resp.status_code == 200

    rows = _load_cards(client_with_test_db, card_ids)
    assert rows[0]["occasion_label"] is None
    assert rows[0]["occasion_id"] is None


def test_patch_without_occasion_id_keeps_label(client_with_test_db):
    """A PATCH that never mentions occasion_id must not touch occasion_label."""
    occ_id, card_ids = _seed(
        client_with_test_db, occasion_name="Second Event", n_cards=1, label="Original Event"
    )

    resp = client_with_test_db.patch("/api/v2/cards/c-occ-0", json={"notes": "x"})
    assert resp.status_code == 200

    rows = _load_cards(client_with_test_db, card_ids)
    assert rows[0]["occasion_label"] == "Original Event"


def test_legacy_card_falls_back_to_label(client_with_test_db):
    """The DTO fed to the Google Contacts sync survives an occasion deletion.

    Seeding happens first so `holder["card_ext_id"]` is populated before `_run`
    (defined below it) ever executes — `_run` only runs later, inside
    `asyncio.run`, so the ordering in the file doesn't matter for correctness,
    but reads top-to-bottom in the order things actually happen.
    """
    occ_id, _ = _seed(client_with_test_db, occasion_name="RI Convention", n_cards=1)
    holder = {"card_ext_id": "c-occ-0"}

    client_with_test_db.delete(f"/api/v2/occasions/{occ_id}")

    async def _run():
        # _load_full_card already loads every relationship build_legacy_card
        # needs (occasion, person.names/contact_details/positions/relationships_from)
        # — reuse it instead of hand-copying that selectinload list here.
        from app.routers.v2.export import _load_full_card
        from app.services.legacy_card import build_legacy_card

        async for db in app.dependency_overrides[get_db]():
            card = await _load_full_card(db, holder["card_ext_id"])
            legacy = build_legacy_card(
                card, card.person, card.person.contact_details, card.person.positions
            )
            holder["occasion_name"] = legacy.occasion_name
            break

    asyncio.run(_run())

    assert holder["occasion_name"] == "RI Convention"


def test_card_detail_exposes_label_after_delete(client_with_test_db):
    """GET /cards/{ext_id} carries the stamped name, so the detail page can show it."""
    occ_id, _ = _seed(client_with_test_db, occasion_name="RI Convention", n_cards=1)
    ext_id = "c-occ-0"  # matches the external_id _seed gives card 0

    linked = client_with_test_db.get(f"/api/v2/cards/{ext_id}").json()
    assert linked["occasion_id"] == occ_id
    assert linked["occasion_label"] is None

    client_with_test_db.delete(f"/api/v2/occasions/{occ_id}")

    orphaned = client_with_test_db.get(f"/api/v2/cards/{ext_id}").json()
    assert orphaned["occasion_id"] is None
    assert orphaned["occasion_label"] == "RI Convention"


def test_legacy_card_prefers_live_occasion_over_label(client_with_test_db):
    """The other half of the resolution rule: a live link wins over an old label.

    Only reachable for rows written before PATCH started clearing the label (the API
    itself never leaves a card with both), but it is the rule legacy_card encodes.
    """
    _seed(
        client_with_test_db,
        occasion_name="Second Event",
        n_cards=1,
        label="Original Event",
    )
    holder = {"card_ext_id": "c-occ-0"}

    async def _run():
        from app.routers.v2.export import _load_full_card
        from app.services.legacy_card import build_legacy_card

        async for db in app.dependency_overrides[get_db]():
            card = await _load_full_card(db, holder["card_ext_id"])
            # Guard the premise: without both set, the assertion below proves nothing.
            assert card.occasion_id is not None
            assert card.occasion_label == "Original Event"
            legacy = build_legacy_card(
                card, card.person, card.person.contact_details, card.person.positions
            )
            holder["occasion_name"] = legacy.occasion_name
            break

    asyncio.run(_run())

    assert holder["occasion_name"] == "Second Event"
