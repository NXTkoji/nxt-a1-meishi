"""
Settings router — My Companies CRUD + read-only relationship types.
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import verify_api_key
from app.db.models import MyCompany, RelationshipType
from app.db.session import get_db
from app.schemas.api import MyCompanyCreate, MyCompanyOut, MyCompanyUpdate

router = APIRouter(
    prefix="/api/v2/settings",
    tags=["settings"],
    dependencies=[Depends(verify_api_key)],
)


# ── My Companies ──────────────────────────────────────────────────────────────

@router.get("/my-companies", response_model=List[MyCompanyOut])
async def list_my_companies(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(MyCompany).order_by(MyCompany.id))).scalars().all()
    return rows


@router.post("/my-companies", response_model=MyCompanyOut, status_code=status.HTTP_201_CREATED)
async def create_my_company(body: MyCompanyCreate, db: AsyncSession = Depends(get_db)):
    company = MyCompany(name=body.name, notes=body.notes)
    db.add(company)
    await db.flush()
    await db.refresh(company)
    return company


@router.patch("/my-companies/{company_id}", response_model=MyCompanyOut)
async def update_my_company(
    company_id: int,
    body: MyCompanyUpdate,
    db: AsyncSession = Depends(get_db),
):
    company = await db.get(MyCompany, company_id)
    if not company:
        raise HTTPException(404, "Company not found")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(company, field, value)
    await db.flush()
    await db.refresh(company)
    return company


@router.delete("/my-companies/{company_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_my_company(company_id: int, db: AsyncSession = Depends(get_db)):
    company = await db.get(MyCompany, company_id)
    if not company:
        raise HTTPException(404, "Company not found")
    await db.delete(company)


# ── Relationship types ────────────────────────────────────────────────────────

@router.get("/relationship-types")
async def list_relationship_types(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(RelationshipType).order_by(RelationshipType.is_predefined.desc(), RelationshipType.label)
    )).scalars().all()
    return [{"id": r.id, "key": r.key, "label": r.label, "is_predefined": r.is_predefined} for r in rows]
