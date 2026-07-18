"""
Export router — user-initiated card export to external destinations.

POST /api/v2/export
  Body: { card_external_ids: [...], destinations: ["odoo", "google_contacts"] }
  Returns: { results: [{ card_external_id, destination, result, error_message }] }
"""
from __future__ import annotations

import logging
import mimetypes
import os
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import verify_api_key
from app.db.models import (
    Card,
    CardMyCompany,
    CardSyncHistory,
    ContactDetail,
    Organization,
    OrganizationName,
    Person,
    PersonName,
    Position,
    PositionDetail,
)
from app.db.session import get_db
from app.schemas.api import ExportRequest, ExportResponse, ExportResultItem
from app.services.csv_export import format_google_csv, format_odoo_csv
from app.services.legacy_card import build_legacy_card

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v2/export",
    tags=["export"],
    dependencies=[Depends(verify_api_key)],
)


async def _load_full_card(db: AsyncSession, card_ext_id: str) -> Card | None:
    return await db.scalar(
        select(Card)
        .where(Card.external_id == card_ext_id, Card.deleted_at.is_(None))
        .options(
            selectinload(Card.sides),
            selectinload(Card.person).selectinload(Person.names),
            selectinload(Card.person).selectinload(Person.contact_details),
            selectinload(Card.person).selectinload(Person.positions)
                .selectinload(Position.details),
            selectinload(Card.person).selectinload(Person.positions)
                .selectinload(Position.organization)
                .selectinload(Organization.names),
            selectinload(Card.my_company_links).selectinload(CardMyCompany.my_company),
        )
    )


async def _export_one(
    db: AsyncSession,
    card: Card,
    destination: str,
) -> ExportResultItem:
    """Run one export and persist the result to CardSyncHistory."""
    ext_id = card.external_id
    person = card.person
    contact_details = person.contact_details
    positions = person.positions

    result = "error"
    error_message = None

    try:
        legacy = build_legacy_card(card, person, contact_details, positions)

        if destination == "odoo":
            from app.services.odoo_sync import sync_to_odoo
            existing_odoo_id = card.odoo_partner_id
            odoo_id = await sync_to_odoo(legacy, existing_odoo_id)
            if odoo_id:
                result = "updated" if existing_odoo_id else "created"
                card.odoo_partner_id = odoo_id
                card.odoo_sync_at = datetime.utcnow()
            else:
                result = "error"
                error_message = "sync_to_odoo returned None"

        elif destination == "google_contacts":
            from app.services.google_contacts import sync_to_google
            existing_resource = person.google_resource
            resource_name = await sync_to_google(legacy, existing_resource)
            if resource_name:
                result = "updated" if existing_resource else "created"
                person.google_resource = resource_name
                card.google_sync_at = datetime.utcnow()
            else:
                result = "error"
                error_message = "sync_to_google returned None"

        else:
            result = "error"
            error_message = f"Unknown destination: {destination}"

    except Exception as exc:
        logger.exception("Export failed for card %s → %s", ext_id, destination)
        result = "error"
        error_message = str(exc)

    db.add(CardSyncHistory(
        card_id=card.id,
        destination=destination,
        result=result,
        error_message=error_message,
    ))

    return ExportResultItem(
        card_external_id=ext_id,
        destination=destination,
        result=result,
        error_message=error_message,
    )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("", response_model=ExportResponse)
async def run_export(body: ExportRequest, db: AsyncSession = Depends(get_db)):
    """
    Export the specified cards to the chosen destinations.
    Runs each (card, destination) pair, records the result, and returns a summary.
    Cards or destinations that error do not block the rest.
    """
    results: List[ExportResultItem] = []

    for ext_id in body.card_external_ids:
        card = await _load_full_card(db, ext_id)
        if not card:
            for dest in body.destinations:
                results.append(ExportResultItem(
                    card_external_id=ext_id,
                    destination=dest,
                    result="error",
                    error_message="Card not found",
                ))
            continue

        for dest in body.destinations:
            item = await _export_one(db, card, dest)
            results.append(item)

    return ExportResponse(results=results)


@router.get("/csv")
async def export_csv(
    card_ids: str = Query(..., description="Comma-separated card external IDs"),
    format: str = Query(..., description="odoo or google_contacts"),
    db: AsyncSession = Depends(get_db),
):
    """
    Download a CSV of the requested cards formatted for Odoo or Google Contacts.

    GET /api/v2/export/csv?card_ids=abc,def&format=odoo
    GET /api/v2/export/csv?card_ids=abc,def&format=google_contacts
    """
    if format not in ("odoo", "google_contacts"):
        raise HTTPException(status_code=400, detail="format must be 'odoo' or 'google_contacts'")

    ext_ids = [cid.strip() for cid in card_ids.split(",") if cid.strip()]
    if not ext_ids:
        raise HTTPException(status_code=400, detail="card_ids must not be empty")

    # Load cards
    legacy_cards = []
    for ext_id in ext_ids:
        db_card = await _load_full_card(db, ext_id)
        if db_card is None:
            continue  # silently skip missing cards
        legacy = build_legacy_card(
            db_card,
            db_card.person,
            db_card.person.contact_details,
            db_card.person.positions,
        )
        legacy_cards.append(legacy)

    if format == "odoo":
        csv_text = format_odoo_csv(legacy_cards)
        filename = "contacts_odoo.csv"
    else:
        csv_text = format_google_csv(legacy_cards)
        filename = "contacts_google.csv"

    return Response(
        content=csv_text.encode("utf-8-sig"),  # utf-8-sig adds BOM for Excel compat
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/image/{card_id}/{side}")
async def export_image(
    card_id: str,
    side: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Download a card image named <primary_name>_front.jpg or <primary_name>_back.jpg.

    side must be "front" or "back".
    Returns 404 if card not found or that side has no image.
    """
    from app.config import settings

    if side not in ("front", "back"):
        raise HTTPException(status_code=400, detail="side must be 'front' or 'back'")

    db_card = await _load_full_card(db, card_id)
    if db_card is None:
        raise HTTPException(status_code=404, detail="Card not found")

    # side_order: 0 = front, 1 = back
    side_order = 0 if side == "front" else 1
    card_side = next((s for s in db_card.sides if s.side_order == side_order), None)
    if card_side is None:
        raise HTTPException(status_code=404, detail=f"No {side} image for this card")

    # Resolve relative image path to absolute file path
    image_path = settings.images_path / card_side.image_path
    # Prevent path traversal: ensure resolved path stays inside images_path
    try:
        resolved = image_path.resolve()
        resolved.relative_to(settings.images_path.resolve())
    except ValueError:
        raise HTTPException(status_code=404, detail="Image file not found on disk")

    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="Image file not found on disk")

    # Build download filename from primary name (use DB model directly)
    db_names = [n for n in db_card.person.names if n.is_current]
    primary_name_obj = next((n for n in db_names if n.name_type == "primary"), None)
    primary = primary_name_obj.full_name if primary_name_obj else (db_names[0].full_name if db_names else "card")
    # Sanitize for use as filename (remove slashes, null bytes)
    safe_name = primary.replace("/", "_").replace("\x00", "")
    ext = os.path.splitext(card_side.image_path)[1] or ".jpg"
    download_filename = f"{safe_name}_{side}{ext}"

    mime_type, _ = mimetypes.guess_type(str(image_path))
    mime_type = mime_type or "image/jpeg"

    return FileResponse(
        path=str(resolved),
        media_type=mime_type,
        filename=download_filename,
    )
