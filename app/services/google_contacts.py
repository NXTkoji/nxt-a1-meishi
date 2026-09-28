from __future__ import annotations

import asyncio
import logging
import time

import httpx

from app.config import settings
from app.models.card import Card

logger = logging.getLogger(__name__)

TOKEN_URL = "https://oauth2.googleapis.com/token"
PEOPLE_API = "https://people.googleapis.com/v1"

# Retry transient failures — a handful of production syncs have hit bare
# httpx.ReadTimeout (connection just stalls, nothing to inspect) and
# succeeded immediately on a second attempt. Also retry Google's own
# rate-limit/server-error statuses; don't retry other 4xx (e.g. a malformed
# body) since retrying an identical request won't change the outcome.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = 1.0

# Every contact this app writes carries its person's external_id in
# clientData under this key. It makes creates idempotent: before creating,
# the app looks for a contact already carrying the marker, so a create that
# timed out after Google had saved it (then retried, or re-sent on the next
# edit) updates that contact instead of leaving a duplicate. It also lets a
# contact whose linked resource disappeared (merged in Google) be found again.
MARKER_KEY = "nxt_a1_person"


class GoogleContactGoneError(Exception):
    """The linked Google contact no longer exists and no contact carrying
    this person's marker was found — it was deleted, or merged in Google into
    a contact the app never wrote to. Deliberately NOT recreated: that would
    resurrect contacts the user removed on purpose. Needs a manual relink."""

    def __init__(self, resource: str):
        super().__init__(
            f"Linked Google contact {resource} no longer exists (deleted or merged "
            f"in Google) - relink this person to the surviving contact"
        )
        self.resource = resource


# Fields the app owns on a Google contact: on update they are always in
# updatePersonFields, so when the app has none of one (e.g. the last website
# was deleted) Google clears it instead of keeping the stale value. Fields
# people also edit directly in Google (notes, birthday, events, relations,
# custom fields) are left alone when the app has nothing for them, and
# memberships can't be cleared to empty (Google rejects it).
_ALWAYS_REPLACED_FIELDS = (
    "names", "nicknames", "organizations", "phoneNumbers",
    "emailAddresses", "addresses", "urls", "imClients",
)

# Contact-group name -> resourceName, cached across calls within a batch run.
_group_cache: dict[str, str] = {}
_group_cache_loaded_at: float = 0.0
_GROUP_CACHE_TTL = 300  # seconds


def _parse_iso_date(value: str) -> dict | None:
    """Parse a 'YYYY-MM-DD' string into a People API Date object."""
    try:
        year, month, day = (int(p) for p in value.split("-"))
        return {"year": year, "month": month, "day": day}
    except (ValueError, AttributeError):
        return None


def _parse_birthday(value: str) -> dict | None:
    """Parse a birthday string into a People API Date object.

    Accepts a full "YYYY-MM-DD" or a year-less "--MM-DD" (year unknown).
    The People API accepts a Date with month/day and no year.
    """
    if not value:
        return None
    try:
        if value.startswith("--"):
            month, day = value[2:].split("-")  # strip leading "--", left with "MM-DD"
            return {"month": int(month), "day": int(day)}
        year, month, day = value.split("-")
        return {"year": int(year), "month": int(month), "day": int(day)}
    except (ValueError, AttributeError):
        return None


async def _get_group_resource(client: httpx.AsyncClient, headers: dict, name: str) -> str | None:
    """Look up a user-created Google Contact Group's resourceName by exact display name."""
    global _group_cache_loaded_at
    if not name:
        return None
    if not _group_cache or (time.monotonic() - _group_cache_loaded_at) > _GROUP_CACHE_TTL:
        resp = await client.get(
            f"{PEOPLE_API}/contactGroups",
            headers=headers,
            params={"groupFields": "name,groupType", "pageSize": 200},
        )
        resp.raise_for_status()
        _group_cache.clear()
        for g in resp.json().get("contactGroups", []):
            if g.get("groupType") == "USER_CONTACT_GROUP":
                _group_cache[g["name"]] = g["resourceName"]
        _group_cache_loaded_at = time.monotonic()

    resource = _group_cache.get(name)
    if not resource:
        logger.warning(
            "No Google Contact Group named %r exists — ask Koji whether to create it "
            "before this card's 'Met As' membership can be set.",
            name,
        )
    return resource


