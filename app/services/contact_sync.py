"""Orchestrates pushing one card to Google Contacts and recording the
result. Used both by the manual /api/v2/export endpoint and by the
automatic background-task triggers on card create/update and person edits.
"""
from __future__ import annotations

import asyncio
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
from app.services.google_contacts import delete_contact, sync_to_google
from app.services.legacy_card import build_legacy_card

logger = logging.getLogger(__name__)

# One lock per person id, so background pushes for the same Google contact
# never overlap. Person edits save field-by-field, so two quick edits can
# queue two syncs at once; run concurrently they would race on the contact's
# etag (the loser fails) or, for a person with no google_resource yet, both
# create a contact and leave a duplicate in Google. Serialized, each sync
# re-reads the DB inside the lock, so the last one always pushes the latest
# state. Entries are never evicted: one small Lock per person is negligible
# in a single-process app with a few hundred contacts.
_person_locks: dict[int, asyncio.Lock] = {}


def _person_lock(person_id: int) -> asyncio.Lock:
    lock = _person_locks.get(person_id)
    if lock is None:
        lock = _person_locks[person_id] = asyncio.Lock()
    return lock


async def sync_card_to_google_contacts(db: AsyncSession, card: Card, legacy) -> tuple[str, str | None]:
    """Push one card's data to Google Contacts. Returns (result, error_message).
    Does not commit — the caller is responsible for committing and recording
    CardSyncHistory, since callers differ in what else they commit alongside it.
    """
    person = card.person
    existing_resource = person.google_resource
    try:
        resource_name = await sync_to_google(legacy, existing_resource, person_marker=person.external_id)
    except Exception as exc:
        logger.exception("Google Contacts sync failed for card %s", card.external_id)
        return "error", str(exc)
    if resource_name:
        result = "updated" if existing_resource else "created"
        person.google_resource = resource_name
        card.google_sync_at = datetime.utcnow()
        return result, None
    return "error", "sync_to_google returned None"


async def auto_delete_google_contacts(resources: list[str]) -> None:
    """Background-task entry point: delete Google contacts left behind by a
    person merge (the merged-away persons' contacts). Never raises — a
    failure is logged and the contact simply stays in Google."""
    for resource in resources:
        try:
            await delete_contact(resource)
            logger.info("Deleted merged-away Google contact %s", resource)
        except Exception:
            logger.exception("auto_delete_google_contacts: failed to delete %s", resource)


async def auto_sync_person(person_id: int) -> None:
    """Background-task entry point for person edits (names, contact details,
    positions, birthday, merge): push the person's contact to Google.

    Google holds ONE contact per person (person.google_resource), but the
    push is built from a card — the card supplies the occasion, notes and
    my-company labels. The newest live card is used, i.e. the person's most
    recent encounter. A person with no live card (created manually) has
    nothing to push and is skipped.
    """
    try:
        async with AsyncSessionLocal() as db:
            card_id = await db.scalar(
                select(Card.id)
                .where(Card.person_id == person_id, Card.deleted_at.is_(None))
                .order_by(Card.created_at.desc(), Card.id.desc())
                .limit(1)
            )
    except Exception:
        # Background task — must never raise.
        logger.exception("auto_sync_person: failed to find a card for person id=%s", person_id)
        return
    if card_id is None:
        logger.info("auto_sync_person: person id=%s has no live card, skipping", person_id)
        return
    await auto_sync_card(card_id)


async def auto_sync_card(card_id: int) -> None:
    """Background-task entry point: push one card to Google Contacts.

    Serialized per person (see _person_locks), so the card's person id is
    looked up first, then the actual sync runs under that person's lock.
    """
    try:
        async with AsyncSessionLocal() as db:
            person_id = await db.scalar(select(Card.person_id).where(Card.id == card_id))
    except Exception:
        # Background task — must never raise.
        logger.exception("auto_sync_card: failed to look up card id=%s", card_id)
        return
    if person_id is None:
        logger.warning("auto_sync_card: card id=%s not found", card_id)
        return
    async with _person_lock(person_id):
        await _sync_card_and_record(card_id)


async def _sync_card_and_record(card_id: int) -> None:
    """Push one card to Google Contacts and record a CardSyncHistory row.

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
