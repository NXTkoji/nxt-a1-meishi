from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends

from app.auth import verify_api_key
from app.models.card import Card
from app.models.responses import ConfirmResponse
from app.services.odoo_sync import sync_to_odoo
from app.services.google_contacts import sync_to_google
from app.services.onedrive import upload_to_onedrive

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", dependencies=[Depends(verify_api_key)])


@router.post("/confirm", response_model=ConfirmResponse)
async def confirm_card(card: Card):
    """Save confirmed card data to Odoo, Google Contacts, and OneDrive."""
    errors: list[str] = []
    odoo_id = None
    google_id = None
    onedrive_urls: dict[str, str] = {}

    # Determine if linking to existing contact
    existing_odoo_id = None
    existing_google_resource = None
    if card.match.is_existing and card.match.matched_contact_id:
        if card.match.match_source == "odoo":
            existing_odoo_id = int(card.match.matched_contact_id)
        elif card.match.match_source == "google":
            existing_google_resource = card.match.matched_contact_id

    # Run all three sync operations concurrently
    odoo_task = _safe_sync("Odoo", sync_to_odoo(card, existing_odoo_id))
    google_task = _safe_sync("Google", sync_to_google(card, existing_google_resource))
    onedrive_task = _safe_sync("OneDrive", upload_to_onedrive(card))

    results = await asyncio.gather(odoo_task, google_task, onedrive_task)

    # Unpack results
    odoo_result, odoo_err = results[0]
    google_result, google_err = results[1]
    onedrive_result, onedrive_err = results[2]

    if odoo_err:
        errors.append(f"Odoo: {odoo_err}")
    else:
        odoo_id = odoo_result

    if google_err:
        errors.append(f"Google: {google_err}")
    else:
        google_id = google_result

    if onedrive_err:
        errors.append(f"OneDrive: {onedrive_err}")
    else:
        onedrive_urls = onedrive_result or {}

    status = "ok" if not errors else "partial"
    return ConfirmResponse(
        status=status,
        odoo_id=odoo_id,
        google_id=google_id,
        onedrive_urls=onedrive_urls,
        errors=errors,
    )


async def _safe_sync(name: str, coro) -> tuple:
    """Run a sync coroutine and catch exceptions."""
    try:
        result = await coro
        return (result, None)
    except Exception as e:
        logger.exception("Failed to sync to %s", name)
        return (None, str(e))
