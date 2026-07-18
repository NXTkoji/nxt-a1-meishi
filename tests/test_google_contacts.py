import asyncio

import httpx

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

    async def fake_patch(self, url, **kwargs):
        captured["params"] = kwargs.get("params", {})
        return httpx.Response(200, json={"resourceName": "people/c123"}, request=httpx.Request("PATCH", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    monkeypatch.setattr(httpx.AsyncClient, "patch", fake_patch)

    card = LegacyCard(person=LegacyPerson(), my_company_labels=["NXT"])
    asyncio.run(sync_to_google(card, existing_resource="people/c123"))

    assert "userDefined" in captured["params"]["updatePersonFields"]
