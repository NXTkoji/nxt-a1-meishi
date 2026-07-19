import asyncio

import httpx
import pytest

from app.models.card import Card as LegacyCard, Person as LegacyPerson
from app.services.google_contacts import _build_person_body, sync_to_google


def test_build_person_body_adds_met_as_custom_field():
    card = LegacyCard(
        person=LegacyPerson(),
        my_company_labels=["NXT", "正康有限公司"],
    )

    body = _build_person_body(card)

    assert body["userDefined"] == [{"key": "Met As", "value": "NXT, 正康有限公司"}]


def test_build_person_body_omits_met_as_when_empty():
    card = LegacyCard(person=LegacyPerson())

    body = _build_person_body(card)

    assert "userDefined" not in body


def test_build_person_body_dedupes_and_sorts_met_as():
    card = LegacyCard(
        person=LegacyPerson(),
        my_company_labels=["NXT", "NXT", "Personal"],
    )

    body = _build_person_body(card)

    assert body["userDefined"] == [{"key": "Met As", "value": "NXT, Personal"}]


def test_sync_to_google_update_mask_includes_user_defined(monkeypatch):
    """Regression test: Google's People API silently drops any body field not
    listed in updatePersonFields, so the 'Met As' custom field would appear on
    contact creation but silently fail to update on every subsequent edit if
    'userDefined' were missing from this mask.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "google_client_id", "cid")
    monkeypatch.setattr(settings, "google_client_secret", "csecret")
    monkeypatch.setattr(settings, "google_refresh_token", "rtoken")

    captured = {}

    async def fake_post(self, url, **kwargs):
        return httpx.Response(200, json={"access_token": "atoken"}, request=httpx.Request("POST", url))

    async def fake_get(self, url, **kwargs):
        return httpx.Response(200, json={"etag": "some-etag"}, request=httpx.Request("GET", url))

    async def fake_patch(self, url, **kwargs):
        captured["params"] = kwargs.get("params", {})
        return httpx.Response(200, json={"resourceName": "people/c123"}, request=httpx.Request("PATCH", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    monkeypatch.setattr(httpx.AsyncClient, "patch", fake_patch)

    card = LegacyCard(person=LegacyPerson(), my_company_labels=["NXT"])
    asyncio.run(sync_to_google(card, existing_resource="people/c123"))

    assert "userDefined" in captured["params"]["updatePersonFields"]


def test_sync_to_google_update_includes_current_etag(monkeypatch):
    """Regression test: Google's People API rejects updateContact calls that
    don't include the contact's current etag with a 400 INVALID_ARGUMENT
    ("Request must set person.etag..."). Confirmed against the real API
    during Task 5 manual QA. sync_to_google must fetch the current etag
    before updating and include it in the request body.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "google_client_id", "cid")
    monkeypatch.setattr(settings, "google_client_secret", "csecret")
    monkeypatch.setattr(settings, "google_refresh_token", "rtoken")

    captured = {}

    async def fake_post(self, url, **kwargs):
        return httpx.Response(200, json={"access_token": "atoken"}, request=httpx.Request("POST", url))

    async def fake_get(self, url, **kwargs):
        return httpx.Response(200, json={"etag": "current-etag-value"}, request=httpx.Request("GET", url))

    async def fake_patch(self, url, **kwargs):
        captured["body"] = kwargs.get("json", {})
        return httpx.Response(200, json={"resourceName": "people/c123"}, request=httpx.Request("PATCH", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    monkeypatch.setattr(httpx.AsyncClient, "patch", fake_patch)

    card = LegacyCard(person=LegacyPerson())
    asyncio.run(sync_to_google(card, existing_resource="people/c123"))

    assert captured["body"]["etag"] == "current-etag-value"


def test_sync_to_google_create_does_not_fetch_etag(monkeypatch):
    """Creating a new contact has no existing etag to fetch — only the
    update path should make the extra GET call."""
    from app.config import settings

    monkeypatch.setattr(settings, "google_client_id", "cid")
    monkeypatch.setattr(settings, "google_client_secret", "csecret")
    monkeypatch.setattr(settings, "google_refresh_token", "rtoken")

    get_called = {"value": False}

    async def fake_post(self, url, **kwargs):
        if "oauth2.googleapis.com" in url:
            return httpx.Response(200, json={"access_token": "atoken"}, request=httpx.Request("POST", url))
        return httpx.Response(200, json={"resourceName": "people/c123"}, request=httpx.Request("POST", url))

    async def fake_get(self, url, **kwargs):
        get_called["value"] = True
        return httpx.Response(200, json={"etag": "unused"}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    card = LegacyCard(person=LegacyPerson())
    asyncio.run(sync_to_google(card, existing_resource=None))

    assert get_called["value"] is False


def test_sync_to_google_retries_on_read_timeout_then_succeeds(monkeypatch):
    """Regression test: production has hit bare httpx.ReadTimeout (connection
    stalls, no response at all) that succeeded immediately on a second
    attempt. sync_to_google must retry transient network errors instead of
    propagating the first failure.
    """
    from app.config import settings
    from app.services import google_contacts

    monkeypatch.setattr(settings, "google_client_id", "cid")
    monkeypatch.setattr(settings, "google_client_secret", "csecret")
    monkeypatch.setattr(settings, "google_refresh_token", "rtoken")
    monkeypatch.setattr(google_contacts, "_RETRY_BACKOFF_SECONDS", 0)

    call_count = {"value": 0}

    async def fake_post(self, url, **kwargs):
        if "oauth2.googleapis.com" in url:
            return httpx.Response(200, json={"access_token": "atoken"}, request=httpx.Request("POST", url))
        call_count["value"] += 1
        if call_count["value"] == 1:
            raise httpx.ReadTimeout("")
        return httpx.Response(200, json={"resourceName": "people/c123"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    card = LegacyCard(person=LegacyPerson())
    resource = asyncio.run(sync_to_google(card, existing_resource=None))

    assert resource == "people/c123"
    assert call_count["value"] == 2


def test_sync_to_google_gives_up_after_max_attempts(monkeypatch):
    """Persistent transient failure must eventually surface as an error
    (background caller records it in CardSyncHistory), not retry forever.
    """
    from app.config import settings
    from app.services import google_contacts

    monkeypatch.setattr(settings, "google_client_id", "cid")
    monkeypatch.setattr(settings, "google_client_secret", "csecret")
    monkeypatch.setattr(settings, "google_refresh_token", "rtoken")
    monkeypatch.setattr(google_contacts, "_RETRY_BACKOFF_SECONDS", 0)

    call_count = {"value": 0}

    async def fake_post(self, url, **kwargs):
        if "oauth2.googleapis.com" in url:
            return httpx.Response(200, json={"access_token": "atoken"}, request=httpx.Request("POST", url))
        call_count["value"] += 1
        raise httpx.ReadTimeout("")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    card = LegacyCard(person=LegacyPerson())
    with pytest.raises(httpx.ReadTimeout):
        asyncio.run(sync_to_google(card, existing_resource=None))

    assert call_count["value"] == google_contacts._MAX_ATTEMPTS


def test_sync_to_google_does_not_retry_permanent_400(monkeypatch):
    """A malformed-request 400 is not transient — retrying an identical
    request wastes time and API quota without changing the outcome.
    """
    from app.config import settings
    from app.services import google_contacts

    monkeypatch.setattr(settings, "google_client_id", "cid")
    monkeypatch.setattr(settings, "google_client_secret", "csecret")
    monkeypatch.setattr(settings, "google_refresh_token", "rtoken")
    monkeypatch.setattr(google_contacts, "_RETRY_BACKOFF_SECONDS", 0)

    call_count = {"value": 0}

    async def fake_post(self, url, **kwargs):
        if "oauth2.googleapis.com" in url:
            return httpx.Response(200, json={"access_token": "atoken"}, request=httpx.Request("POST", url))
        call_count["value"] += 1
        return httpx.Response(400, json={"error": {"message": "bad request"}}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    card = LegacyCard(person=LegacyPerson())
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(sync_to_google(card, existing_resource=None))

    assert call_count["value"] == 1


def test_sync_to_google_retries_on_503(monkeypatch):
    """Google's own server-error/rate-limit statuses are transient too."""
    from app.config import settings
    from app.services import google_contacts

    monkeypatch.setattr(settings, "google_client_id", "cid")
    monkeypatch.setattr(settings, "google_client_secret", "csecret")
    monkeypatch.setattr(settings, "google_refresh_token", "rtoken")
    monkeypatch.setattr(google_contacts, "_RETRY_BACKOFF_SECONDS", 0)

    call_count = {"value": 0}

    async def fake_post(self, url, **kwargs):
        if "oauth2.googleapis.com" in url:
            return httpx.Response(200, json={"access_token": "atoken"}, request=httpx.Request("POST", url))
        call_count["value"] += 1
        if call_count["value"] == 1:
            return httpx.Response(503, text="unavailable", request=httpx.Request("POST", url))
        return httpx.Response(200, json={"resourceName": "people/c123"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    card = LegacyCard(person=LegacyPerson())
    resource = asyncio.run(sync_to_google(card, existing_resource=None))

    assert resource == "people/c123"
    assert call_count["value"] == 2
