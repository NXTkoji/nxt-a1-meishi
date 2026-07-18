"""
Cards router — browse and manage confirmed cards.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import verify_api_key
from app.db.models import Card, CardMyCompany, CardSide, Person, PersonName
from app.db.session import get_db
from app.schemas.api import CardListItem, CardOut, CardSideOut
from app.services import image_store
from app.services.contact_sync import auto_sync_card

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v2/cards",
    tags=["cards"],
    dependencies=[Depends(verify_api_key)],
)


@router.get("", response_model=List[CardListItem])
async def list_cards(
    person_id: Optional[int] = Query(None),
    occasion_id: Optional[int] = Query(None),
    my_company_id: Optional[int] = Query(None, description="Filter by Met As (my company) ID"),
    q: Optional[str] = Query(None, description="Full-text search across names, org, contacts, titles"),
    year: Optional[int] = Query(None),
    month: Optional[str] = Query(None, description="YYYY-MM"),
    date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    not_exported: bool = Query(False, description="Only cards with no sync history to odoo or google_contacts"),
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    from datetime import date as date_type
    from sqlalchemy import exists, or_, and_, extract
    from app.db.models import (
        CardMyCompany, ContactDetail, Organization, OrganizationName,
        PersonName as PersonNameModel, Position, PositionDetail,
        CardSyncHistory,
    )

    stmt = (
        select(Card)
        .where(Card.deleted_at.is_(None))
        .order_by(Card.created_at.desc())
        .limit(limit)
        .offset(offset)
        .options(selectinload(Card.sides))
    )

    if person_id:
        stmt = stmt.where(Card.person_id == person_id)
    if occasion_id:
        stmt = stmt.where(Card.occasion_id == occasion_id)
    if my_company_id:
        mc_subq = select(CardMyCompany.card_id).where(CardMyCompany.my_company_id == my_company_id)
        stmt = stmt.where(Card.id.in_(mc_subq))

    # Date filters (prefer received_date, fall back to created_at)
    if date:
        d = date_type.fromisoformat(date)
        stmt = stmt.where(
            or_(
                func.date(Card.received_date) == d,
                and_(Card.received_date.is_(None), func.date(Card.created_at) == d),
            )
        )
    elif month:
        y, m = int(month[:4]), int(month[5:7])
        stmt = stmt.where(
            or_(
                and_(
                    extract('year', Card.received_date) == y,
                    extract('month', Card.received_date) == m,
                ),
                and_(
                    Card.received_date.is_(None),
                    extract('year', Card.created_at) == y,
                    extract('month', Card.created_at) == m,
                ),
            )
        )
    elif year:
        stmt = stmt.where(
            or_(
                extract('year', Card.received_date) == year,
                and_(Card.received_date.is_(None), extract('year', Card.created_at) == year),
            )
        )

    # not_exported: no successful sync history to odoo or google_contacts
    if not_exported:
        exported_subq = (
            select(CardSyncHistory.card_id)
            .where(
                CardSyncHistory.card_id == Card.id,
                CardSyncHistory.destination.in_(["odoo", "google_contacts"]),
                CardSyncHistory.result.in_(["created", "updated"]),
            )
        )
        stmt = stmt.where(~exists(exported_subq))

    # Full-text search across person data
    if q:
        like = f"%{q}%"
        text_subq = (
            select(PersonNameModel.person_id)
            .where(
                PersonNameModel.person_id == Card.person_id,
                PersonNameModel.is_current == True,  # noqa: E712
                PersonNameModel.full_name.ilike(like),
            )
        )
        contact_subq = (
            select(ContactDetail.person_id)
            .where(
                ContactDetail.person_id == Card.person_id,
                ContactDetail.value.ilike(like),
            )
        )
        pos_subq = (
            select(PositionDetail.position_id)
            .join(Position, PositionDetail.position_id == Position.id)
            .where(
                Position.person_id == Card.person_id,
                or_(
                    PositionDetail.title.ilike(like),
                    PositionDetail.department.ilike(like),
                ),
            )
        )
        org_subq = (
            select(OrganizationName.org_id)
            .join(Organization, OrganizationName.org_id == Organization.id)
            .join(Position, Position.org_id == Organization.id)
            .where(
                Position.person_id == Card.person_id,
                OrganizationName.is_current == True,  # noqa: E712
                OrganizationName.name.ilike(like),
            )
        )
        stmt = stmt.where(
            or_(
                exists(text_subq),
                exists(contact_subq),
                exists(pos_subq),
                exists(org_subq),
            )
        )

    rows = (await db.execute(stmt)).scalars().all()

    # Fetch sync history for all returned cards in one query
    card_ids = [c.id for c in rows]
    sync_rows: list = []
    if card_ids:
        sh_stmt = (
            select(CardSyncHistory)
            .where(
                CardSyncHistory.card_id.in_(card_ids),
                CardSyncHistory.result.in_(["created", "updated"]),
            )
            .order_by(CardSyncHistory.synced_at.desc())
        )
        sync_rows = (await db.execute(sh_stmt)).scalars().all()

    # Build map: card_id → set of destinations with successful sync
    synced_map: dict[int, set[str]] = {}
    for sh in sync_rows:
        synced_map.setdefault(sh.card_id, set()).add(sh.destination)

    async def _get_name(pid: int, lang: Optional[str]) -> Optional[str]:
        base = (PersonName.person_id == pid, PersonName.is_current == True)  # noqa: E712
        if lang:
            preferred = await db.scalar(
                select(PersonName.full_name)
                .where(*base, PersonName.language.like(f"{lang}%"))
                .order_by(PersonName.id.asc())
                .limit(1)
            )
            if preferred:
                return preferred
        return await db.scalar(
            select(PersonName.full_name)
            .where(*base)
            .order_by(PersonName.id.asc())
            .limit(1)
        )

    items = []
    for card in rows:
        name = await _get_name(card.person_id, card.display_name_language)
        front = next(
            (s.image_path for s in sorted(card.sides, key=lambda s: s.side_order)), None
        )
        items.append(CardListItem(
            id=card.id,
            external_id=card.external_id,
            person_id=card.person_id,
            received_date=card.received_date,
            sync_status=card.sync_status,
            created_at=card.created_at,
            person_name=name,
            front_image_path=front,
            synced_destinations=sorted(synced_map.get(card.id, set())),
        ))
    return items


async def _load_card(db: AsyncSession, card_ext_id: str) -> Card:
    card = await db.scalar(
        select(Card)
        .where(Card.external_id == card_ext_id, Card.deleted_at.is_(None))
        .options(selectinload(Card.sides), selectinload(Card.my_company_links))
    )
    if not card:
        raise HTTPException(404, "Card not found")
    return card


@router.get("/{card_ext_id}", response_model=CardOut)
async def get_card(card_ext_id: str, db: AsyncSession = Depends(get_db)):
    card = await _load_card(db, card_ext_id)
    person_ext_id = await db.scalar(select(Person.external_id).where(Person.id == card.person_id))
    out = CardOut.model_validate(card)
    out.person_external_id = person_ext_id
    out.my_company_ids = [link.my_company_id for link in card.my_company_links]
    return out


@router.patch("/{card_ext_id}", response_model=CardOut)
async def update_card(
    card_ext_id: str,
    body: dict,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    from datetime import date as date_type
    card = await _load_card(db, card_ext_id)
    if "received_date" in body:
        val = body["received_date"]
        card.received_date = date_type.fromisoformat(val) if val else None
    if "notes" in body:
        card.notes = body["notes"]
    if "display_name_language" in body:
        val = body["display_name_language"]
        card.display_name_language = val if val else None
    if "occasion_id" in body:
        card.occasion_id = body["occasion_id"] or None
    if "my_company_ids" in body:
        from sqlalchemy import delete as sa_delete
        await db.execute(sa_delete(CardMyCompany).where(CardMyCompany.card_id == card.id))
        for mc_id in body["my_company_ids"]:
            db.add(CardMyCompany(card_id=card.id, my_company_id=mc_id))
    await db.flush()
    person_ext_id = await db.scalar(select(Person.external_id).where(Person.id == card.person_id))
    mc_ids = (await db.execute(
        select(CardMyCompany.my_company_id).where(CardMyCompany.card_id == card.id)
    )).scalars().all()
    out = CardOut.model_validate(card)
    out.person_external_id = person_ext_id
    out.my_company_ids = list(mc_ids)
    background_tasks.add_task(auto_sync_card, card.id)
    return out


@router.post("/{card_ext_id}/sides", response_model=CardSideOut, status_code=201)
async def add_card_side(
    card_ext_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload a new image and append it as the next side of a card."""
    card = await _load_card(db, card_ext_id)

    # Determine next side_order
    existing = (await db.execute(
        select(CardSide.side_order).where(CardSide.card_id == card.id)
    )).scalars().all()
    side_order = max(existing, default=-1) + 1

    data = await file.read()
    rel_path, filename, sha, w, h = image_store.save_permanent_image(
        card_ext_id, side_order, data
    )

    side = CardSide(
        card_id=card.id,
        side_order=side_order,
        image_path=rel_path,
        image_filename=filename,
        image_hash=sha,
        width_px=w,
        height_px=h,
    )
    db.add(side)
    await db.flush()
    await db.refresh(side)
    return side


