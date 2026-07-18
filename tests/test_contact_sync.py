import asyncio

import httpx

from app.db.session import get_db
from app.main import app


def _mock_google(monkeypatch, resource_name="people/c123"):
    async def fake_post(self, url, **kwargs):
        if "oauth2.googleapis.com" in url:
            return httpx.Response(200, json={"access_token": "atoken"}, request=httpx.Request("POST", url))
        return httpx.Response(200, json={"resourceName": resource_name}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    from app.config import settings
    monkeypatch.setattr(settings, "google_client_id", "cid")
    monkeypatch.setattr(settings, "google_client_secret", "csecret")
    monkeypatch.setattr(settings, "google_refresh_token", "rtoken")


def test_sync_card_to_google_contacts_creates_and_updates_person(client_with_test_db, monkeypatch):
    _mock_google(monkeypatch)

    from app.db.models import Card, Person
    from app.models.card import Card as LegacyCard, Person as LegacyPerson
    from app.services.contact_sync import sync_card_to_google_contacts

    async def _run():
        async for db in app.dependency_overrides[get_db]():
            person = Person(external_id="p1")
            db.add(person)
            await db.flush()
            card = Card(external_id="c1", person_id=person.id, sync_status="pending")
            db.add(card)
            await db.flush()
            card.person = person

            legacy = LegacyCard(person=LegacyPerson())
            result, error = await sync_card_to_google_contacts(db, card, legacy)

            assert result == "created"
            assert error is None
            assert person.google_resource == "people/c123"
            assert card.google_sync_at is not None
            break

    asyncio.run(_run())


def test_sync_card_to_google_contacts_reports_error(client_with_test_db, monkeypatch):
    async def fake_post(self, url, **kwargs):
        return httpx.Response(500, text="boom", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    from app.config import settings
    monkeypatch.setattr(settings, "google_client_id", "cid")
    monkeypatch.setattr(settings, "google_client_secret", "csecret")
    monkeypatch.setattr(settings, "google_refresh_token", "rtoken")

    from app.db.models import Card, Person
    from app.models.card import Card as LegacyCard, Person as LegacyPerson
    from app.services.contact_sync import sync_card_to_google_contacts

    async def _run():
        async for db in app.dependency_overrides[get_db]():
            person = Person(external_id="p2")
            db.add(person)
            await db.flush()
            card = Card(external_id="c2", person_id=person.id, sync_status="pending")
            db.add(card)
            await db.flush()
            card.person = person

            legacy = LegacyCard(person=LegacyPerson())
            result, error = await sync_card_to_google_contacts(db, card, legacy)

            assert result == "error"
            assert error is not None
            assert person.google_resource is None
            break

    asyncio.run(_run())


def test_auto_sync_card_records_history(client_with_test_db, monkeypatch):
    _mock_google(monkeypatch, resource_name="people/c999")

    from app.services import contact_sync
    monkeypatch.setattr(contact_sync, "AsyncSessionLocal", client_with_test_db.session_maker)

    from app.db.models import Card, CardSyncHistory, Person
    from sqlalchemy import select

    card_id_holder = {}

    async def _setup():
        async for db in app.dependency_overrides[get_db]():
            person = Person(external_id="p3")
            db.add(person)
            await db.flush()
            card = Card(external_id="c3", person_id=person.id, sync_status="pending")
            db.add(card)
            await db.flush()
            card_id_holder["id"] = card.id
            # Explicit commit needed: breaking out of this async-generator loop
            # skips _override_get_db's post-yield commit (async generators are
            # not auto-closed on `break`), so without this the row would be
            # invisible to auto_sync_card's independently-opened session.
            await db.commit()
            break

    asyncio.run(_setup())
    asyncio.run(contact_sync.auto_sync_card(card_id_holder["id"]))

    async def _check():
        async for db in app.dependency_overrides[get_db]():
            person = await db.scalar(select(Person).where(Person.external_id == "p3"))
            assert person.google_resource == "people/c999"
            history = (await db.scalars(select(CardSyncHistory))).all()
            assert len(history) == 1
            assert history[0].destination == "google_contacts"
            assert history[0].result == "created"
            break

    asyncio.run(_check())


def test_auto_sync_card_records_error_history_when_legacy_card_build_fails(client_with_test_db, monkeypatch):
    """If something inside auto_sync_card's orchestration (e.g. build_legacy_card)
    raises unexpectedly, auto_sync_card must not propagate the exception (it runs
    as a fire-and-forget BackgroundTasks call with nothing awaiting it) and must
    still record a CardSyncHistory row with result="error" so the failure is
    visible instead of vanishing silently.
    """
    from app.services import contact_sync
    monkeypatch.setattr(contact_sync, "AsyncSessionLocal", client_with_test_db.session_maker)

    def _boom(*args, **kwargs):
        raise RuntimeError("unexpected shape surprise")

    monkeypatch.setattr(contact_sync, "build_legacy_card", _boom)

    from app.db.models import Card, CardSyncHistory, Person
    from sqlalchemy import select

    card_id_holder = {}

    async def _setup():
        async for db in app.dependency_overrides[get_db]():
            person = Person(external_id="p4")
            db.add(person)
            await db.flush()
            card = Card(external_id="c4", person_id=person.id, sync_status="pending")
            db.add(card)
            await db.flush()
            card_id_holder["id"] = card.id
            await db.commit()
            break

    asyncio.run(_setup())

    # Must not raise.
    asyncio.run(contact_sync.auto_sync_card(card_id_holder["id"]))

    async def _check():
        async for db in app.dependency_overrides[get_db]():
            person = await db.scalar(select(Person).where(Person.external_id == "p4"))
            # No successful sync happened, so the resource should stay unset.
            assert person.google_resource is None
            history = (await db.scalars(select(CardSyncHistory))).all()
            assert len(history) == 1
            assert history[0].destination == "google_contacts"
            assert history[0].result == "error"
            assert "unexpected shape surprise" in history[0].error_message
            break

    asyncio.run(_check())
