"""
Occasions router — group cards by event (date + location).
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import verify_api_key
from app.db.models import Card, Occasion
from app.db.session import get_db
from app.schemas.api import OccasionCreate, OccasionOut, OccasionUpdate

router = APIRouter(
    prefix="/api/v2/occasions",
    tags=["occasions"],
    dependencies=[Depends(verify_api_key)],
)


def _card_count_subquery():
    """Correlated scalar count of live (not soft-deleted) cards for an Occasion row.

    Single owner of the "how many cards use this occasion" rule, so list_occasions
    and update_occasion cannot disagree on it. Correlates on Occasion.id, so it
    must be selected alongside an Occasion in the same query.
    """
    return (
        select(func.count(Card.id))
        .where(Card.occasion_id == Occasion.id, Card.deleted_at.is_(None))
        .scalar_subquery()
    )


@router.get("", response_model=List[OccasionOut])
async def list_occasions(db: AsyncSession = Depends(get_db)):
    # Correlated count of live cards per occasion — one query, no N+1.
    rows = (await db.execute(
        select(Occasion, _card_count_subquery()).order_by(
            Occasion.event_date.desc().nullslast(), Occasion.created_at.desc()
        )
    )).all()

    out = []
    for occ, count in rows:
        item = OccasionOut.model_validate(occ)
        item.card_count = count
        out.append(item)
    return out


@router.post("", response_model=OccasionOut, status_code=status.HTTP_201_CREATED)
async def create_occasion(body: OccasionCreate, db: AsyncSession = Depends(get_db)):
    # card_count is correctly left at its OccasionOut default of 0 here: a
    # brand-new occasion cannot have any cards linked to it yet.
    occ = Occasion(**body.model_dump())
    db.add(occ)
    await db.flush()
    return occ


@router.patch("/{occasion_id}", response_model=OccasionOut)
async def update_occasion(
    occasion_id: int,
    body: OccasionUpdate,
    db: AsyncSession = Depends(get_db),
):
    occ = await db.get(Occasion, occasion_id)
    if not occ:
        raise HTTPException(404, "Occasion not found")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(occ, field, value)
    await db.flush()

    # Unlike create, an existing occasion may already have cards — report the
    # real count rather than leaving the OccasionOut default of 0, or a renamed
    # occasion with cards would look empty in the response.
    count = await db.scalar(
        select(_card_count_subquery()).select_from(Occasion).where(Occasion.id == occasion_id)
    )
    out = OccasionOut.model_validate(occ)
    out.card_count = count or 0
    return out


@router.delete("/{occasion_id}", status_code=204)
async def delete_occasion(occasion_id: int, db: AsyncSession = Depends(get_db)):
    occ = await db.get(Occasion, occasion_id)
    if not occ:
        raise HTTPException(404, "Occasion not found")

    # Stamp the name onto every card that points here BEFORE deleting, so the card
    # keeps a readable record. SQLAlchemy will null out occasion_id as part of the
    # delete; occasion_label is a different column and survives that.
    # Always overwrite: the label should reflect the most recently deleted occasion,
    # even if the card already carried an older label from a previous deletion.
    await db.execute(
        update(Card)
        .where(Card.occasion_id == occasion_id)
        .values(occasion_label=occ.name)
        .execution_options(synchronize_session=False)
    )

    await db.delete(occ)
