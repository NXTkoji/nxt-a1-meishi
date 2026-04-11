from __future__ import annotations

import logging
from datetime import datetime

import httpx

from app.config import settings
from app.models.card import Card
from app.utils.image import base64_to_bytes

logger = logging.getLogger(__name__)

TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
GRAPH_API = "https://graph.microsoft.com/v1.0"


async def _get_access_token() -> str:
    """Exchange refresh token for a fresh access token."""
    url = TOKEN_URL.format(tenant=settings.ms_tenant_id)
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, data={
            "client_id": settings.ms_client_id,
            "client_secret": settings.ms_client_secret,
            "refresh_token": settings.ms_refresh_token,
            "grant_type": "refresh_token",
            "scope": "https://graph.microsoft.com/.default",
        })
        resp.raise_for_status()
        return resp.json()["access_token"]


def _build_folder_path(card: Card) -> str:
    """Build OneDrive folder path: BusinessCards/YYYY-MM/PersonName_Date/"""
    person_name = "unknown"
    for n in card.person.names:
        if n.type == "primary":
            person_name = n.value
            break
    if person_name == "unknown" and card.person.names:
        person_name = card.person.names[0].value

    # Sanitize for path
    person_name = person_name.replace("/", "_").replace("\\", "_").replace(":", "_")

    date_str = card.received_date or datetime.now().strftime("%Y-%m-%d")
    month_str = date_str[:7]  # YYYY-MM

    return f"{settings.onedrive_folder}/{month_str}/{person_name}_{date_str}"


async def _upload_file(
    client: httpx.AsyncClient,
    headers: dict,
    folder_path: str,
    filename: str,
    data: bytes,
) -> str:
    """Upload a single file to OneDrive. Returns the web URL."""
    path = f"{GRAPH_API}/me/drive/root:/{folder_path}/{filename}:/content"
    resp = await client.put(
        path,
        headers={**headers, "Content-Type": "application/octet-stream"},
        content=data,
    )
    resp.raise_for_status()
    result = resp.json()
    url = result.get("webUrl", "")
    logger.info("Uploaded %s/%s → %s", folder_path, filename, url)
    return url


async def upload_to_onedrive(card: Card) -> dict[str, str]:
    """Upload card images to OneDrive. Returns dict of {image_type: web_url}."""
    if not settings.ms_refresh_token:
        logger.warning("OneDrive not configured, skipping")
        return {}

    token = await _get_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    folder = _build_folder_path(card)
    urls: dict[str, str] = {}

    async with httpx.AsyncClient() as client:
        if card.images.card_front:
            data = base64_to_bytes(card.images.card_front)
            urls["card_front"] = await _upload_file(client, headers, folder, "front.jpg", data)

        if card.images.card_back:
            data = base64_to_bytes(card.images.card_back)
            urls["card_back"] = await _upload_file(client, headers, folder, "back.jpg", data)

        if card.images.person_photo:
            data = base64_to_bytes(card.images.person_photo)
            urls["person_photo"] = await _upload_file(client, headers, folder, "photo.jpg", data)

    return urls
