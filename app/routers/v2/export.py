"""
Export router — user-initiated card export to external destinations.

POST /api/v2/export
  Body: { card_external_ids: [...], destinations: ["odoo", "google_contacts"] }
  Returns: { results: [{ card_external_id, destination, result, error_message }] }
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import verify_api_key
from app.db.models import (
    Card,
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

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v2/export",
    tags=["export"],
    dependencies=[Depends(verify_api_key)],
)


# ---------------------------------------------------------------------------
# Bridge: convert v2 DB Card → legacy models.card.Card for sync services
# ---------------------------------------------------------------------------

def _build_legacy_card(
    db_card: Card,
    person: Person,
    contact_details: list[ContactDetail],
    positions: list[Position],
):
    """Convert v2 DB objects into the legacy app.models.card.Card Pydantic model."""
    from app.models.card import (
        Address,
        Card as LegacyCard,
        Email,
        Person as LegacyPerson,
        PersonName as LegacyName,
        Phone,
        Position as LegacyPosition,
        Social,
    )

    names = [
        LegacyName(
            value=n.full_name,
            language=n.language,
            type=n.name_type,
        )
        for n in person.names
        if n.is_current
    ]

    legacy_positions = []
    for pos in positions:
        org_name_ja = next(
            (on.name for on in pos.organization.names if on.language == "ja" and on.is_current),
            next((on.name for on in pos.organization.names if on.is_current), ""),
        )
        org_name_en = next(
            (on.name for on in pos.organization.names if on.language == "en" and on.is_current),
            "",
        )
        title_ja = next(
            (pd.title or "" for pd in pos.details if pd.language == "ja"), ""
        )
        title_en = next(
            (pd.title or "" for pd in pos.details if pd.language == "en"), ""
        )
        dept = next(
            (pd.department or "" for pd in pos.details if pd.language == "ja"),
            next((pd.department or "" for pd in pos.details), ""),
        )
        legacy_positions.append(LegacyPosition(
            company=org_name_ja,
            company_english=org_name_en,
            title=title_ja,
            title_english=title_en,
            department=dept,
        ))

    phones, emails, addresses = [], [], []
    website = ""
    social = Social()
    for cd in contact_details:
        t = cd.detail_type
        if t in ("phone_work", "phone_mobile", "phone_fax"):
            kind = t.replace("phone_", "")
            phones.append(Phone(value=cd.value, type=kind, label=cd.label or ""))
        elif t in ("email_work", "email_personal"):
            kind = t.replace("email_", "")
            emails.append(Email(value=cd.value, type=kind))
        elif t in ("address_work", "address_home"):
            kind = t.replace("address_", "")
            addresses.append(Address(type=kind, full=cd.value))
        elif t == "url_website":
            website = cd.value
        elif t == "social_wechat":
            social.wechat = cd.value
        elif t == "social_line":
            social.line = cd.value
        elif t == "social_linkedin":
            social.linkedin = cd.value

    legacy_person = LegacyPerson(
        names=names,
        positions=legacy_positions,
        phones=phones,
        emails=emails,
        addresses=addresses,
        website=website,
        social=social,
    )

    return LegacyCard(person=legacy_person)


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
        legacy = _build_legacy_card(card, person, contact_details, positions)

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
