"""
Cards router — browse and manage confirmed cards.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import verify_api_key
from app.db.models import Card, CardSide, Person, PersonName
from app.db.session import get_db
from app.schemas.api import CardListItem, CardOut, CardSideOut
from app.services import image_store

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
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
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

    rows = (await db.execute(stmt)).scalars().all()

    async def _get_name(person_id: int, lang: Optional[str]) -> Optional[str]:
        base = (PersonName.person_id == person_id, PersonName.is_current == True)  # noqa: E712
        if lang:
            # Prefer the per-card language (prefix match: "zh" matches "zh-TW" etc.)
            preferred = await db.scalar(
                select(PersonName.full_name)
                .where(*base, PersonName.language.like(f"{lang}%"))
                .order_by(PersonName.id.asc())
                .limit(1)
            )
            if preferred:
                return preferred
        # Fallback: first available current name
        return await db.scalar(
            select(PersonName.full_name)
            .where(*base)
            .order_by(PersonName.id.asc())
            .limit(1)
        )

    items = []
    for card in rows:
        name = await _get_name(card.person_id, card.display_name_language)
        front = next((s.image_path for s in sorted(card.sides, key=lambda s: s.side_order)), None)
        items.append(CardListItem(
            id=card.id,
            external_id=card.external_id,
            person_id=card.person_id,
            received_date=card.received_date,
            sync_status=card.sync_status,
            created_at=card.created_at,
            person_name=name,
            front_image_path=front,
        ))
    return items


async def _load_card(db: AsyncSession, card_ext_id: str) -> Card:
    card = await db.scalar(
        select(Card)
        .where(Card.external_id == card_ext_id, Card.deleted_at.is_(None))
        .options(selectinload(Card.sides))
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
    return out


@router.patch("/{card_ext_id}", response_model=CardOut)
async def update_card(
    card_ext_id: str,
    body: dict,
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
    await db.flush()
    person_ext_id = await db.scalar(select(Person.external_id).where(Person.id == card.person_id))
    out = CardOut.model_validate(card)
    out.person_external_id = person_ext_id
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
