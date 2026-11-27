"""App preferences (Settings page): defaults, validation, and the automatic
export following the chosen destinations."""
import asyncio

from sqlalchemy import select

from app.db.models import Card, CardSyncHistory, Person
from app.db.session import get_db
from app.main import app


def test_defaults_match_previous_behaviour(client_with_test_db):
    """A fresh database: auto-export to Google Contacts, Project Management on."""
    r = client_with_test_db.get("/api/v2/settings/app")
    assert r.status_code == 200
    assert r.json() == {
        "auto_export_destinations": ["google_contacts"],
        "project_management_enabled": True,
    }


def test_partial_update_keeps_other_settings(client_with_test_db):
    r = client_with_test_db.patch("/api/v2/settings/app", json={"project_management_enabled": False})
    assert r.status_code == 200
    assert r.json()["project_management_enabled"] is False
    assert r.json()["auto_export_destinations"] == ["google_contacts"]

    # Stored order follows the app's order, duplicates dropped
    r = client_with_test_db.patch(
        "/api/v2/settings/app",
        json={"auto_export_destinations": ["google_contacts", "odoo", "odoo"]},
    )
    assert r.json() == {
        "auto_export_destinations": ["odoo", "google_contacts"],
        "project_management_enabled": False,
    }
    # And it persists
    assert client_with_test_db.get("/api/v2/settings/app").json()["auto_export_destinations"] == [
        "odoo", "google_contacts",
    ]


def test_unknown_destination_is_rejected(client_with_test_db):
    r = client_with_test_db.patch("/api/v2/settings/app", json={"auto_export_destinations": ["dropbox"]})
    assert r.status_code == 422
    assert client_with_test_db.get("/api/v2/settings/app").json()["auto_export_destinations"] == [
        "google_contacts",
    ]


def _make_card(ext: str) -> int:
    holder = {}

    async def _setup():
        async for db in app.dependency_overrides[get_db]():
            person = Person(external_id=f"p-{ext}")
            db.add(person)
            await db.flush()
            card = Card(external_id=ext, person_id=person.id, sync_status="pending")
            db.add(card)
            await db.flush()
            holder["id"] = card.id
            await db.commit()
            break

    asyncio.run(_setup())
    return holder["id"]


def _history() -> list[tuple[str, str]]:
    out = []

    async def _read():
        async for db in app.dependency_overrides[get_db]():
            rows = (await db.scalars(select(CardSyncHistory).order_by(CardSyncHistory.id))).all()
            out.extend((r.destination, r.result) for r in rows)
            break

    asyncio.run(_read())
    return out


def test_auto_export_off_does_nothing(client_with_test_db, monkeypatch):
    from app.services import contact_sync

    called = []

    async def _fake_export_one(db, card, destination, company_partner_id=None):
        called.append(destination)

    from app.routers.v2 import export as export_router
    monkeypatch.setattr(export_router, "_export_one", _fake_export_one)

    client_with_test_db.patch("/api/v2/settings/app", json={"auto_export_destinations": []})
    card_id = _make_card("off")
    asyncio.run(contact_sync.auto_sync_card(card_id))
    assert called == []
    assert _history() == []


def test_auto_export_runs_each_chosen_destination(client_with_test_db, monkeypatch):
    """Odoo and Google Contacts both chosen: one export (and one history row) each."""
    from app.services import contact_sync

    async def _fake_odoo(legacy, existing_odoo_id, company_partner_id):
        return 4242

    async def _fake_google(db, card, legacy):
        return "created", None

    import app.services.odoo_sync as odoo_sync
    monkeypatch.setattr(odoo_sync, "sync_to_odoo", _fake_odoo)
    monkeypatch.setattr(contact_sync, "sync_card_to_google_contacts", _fake_google)

    client_with_test_db.patch(
        "/api/v2/settings/app", json={"auto_export_destinations": ["odoo", "google_contacts"]},
    )
    card_id = _make_card("both")
    asyncio.run(contact_sync.auto_sync_card(card_id))
    assert _history() == [("odoo", "created"), ("google_contacts", "created")]
