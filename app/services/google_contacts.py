from __future__ import annotations

import logging

import httpx

from app.config import settings
from app.models.card import Card

logger = logging.getLogger(__name__)

TOKEN_URL = "https://oauth2.googleapis.com/token"
PEOPLE_API = "https://people.googleapis.com/v1"


async def _get_access_token() -> str:
    """Exchange refresh token for a fresh access token."""
    async with httpx.AsyncClient() as client:
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

    # Organizations
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

    # Addresses
    addresses = []
    for a in person.addresses:
        addr: dict = {"type": a.type or "work"}
        if a.full:
            addr["formattedValue"] = a.full
        if a.street:
            addr["streetAddress"] = a.street
        if a.city:
            addr["city"] = a.city
        if a.state:
            addr["region"] = a.state
        if a.postal_code:
            addr["postalCode"] = a.postal_code
        if a.country:
            addr["country"] = a.country
        if a.country_code:
            addr["countryCode"] = a.country_code
        addresses.append(addr)
    if addresses:
        body["addresses"] = addresses

    # Website
    if person.website:
        body["urls"] = [{"value": person.website, "type": "work"}]

    # Notes (received date + card notes)
    note_parts = []
    if card.received_date:
        note_parts.append(f"Card received: {card.received_date}")
    if card.notes:
        note_parts.append(card.notes)
    if note_parts:
        body["biographies"] = [{"value": "\n".join(note_parts), "contentType": "TEXT_PLAIN"}]

    # Met As (which of the user's businesses this contact was met through)
    if card.my_company_labels:
        labels = sorted({label for label in card.my_company_labels if label})
        if labels:
            body["userDefined"] = [{"key": "Met As", "value": ", ".join(labels)}]

    return body


async def sync_to_google(card: Card, existing_resource: str | None = None) -> str | None:
    """Create or update a Google Contact. Returns resource name (e.g. 'people/c123')."""
    if not settings.google_refresh_token:
        logger.warning("Google Contacts not configured, skipping")
        return None

    token = await _get_access_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = _build_person_body(card)

    async with httpx.AsyncClient() as client:
        if existing_resource:
            # Google's People API rejects updateContact unless the request
            # includes the contact's current etag ("Request must set
            # person.etag..."), so fetch it first.
            etag_resp = await client.get(
                f"{PEOPLE_API}/{existing_resource}",
                headers=headers,
                params={"personFields": "metadata"},
            )
            etag_resp.raise_for_status()
            body["etag"] = etag_resp.json()["etag"]

            # Update existing contact
            resp = await client.patch(
                f"{PEOPLE_API}/{existing_resource}:updateContact",
                headers=headers,
                json=body,
                params={"updatePersonFields": "names,nicknames,organizations,phoneNumbers,emailAddresses,addresses,urls,biographies,userDefined"},
            )
        else:
            resp = await client.post(
                f"{PEOPLE_API}/people:createContact",
                headers=headers,
                json=body,
            )

        resp.raise_for_status()
        result = resp.json()
        resource_name = result.get("resourceName", "")
        logger.info("Google contact synced: %s", resource_name)

        # Upload person photo if available
        if card.images.person_photo and resource_name:
            await _upload_photo(client, headers, resource_name, card.images.person_photo)

        return resource_name


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
