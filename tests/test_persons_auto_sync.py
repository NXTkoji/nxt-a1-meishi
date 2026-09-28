"""Person edits auto-push the person's contact to Google Contacts."""
import asyncio
from datetime import datetime

import httpx
from sqlalchemy import select

from app.db.models import Card, CardSyncHistory, ContactDetail, Person
from app.db.session import get_db
from app.main import app


def _mock_google(monkeypatch, create_delay=0.0):
    """Fake the People API. Returns a list of ("create" | "update", url) calls.

    create_delay stretches the createContact call so two overlapping syncs
    would both reach it if nothing serialized them.
    """
    calls = []

    async def fake_post(self, url, **kwargs):
        if "oauth2.googleapis.com" in url:
            return httpx.Response(200, json={"access_token": "atoken"}, request=httpx.Request("POST", url))
        calls.append(("create", url))
        await asyncio.sleep(create_delay)
        return httpx.Response(200, json={"resourceName": "people/c900"}, request=httpx.Request("POST", url))

    async def fake_get(self, url, **kwargs):
        return httpx.Response(200, json={"etag": "e1"}, request=httpx.Request("GET", url))

    async def fake_patch(self, url, **kwargs):
        calls.append(("update", url))
        return httpx.Response(200, json={"resourceName": "people/c900"}, request=httpx.Request("PATCH", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    monkeypatch.setattr(httpx.AsyncClient, "patch", fake_patch)

    from app.config import settings
    monkeypatch.setattr(settings, "google_client_id", "cid")
    monkeypatch.setattr(settings, "google_client_secret", "csecret")
    monkeypatch.setattr(settings, "google_refresh_token", "rtoken")
    return calls


def _use_test_db_for_sync(client, monkeypatch):
    from app.services import contact_sync
    monkeypatch.setattr(contact_sync, "AsyncSessionLocal", client.session_maker)
    # Fresh locks per test: each test DB restarts person ids at 1, and a
    # lock left over from another test's event loop can't be awaited here.
    monkeypatch.setattr(contact_sync, "_person_locks", {})


def _run_db(fn):
    """Run fn(db) against the test DB and commit."""
    async def _inner():
        async for db in app.dependency_overrides[get_db]():
            result = await fn(db)
            await db.commit()
            return result
    return asyncio.run(_inner())


def _make_person(db, ext_id, cards):
    """Create a person with the given cards: list of (ext_id, created_at, deleted_at)."""
    async def _inner():
        person = Person(external_id=ext_id)
        db.add(person)
        await db.flush()
        for card_ext, created_at, deleted_at in cards:
            db.add(Card(
                external_id=card_ext, person_id=person.id, sync_status="pending",
                created_at=created_at, deleted_at=deleted_at,
            ))
        detail = ContactDetail(person_id=person.id, detail_type="email", value="a@x.com", is_primary=True)
        db.add(detail)
        await db.flush()
        return person.id, detail.id
    return _inner()


def test_contact_detail_edit_pushes_newest_live_card(client_with_test_db, monkeypatch):
    calls = _mock_google(monkeypatch)
    _use_test_db_for_sync(client_with_test_db, monkeypatch)

    person_id, detail_id = _run_db(lambda db: _make_person(db, "p1", [
        ("old", datetime(2026, 1, 1), None),
        ("new", datetime(2026, 6, 1), None),
        # Newest overall, but deleted — must be skipped.
        ("gone", datetime(2026, 9, 1), datetime(2026, 9, 2)),
    ]))

    resp = client_with_test_db.patch(
        f"/api/v2/persons/p1/contact-details/{detail_id}", json={"value": "b@x.com"},
    )
    assert resp.status_code == 200
    assert [c[0] for c in calls] == ["create"]

    async def _check(db):
        person = await db.get(Person, person_id)
        assert person.google_resource == "people/c900"
        history = (await db.execute(select(CardSyncHistory))).scalars().all()
        card_ids = {c.id: c.external_id for c in (await db.execute(select(Card))).scalars()}
        assert [card_ids[h.card_id] for h in history] == ["new"]
        assert history[0].result == "created"
    _run_db(_check)


def test_person_without_card_is_not_pushed(client_with_test_db, monkeypatch):
    calls = _mock_google(monkeypatch)
    _use_test_db_for_sync(client_with_test_db, monkeypatch)

    _run_db(lambda db: _make_person(db, "p2", []))

    resp = client_with_test_db.patch("/api/v2/persons/p2", json={"birthday": "1980-05-01"})
    assert resp.status_code == 200
    assert calls == []


def test_overlapping_syncs_for_one_person_create_one_contact(client_with_test_db, monkeypatch):
    """Two quick edits queue two syncs; they must not both create a contact."""
    calls = _mock_google(monkeypatch, create_delay=0.2)
    _use_test_db_for_sync(client_with_test_db, monkeypatch)

    person_id, _ = _run_db(lambda db: _make_person(db, "p3", [("c3", datetime(2026, 1, 1), None)]))

    from app.services.contact_sync import auto_sync_person

    async def _both():
        await asyncio.gather(auto_sync_person(person_id), auto_sync_person(person_id))
    asyncio.run(_both())

    # Serialized: the first creates, the second sees google_resource and updates.
    assert [c[0] for c in calls] == ["create", "update"]