@router.post("/{card_ext_id}/sides/{side_order}/promote", status_code=204)
async def promote_card_side_to_front(
    card_ext_id: str,
    side_order: int,
    db: AsyncSession = Depends(get_db),
):
    """Promote a side to side_order 0 (Front). Sides that were before it shift right by 1."""
    if side_order == 0:
        return  # already front
    card = await _load_card(db, card_ext_id)
    sides = (await db.execute(
        select(CardSide).where(CardSide.card_id == card.id)
    )).scalars().all()
    target = next((s for s in sides if s.side_order == side_order), None)
    if not target:
        raise HTTPException(404, "Side not found")
    # Move all to negative temps to avoid unique constraint during reassignment
    original_orders = {s.id: s.side_order for s in sides}
    for s in sides:
        s.side_order = -(s.side_order + 1)
    await db.flush()
    # Assign final values: promoted → 0, those before it shift +1, those after unchanged
    for s in sides:
        orig = original_orders[s.id]
        if orig == side_order:
            s.side_order = 0
        elif orig < side_order:
            s.side_order = orig + 1
        else:
            s.side_order = orig
    await db.flush()


@router.delete("/{card_ext_id}/sides/{side_order}", status_code=204)
async def delete_card_side(
    card_ext_id: str,
    side_order: int,
    db: AsyncSession = Depends(get_db),
):
    """Remove one image side from a card (not allowed if it's the only side)."""
    card = await _load_card(db, card_ext_id)
    side = await db.scalar(
        select(CardSide).where(CardSide.card_id == card.id, CardSide.side_order == side_order)
    )
    if not side:
        raise HTTPException(404, "Side not found")
    total = await db.scalar(
        select(func.count()).select_from(CardSide).where(CardSide.card_id == card.id)
    )
    if total <= 1:
        raise HTTPException(400, "Cannot delete the only image of a card")
    await db.delete(side)


@router.delete("/{card_ext_id}", status_code=204)
async def delete_card(card_ext_id: str, db: AsyncSession = Depends(get_db)):
    from datetime import datetime
    card = await db.scalar(select(Card).where(Card.external_id == card_ext_id, Card.deleted_at.is_(None)))
    if not card:
        raise HTTPException(404, "Card not found")
    card.deleted_at = datetime.utcnow()
