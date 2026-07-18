import asyncio

import httpx

from app.db.session import get_db
from app.main import app


def test_update_card_triggers_google_contacts_sync(client_with_test_db, monkeypatch):
    async def fake_post(self, url, **kwargs):
        if "oauth2.googleapis.com" in url:
            return httpx.Response(200, json={"access_token": "atoken"}, request=httpx.Request("POST", url))
        return httpx.Response(200, json={"resourceName": "people/c777"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    from app.config import settings
    monkeypatch.setattr(settings, "google_client_id", "cid")
    monkeypatch.setattr(settings, "google_client_secret", "csecret")
    monkeypatch.setattr(settings, "google_refresh_token", "rtoken")

    from app.services import contact_sync
    monkeypatch.setattr(contact_sync, "AsyncSessionLocal", client_with_test_db.session_maker)

    from app.db.models import Card, Person
    from sqlalchemy import select

    card_ext_id_holder = {}

    async def _setup():
        async for db in app.dependency_overrides[get_db]():
            person = Person(external_id="p1")
            db.add(person)
            await db.flush()
            card = Card(external_id="c1", person_id=person.id, sync_status="pending")
            db.add(card)
            await db.flush()
            card_ext_id_holder["id"] = card.external_id
            await db.commit()
            break

    asyncio.run(_setup())

    resp = client_with_test_db.patch(
        f"/api/v2/cards/{card_ext_id_holder['id']}",
        json={"notes": "updated notes"},
    )
    assert resp.status_code == 200

    async def _check():
        async for db in app.dependency_overrides[get_db]():
            person = await db.scalar(select(Person).where(Person.external_id == "p1"))
            assert person.google_resource == "people/c777"
            break

    asyncio.run(_check())
