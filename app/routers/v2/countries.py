"""
Countries router — managed list of countries for consistent naming.
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import verify_api_key
from app.db.models import Country
from app.db.session import get_db
from app.schemas.api import CountryCreate, CountryOut, CountryUpdate

router = APIRouter(
    prefix="/api/v2/countries",
    tags=["countries"],
    dependencies=[Depends(verify_api_key)],
)


@router.get("", response_model=List[CountryOut])
async def list_countries(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(Country).order_by(Country.name.asc())
    )).scalars().all()
    return rows


@router.post("", response_model=CountryOut, status_code=status.HTTP_201_CREATED)
async def create_country(body: CountryCreate, db: AsyncSession = Depends(get_db)):
    code = body.code.strip().upper()
    existing = await db.scalar(select(Country).where(Country.code == code))
    if existing:
        raise HTTPException(409, f"Country with code '{code}' already exists")
    country = Country(code=code, name=body.name.strip())
    db.add(country)
    await db.flush()
    return country


@router.patch("/{country_id}", response_model=CountryOut)
async def update_country(
    country_id: int,
    body: CountryUpdate,
    db: AsyncSession = Depends(get_db),
):
    country = await db.get(Country, country_id)
    if not country:
        raise HTTPException(404, "Country not found")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(country, field, value)
    await db.flush()
    return country


@router.delete("/{country_id}", status_code=204)
async def delete_country(country_id: int, db: AsyncSession = Depends(get_db)):
    country = await db.get(Country, country_id)
    if not country:
        raise HTTPException(404, "Country not found")
    await db.delete(country)
