"""Duplicate-prevention in Google Contacts sync:
- creates are idempotent via the clientData person marker (a timed-out
  create that Google actually saved is found, not created again),
- a linked contact that is gone is relinked via the marker, or reported
  clearly and never recreated,
- merging persons deletes the merged-away persons' Google contacts.
"""
import asyncio
import itertools

import httpx
import pytest
from sqlalchemy import select

from app.db.models import Card, Person
from app.db.session import get_db
from app.main import app
from app.models.card import Card as LegacyCard, Person as LegacyPerson
from app.services import google_contacts
from app.services.google_contacts import MARKER_KEY, GoogleContactGoneError, sync_to_google


class FakeGoogle:
    """In-memory People API: {resource: clientData list}. Records calls."""

    def __init__(self, monkeypatch):
        self.contacts: dict[str, list] = {}
        self.calls: list[tuple[str, str]] = []
        # When set, the next createContact saves the contact and then raises
        # this — a timeout after Google already committed the write.
        self.fail_after_create: Exception | None = None
        self._ids = itertools.count(100)

        from app.config import settings
        monkeypatch.setattr(settings, "google_client_id", "cid")
        monkeypatch.setattr(settings, "google_client_secret", "csecret")
        monkeypatch.setattr(settings, "google_refresh_token", "rtoken")
        monkeypatch.setattr(google_contacts, "_RETRY_BACKOFF_SECONDS", 0)
        # Bound methods are not descriptors, so the AsyncClient instance is
        # not passed in — the handlers take (url, **kwargs) only.
        monkeypatch.setattr(httpx.AsyncClient, "post", self._post)
        monkeypatch.setattr(httpx.AsyncClient, "get", self._get)
        monkeypatch.setattr(httpx.AsyncClient, "patch", self._patch)
        monkeypatch.setattr(httpx.AsyncClient, "delete", self._delete)

    @staticmethod
    def _resp(method, url, status=200, json=None):
        return httpx.Response(status, json=json or {}, request=httpx.Request(method, url))

    @staticmethod
    def _resource(url):
        # ".../v1/people/c101:updateContact" -> "people/c101"
        return "people/" + url.split("/people/")[1].split(":")[0]

    async def _post(self, url, **kwargs):
        if "oauth2.googleapis.com" in url:
            return self._resp("POST", url, json={"access_token": "atoken"})
        self.calls.append(("create", url))
        resource = f"people/c{next(self._ids)}"
        self.contacts[resource] = kwargs["json"].get("clientData", [])
        if self.fail_after_create:
            exc, self.fail_after_create = self.fail_after_create, None
            raise exc
        return self._resp("POST", url, json={"resourceName": resource})

    async def _get(self, url, **kwargs):
        if url.endswith("/people/me/connections"):
            self.calls.append(("list", url))
            conns = [{"resourceName": r, "clientData": cd} for r, cd in self.contacts.items()]
            return self._resp("GET", url, json={"connections": conns})
        resource = self._resource(url)
        if resource not in self.contacts:
            return self._resp("GET", url, status=404)
        return self._resp("GET", url, json={"etag": "e1"})

    async def _patch(self, url, **kwargs):
        resource = self._resource(url)
        self.calls.append(("update", resource))
        self.contacts[resource] = kwargs["json"].get("clientData", [])
        return self._resp("PATCH", url, json={"resourceName": resource})

    async def _delete(self, url, **kwargs):
        resource = self._resource(url)
        self.calls.append(("delete", resource))
        if self.contacts.pop(resource, None) is None:
            return self._resp("DELETE", url, status=404)
        return self._resp("DELETE", url)

    def kinds(self):
        return [c[0] for c in self.calls if c[0] != "list"]


def _card():
    return LegacyCard(person=LegacyPerson())


def test_create_writes_marker(monkeypatch):
    google = FakeGoogle(monkeypatch)
    resource = asyncio.run(sync_to_google(_card(), None, person_marker="p-1"))
    assert google.kinds() == ["create"]
    assert google.contacts[resource] == [{"key": MARKER_KEY, "value": "p-1"}]


def test_create_updates_contact_already_carrying_marker(monkeypatch):
    """The person has no google_resource, but a contact with its marker exists
    (e.g. an earlier create whose response was lost): update it, don't create."""
    google = FakeGoogle(monkeypatch)
    google.contacts["people/c1"] = [{"key": MARKER_KEY, "value": "p-1"}]
    resource = asyncio.run(sync_to_google(_card(), None, person_marker="p-1"))
    assert resource == "people/c1"
    assert google.kinds() == ["update"]


def test_timed_out_create_that_google_saved_is_not_duplicated(monkeypatch):
    """The July duplicates: createContact saved the contact but the response
    timed out, and the retry created it again."""
    google = FakeGoogle(monkeypatch)
    google.fail_after_create = httpx.ReadTimeout("")
    resource = asyncio.run(sync_to_google(_card(), None, person_marker="p-1"))
    assert google.kinds() == ["create", "update"]
    assert len(google.contacts) == 1
    assert resource in google.contacts


def test_gone_contact_is_relinked_via_marker(monkeypatch):
    """Linked contact merged away in Google; the survivor kept the marker."""
    google = FakeGoogle(monkeypatch)
    google.contacts["people/c2"] = [{"key": MARKER_KEY, "value": "p-1"}]
    resource = asyncio.run(sync_to_google(_card(), "people/c-dead", person_marker="p-1"))
    assert resource == "people/c2"
    assert google.kinds() == ["update"]


def test_gone_contact_without_marker_raises_and_is_not_recreated(monkeypatch):
    google = FakeGoogle(monkeypatch)
    with pytest.raises(GoogleContactGoneError, match="people/c-dead"):
        asyncio.run(sync_to_google(_card(), "people/c-dead", person_marker="p-1"))
    assert google.kinds() == []
    assert google.contacts == {}


def test_merge_deletes_merged_away_google_contacts(client_with_test_db, monkeypatch):
    google = FakeGoogle(monkeypatch)
    from app.services import contact_sync
    monkeypatch.setattr(contact_sync, "AsyncSessionLocal", client_with_test_db.session_maker)
    monkeypatch.setattr(contact_sync, "_person_locks", {})
    google.contacts.update({"people/c-a": [], "people/c-b": [], "people/c-c": []})

    async def _setup():
        async for db in app.dependency_overrides[get_db]():
            # Primary has no Google contact: it adopts the first source's.
            for ext, resource in [("prim", None), ("s1", "people/c-a"), ("s2", "people/c-b")]:
                db.add(Person(external_id=ext, google_resource=resource))
            await db.flush()
            prim = await db.scalar(select(Person).where(Person.external_id == "prim"))
            db.add(Card(external_id="card-p", person_id=prim.id, sync_status="pending"))
            await db.commit()
            break
    asyncio.run(_setup())

    resp = client_with_test_db.post("/api/v2/persons/prim/merge", json={"source_ids": ["s1", "s2"]})
    assert resp.status_code == 200

    # c-a adopted and updated, c-b deleted, the unrelated c-c untouched.
    assert ("update", "people/c-a") in google.calls
    assert ("delete", "people/c-b") in google.calls
    assert set(google.contacts) == {"people/c-a", "people/c-c"}

    async def _check():
        async for db in app.dependency_overrides[get_db]():
            prim = await db.scalar(select(Person).where(Person.external_id == "prim"))
            assert prim.google_resource == "people/c-a"
            break
    asyncio.run(_check())