async def _get_access_token() -> str:
    """Exchange refresh token for a fresh access token."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(TOKEN_URL, data={
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "refresh_token": settings.google_refresh_token,
            "grant_type": "refresh_token",
        })
        resp.raise_for_status()
        return resp.json()["access_token"]


def _build_person_body(card: Card) -> dict:
    """Build Google People API Person resource from card data."""
    person = card.person
    body: dict = {}

    # Names — the People API rejects more than one names[] entry per source,
    # so send only the primary name and put any others in nicknames[] instead.
    primary_name = next((n for n in person.names if n.type == "primary"), None)
    if not primary_name and person.names:
        primary_name = person.names[0]
    other_names = [n.value for n in person.names if n is not primary_name and n.value]
    if primary_name:
        body["names"] = [{"unstructuredName": primary_name.value, "metadata": {"primary": True}}]
    if other_names:
        body["nicknames"] = [{"value": v} for v in other_names]

    # Organizations — native org/title as the primary entry; English company
    # name (if any) as a second entry, since Google doesn't restrict
    # organizations[] to one-per-source the way it does names[].
    orgs = []
    for pos in person.positions:
        org: dict = {}
        if pos.company:
            org["name"] = pos.company
        if pos.department:
            org["department"] = pos.department
        if pos.title:
            org["title"] = pos.title
        if org:
            orgs.append(org)
    english_company = next((pos.company_english for pos in person.positions if pos.company_english), "")
    if english_company:
        orgs.append({"name": english_company})
    if orgs:
        body["organizations"] = orgs

    # Phones
    phones = []
    for p in person.phones:
        type_map = {"work": "work", "mobile": "mobile", "fax": "workFax"}
        phones.append({
            "value": p.value,
            "type": type_map.get(p.type, "work"),
        })
    if phones:
        body["phoneNumbers"] = phones

    # Emails
    emails = []
    for e in person.emails:
        emails.append({"value": e.value, "type": e.type or "work"})
    if emails:
        body["emailAddresses"] = emails

    # Addresses — kept as a single formatted/street field rather than split
    # into city/state/postal, per Koji's call.
    addresses = []
    for a in person.addresses:
        addr: dict = {"type": a.type or "work"}
        if a.full:
            addr["formattedValue"] = a.full
            addr["streetAddress"] = a.full
        addresses.append(addr)
    if addresses:
        body["addresses"] = addresses

    # URLs — work website, personal website, plus LinkedIn as a further entry.
    urls = []
    if person.website:
        urls.append({"value": person.website, "type": "work"})
    if person.website_personal:
        urls.append({"value": person.website_personal, "type": "home"})
    if person.social.linkedin:
        urls.append({"value": person.social.linkedin, "type": "other", "formattedType": "LinkedIn"})
    if urls:
        body["urls"] = urls

    # IM clients — WeChat / LINE.
    im_clients = []
    if person.social.wechat:
        im_clients.append({"username": person.social.wechat, "protocol": "WeChat"})
    if person.social.line:
        im_clients.append({"username": person.social.line, "protocol": "LINE"})
    if im_clients:
        body["imClients"] = im_clients

    # Events — card received date as a labeled significant date.
    received = _parse_iso_date(card.received_date) if card.received_date else None
    if received:
        body["events"] = [{"date": received, "type": "Card received"}]

    # Birthday — full date or year-less "--MM-DD".
    birthday_date = _parse_birthday(person.birthday)
    if birthday_date:
        body["birthdays"] = [{"date": birthday_date, "text": person.birthday}]

    # User-defined custom fields — occasion + received location.
    user_defined = []
    if card.occasion_name or card.occasion_location:
        value = card.occasion_name
        if card.occasion_location:
            value = f"{value} ({card.occasion_location})" if value else card.occasion_location
        user_defined.append({"key": "Occasion", "value": value})
    if card.received_location:
        user_defined.append({"key": "Received location", "value": card.received_location})
    if user_defined:
        body["userDefined"] = user_defined

    # Relations — introduced_by, colleague, etc.
    relations = [
        {"person": r.name, "type": r.type}
        for r in person.relations
        if r.name and r.type
    ]
    if relations:
        body["relations"] = relations

    # Notes — English title (if any) plus card notes.
    note_parts = []
    english_titles = [pos.title_english for pos in person.positions if pos.title_english]
    if english_titles:
        note_parts.append(f"English title: {'; '.join(english_titles)}")
    if card.notes:
        note_parts.append(card.notes)
    if note_parts:
        body["biographies"] = [{"value": "\n".join(note_parts), "contentType": "TEXT_PLAIN"}]

    return body


async def sync_to_google(
    card: Card,
    existing_resource: str | None = None,
    person_marker: str | None = None,
) -> str | None:
    """Create or update a Google Contact. Returns resource name (e.g. 'people/c123').

    person_marker (the person's external_id) is written to the contact's
    clientData and used to find an existing contact before creating one —
    see MARKER_KEY. The returned resource can differ from existing_resource
    when the linked contact was gone and the marker found its successor.
    """
    if not settings.google_refresh_token:
        logger.warning("Google Contacts not configured, skipping")
        return None

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return await _sync_to_google_once(card, existing_resource, person_marker)
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            retryable, last_exc = True, exc
        except httpx.HTTPStatusError as exc:
            retryable = exc.response.status_code in _RETRYABLE_STATUS
            last_exc = exc

        if not retryable or attempt == _MAX_ATTEMPTS:
            raise last_exc

        delay = _RETRY_BACKOFF_SECONDS * attempt
        logger.warning(
            "Google Contacts sync attempt %d/%d failed (%r), retrying in %.0fs",
            attempt, _MAX_ATTEMPTS, last_exc, delay,
        )
        await asyncio.sleep(delay)


async def _get_etag(client: httpx.AsyncClient, headers: dict, resource: str) -> str | None:
    """Current etag of a contact, or None if the contact no longer exists."""
    resp = await client.get(
        f"{PEOPLE_API}/{resource}",
        headers=headers,
        params={"personFields": "metadata"},
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()["etag"]


async def _find_contact_by_marker(client: httpx.AsyncClient, headers: dict, marker: str) -> str | None:
    """Resource name of the contact whose clientData carries this marker.

    Lists all connections: the People API has no search by clientData, and
    searchContacts only covers names/emails/phones and lags behind writes.
    One page holds 1000 contacts, so this is normally a single request.
    """
    page_token = None
    while True:
        params = {"personFields": "clientData", "pageSize": 1000}
        if page_token:
            params["pageToken"] = page_token
        resp = await client.get(f"{PEOPLE_API}/people/me/connections", headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()
        for person in data.get("connections", []):
            for entry in person.get("clientData", []):
                if entry.get("key") == MARKER_KEY and entry.get("value") == marker:
                    return person["resourceName"]
        page_token = data.get("nextPageToken")
        if not page_token:
            return None


async def _sync_to_google_once(
    card: Card,
    existing_resource: str | None,
    person_marker: str | None = None,
) -> str | None:
    """Single create/update attempt — re-fetches token, body, and etag fresh
    each call so a retry never reuses a stale etag from a prior attempt."""
    token = await _get_access_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = _build_person_body(card)
    if person_marker:
        body["clientData"] = [{"key": MARKER_KEY, "value": person_marker}]

    async with httpx.AsyncClient(timeout=30.0) as client:
        if card.my_company_labels:
            labels = sorted({label for label in card.my_company_labels if label})
            group_resources = []
            for label in labels:
                group_resource = await _get_group_resource(client, headers, label)
                if group_resource:
                    group_resources.append(group_resource)
            if group_resources:
                # Keep the contact in the default "My Contacts" view as well —
                # memberships[] replaces the whole set, it doesn't add to it.
                body["memberships"] = [
                    {"contactGroupMembership": {"contactGroupResourceName": "contactGroups/myContacts"}},
                    *[{"contactGroupMembership": {"contactGroupResourceName": r}} for r in group_resources],
                ]

        # Decide which contact to update, if any. Google's People API rejects
        # updateContact unless the request includes the contact's current
        # etag ("Request must set person.etag..."), so fetch it first — which
        # also tells us whether the linked contact still exists.
        resource, etag = existing_resource, None
        if resource:
            etag = await _get_etag(client, headers, resource)
            if etag is None:
                resource = None
        if resource is None and person_marker:
            found = await _find_contact_by_marker(client, headers, person_marker)
            if found:
                etag = await _get_etag(client, headers, found)
                if etag is not None:
                    resource = found
                    if existing_resource:
                        logger.info("Relinked Google contact %s -> %s via marker", existing_resource, found)
        if resource is None and existing_resource:
            raise GoogleContactGoneError(existing_resource)

        if resource:
            body["etag"] = etag

            # Any field listed in updatePersonFields but absent from the body
            # gets cleared server-side. Every field in the body is listed
            # (computed from body.keys() so a new field added to
            # _build_person_body can't be silently dropped on update), plus
            # the app-owned fields so an emptied one is cleared in Google.
            update_fields = ",".join(dict.fromkeys(
                [k for k in body.keys() if k != "etag"] + list(_ALWAYS_REPLACED_FIELDS)
            ))

            # Update existing contact
            resp = await client.patch(
                f"{PEOPLE_API}/{resource}:updateContact",
                headers=headers,
                json=body,
                params={"updatePersonFields": update_fields},
            )
        else:
            resp = await client.post(
                f"{PEOPLE_API}/people:createContact",
                headers=headers,
                json=body,
            )

        if resp.is_error:
            raise httpx.HTTPStatusError(
                f"{resp.status_code} {resp.reason_phrase} for {resp.request.url}: {resp.text}",
                request=resp.request,
                response=resp,
            )
        result = resp.json()
        resource_name = result.get("resourceName", "")
        logger.info("Google contact synced: %s", resource_name)

        # Upload person photo if available
        if card.images.person_photo and resource_name:
            await _upload_photo(client, headers, resource_name, card.images.person_photo)

        return resource_name


async def delete_contact(resource: str) -> None:
    """Delete a Google contact. A contact that is already gone counts as done."""
    token = await _get_access_token()
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.delete(
            f"{PEOPLE_API}/{resource}:deleteContact",
            headers={"Authorization": f"Bearer {token}"},
        )
    if resp.status_code == 404:
        return
    resp.raise_for_status()


async def _upload_photo(
    client: httpx.AsyncClient,
    headers: dict,
    resource_name: str,
    photo_b64: str,
) -> None:
    """Upload a contact photo to Google Contacts."""
    import base64
    try:
        resp = await client.patch(
            f"{PEOPLE_API}/{resource_name}:updateContactPhoto",
            headers=headers,
            json={"photoBytes": photo_b64},
        )
        resp.raise_for_status()
        logger.info("Uploaded photo for %s", resource_name)
    except Exception:
        logger.exception("Failed to upload Google contact photo")


async def search_google_contacts(query: str) -> list[dict]:
    """Search Google Contacts by name/email/phone."""
    if not settings.google_refresh_token:
        return []

    token = await _get_access_token()
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{PEOPLE_API}/people:searchContacts",
            headers=headers,
            params={
                "query": query,
                "readMask": "names,emailAddresses,phoneNumbers,organizations",
                "pageSize": 10,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    results = []
    for item in data.get("results", []):
        person = item.get("person", {})
        names = person.get("names", [{}])
        orgs = person.get("organizations", [{}])
        emails = person.get("emailAddresses", [{}])
        phones = person.get("phoneNumbers", [{}])
        results.append({
            "id": person.get("resourceName", ""),
            "name": names[0].get("displayName", "") if names else "",
            "company": orgs[0].get("name", "") if orgs else "",
            "email": emails[0].get("value", "") if emails else "",
            "phone": phones[0].get("value", "") if phones else "",
            "source": "google",
        })
    return results
