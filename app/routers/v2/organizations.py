"""
Organizations router — list and search.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import verify_api_key
from app.db.models import Organization, OrganizationName
from app.db.session import get_db
from app.schemas.api import OrgListItem, OrgNameOut, OrgOut

router = APIRouter(
    prefix="/api/v2/organizations",
    tags=["organizations"],
    dependencies=[Depends(verify_api_key)],
)


@router.get("", response_model=List[OrgListItem])
async def list_organizations(
    q: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    if q:
        matched_ids = (await db.execute(
            select(OrganizationName.org_id)
            .where(OrganizationName.is_current == True, OrganizationName.name.ilike(f"%{q}%"))  # noqa: E712
        )).scalars().all()
        stmt = select(Organization).where(Organization.id.in_(matched_ids))
    else:
        stmt = select(Organization).order_by(Organization.created_at.desc()).limit(limit).offset(offset)

    orgs = (await db.execute(stmt)).scalars().all()

    items = []
    for org in orgs:
        primary = await db.scalar(
            select(OrganizationName.name)
            .where(OrganizationName.org_id == org.id, OrganizationName.is_current == True)  # noqa: E712
            .order_by(OrganizationName.id.asc())
            .limit(1)
        )
        items.append(OrgListItem(id=org.id, external_id=org.external_id, primary_name=primary, created_at=org.created_at))
    return items


@router.get("/{org_ext_id}", response_model=OrgOut)
async def get_organization(org_ext_id: str, db: AsyncSession = Depends(get_db)):
    org = await db.scalar(select(Organization).where(Organization.external_id == org_ext_id))
    if not org:
        raise HTTPException(404, "Organization not found")
    names = (await db.execute(
        select(OrganizationName).where(OrganizationName.org_id == org.id).order_by(OrganizationName.id)
    )).scalars().all()
    return OrgOut(
        id=org.id,
        external_id=org.external_id,
        created_at=org.created_at,
        names=[OrgNameOut(language=n.language, name=n.name, is_current=n.is_current) for n in names],
    )
