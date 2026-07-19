"""Orchestrates pushing one card to Google Contacts and recording the
result. Used both by the manual /api/v2/export endpoint and by the
automatic background-task triggers on card create/update.
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.engine import AsyncSessionLocal
from app.db.models import (
    Card,
    CardMyCompany,
    CardSyncHistory,
    Organization,
    Person,
    PersonRelationship,
    Position,
)
from app.services.google_contacts import sync_to_google
from app.services.legacy_card import build_legacy_card

logger = logging.getLogger(__name__)


async def sync_card_to_google_contacts(db: AsyncSession, card: Card, legacy) -> tuple[str, str | None]:
    """Push one card's data to Google Contacts. Returns (result, error_message).
    Does not commit — the caller is responsible for committing and recording
    CardSyncHistory, since callers differ in what else they commit alongside it.
    """
    person = card.person
    existing_resource = person.google_resource
    try:
        resource_name = await sync_to_google(legacy, existing_resource)
    except Exception as exc:
        logger.exception("Google Contacts sync failed for card %s", card.external_id)
        return "error", str(exc)
    if resource_name:
        result = "updated" if existing_resource else "created"
        person.google_resource = resource_name
        card.google_sync_at = datetime.utcnow()
        return result, None
    return "error", "sync_to_google returned None"


async def auto_sync_card(card_id: int) -> None:
    """Background-task entry point: push one card to Google Contacts.

    Opens its own DB session — this runs after the request that scheduled
    it has already returned its response, so the request's session may
    already be closed by then.
    """
    async with AsyncSessionLocal() as db:
        card = None
        try:
            card = await db.scalar(
                select(Card)
                .where(Card.id == card_id)
                .options(
                    selectinload(Card.person).selectinload(Person.names),
                    selectinload(Card.person).selectinload(Person.contact_details),
                    selectinload(Card.person).selectinload(Person.positions)
                        .selectinload(Position.details),
                    selectinload(Card.person).selectinload(Person.positions)
                        .selectinload(Position.organization)
                        .selectinload(Organization.names),
                    selectinload(Card.my_company_links).selectinload(CardMyCompany.my_company),
                    selectinload(Card.occasion),
                    selectinload(Card.person).selectinload(Person.relationships_from)
                        .selectinload(PersonRelationship.relationship_type),
                    selectinload(Card.person).selectinload(Person.relationships_from)
                        .selectinload(PersonRelationship.to_person)
                        .selectinload(Person.names),
                )
            )
            if card is None or card.deleted_at is not None:
                logger.warning("auto_sync_card: card id=%s not found or deleted", card_id)
                return

            legacy = build_legacy_card(card, card.person, card.person.contact_details, card.person.positions)
            result, error_message = await sync_card_to_google_contacts(db, card, legacy)

            db.add(CardSyncHistory(
                card_id=card.id,
                destination="google_contacts",
                result=result,
                error_message=error_message,
            ))
            await db.commit()
        except Exception as exc:
            logger.exception("auto_sync_card: unexpected error syncing card id=%s", card_id)
            if card is None:
                # Card was never loaded (e.g. the initial query itself failed) —
                # there's nothing to attach a CardSyncHistory row to.
                return
            # Reset the session in case the failure left a pending transaction
            # in a bad state, then record the failure so it's at least visible.
            # Note: card_id (the function argument) is used here rather than
            # card.id — rollback() expires all attributes on `card`, and
            # re-reading card.id afterward would trigger an implicit lazy
            # reload outside of an awaited context (MissingGreenlet).
            #
            # rollback() itself is inside this try so that a failure here
            # (e.g. a broken connection) is logged and swallowed too — this
            # background task must never raise.
            try:
                await db.rollback()
                db.add(CardSyncHistory(
                    card_id=card_id,
                    destination="google_contacts",
                    result="error",
                    error_message=str(exc),
                ))
                await db.commit()
            except Exception:
                logger.exception(
                    "auto_sync_card: failed to record error CardSyncHistory for card id=%s", card_id
                )
