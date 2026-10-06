"""Guards that keep the test suite away from production data and services.

Regression: three occasion-lifecycle tests PATCHed a card, which scheduled
auto_sync_card as a background task. TestClient runs background tasks
inline, and contact_sync opens its own session from the LIVE engine
(~/.nxt-a1/meishi.db) — so the task loaded the production card with the
test's id (1) and, with a Google token in .env, pushed it to the real
Google Contacts account.
"""
import httpx
import pytest

from app.config import settings
from app.services import contact_sync


def test_background_sync_uses_the_test_database(client_with_test_db):
    assert contact_sync.AsyncSessionLocal is client_with_test_db.session_maker


@pytest.mark.parametrize("name", ["google_refresh_token", "odoo_password", "anthropic_api_key"])
def test_credentials_are_blank_by_default(name):
    assert getattr(settings, name) == ""


def test_real_network_is_blocked():
    with pytest.raises(RuntimeError, match="real network"):
        httpx.get("https://people.googleapis.com/")


def test_mock_transport_still_works():
    """Tests that fake HTTP with httpx.MockTransport must keep working."""
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True}))
    with httpx.Client(transport=transport) as client:
        assert client.get("https://people.googleapis.com/").json() == {"ok": True}


def test_card_patch_background_sync_writes_to_test_db(client_with_test_db):
    """End to end: the sync a card PATCH schedules records its history in the
    test DB (token is blank, so it records an error row and never calls Google)."""
    import asyncio

    from sqlalchemy import select

    from app.db.models import Card, CardSyncHistory, Person

    async def _seed():
        async with client_with_test_db.session_maker() as db:
            person = Person()
            db.add(person)
            await db.flush()
            card = Card(person_id=person.id)
            db.add(card)
            await db.commit()
            return card.external_id, card.id

    ext_id, card_id = asyncio.run(_seed())
    resp = client_with_test_db.patch(f"/api/v2/cards/{ext_id}", json={"notes": "x"})
    assert resp.status_code == 200

    async def _history():
        async with client_with_test_db.session_maker() as db:
            return (await db.scalars(
                select(CardSyncHistory).where(CardSyncHistory.card_id == card_id)
            )).all()

    rows = asyncio.run(_history())
    assert len(rows) == 1
    assert rows[0].destination == "google_contacts"
