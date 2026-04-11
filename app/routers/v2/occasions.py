"""
Occasions router — group cards by event (date + location).
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import verify_api_key
from app.db.models import Occasion
from app.db.session import get_db
from app.schemas.api import OccasionCreate, OccasionOut, OccasionUpdate

router = APIRouter(
    prefix="/api/v2/occasions",
    tags=["occasions"],
    dependencies=[Depends(verify_api_key)],
)


@router.get("", response_model=List[OccasionOut])
async def list_occasions(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(Occasion).order_by(Occasion.event_date.desc().nullslast(), Occasion.created_at.desc())
    )).scalars().all()
    return rows


@router.post("", response_model=OccasionOut, status_code=status.HTTP_201_CREATED)
async def create_occasion(body: OccasionCreate, db: AsyncSession = Depends(get_db)):
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
    return occ


@router.delete("/{occasion_id}", status_code=204)
async def delete_occasion(occasion_id: int, db: AsyncSession = Depends(get_db)):
    occ = await db.get(Occasion, occasion_id)
    if not occ:
        raise HTTPException(404, "Occasion not found")
    await db.delete(occ)
