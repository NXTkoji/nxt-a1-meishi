"""
Persons router — CRUD + search.
Supports creating persons manually (no card) and searching for merge candidates.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete as sa_delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import verify_api_key
from app.db.models import (
    Card,
    ContactDetail,
    Organization,
    OrganizationName,
    Person,
    PersonName,
    PersonRelationship,
    Position,
    PositionDetail,
)
from app.db.session import get_db
from app.schemas.api import (
    ContactDetailOut,
    MergeRequest,
    MergeResult,
    OrgNameOut,
    PersonCreate,
    PersonListItem,
    PersonNameOut,
    PersonOut,
    PersonUpdate,
    PositionDetailOut,
    PositionOut,
)
from pydantic import BaseModel


class PersonNameUpdate(BaseModel):
    full_name: Optional[str] = None
    family_name: Optional[str] = None
    given_name: Optional[str] = None
    honorific: Optional[str] = None


class ContactDetailUpdate(BaseModel):
    value: Optional[str] = None
    label: Optional[str] = None
    detail_type: Optional[str] = None
    country_code: Optional[str] = None


class OrgNameUpdate(BaseModel):
    name: Optional[str] = None


class PositionDetailUpdate(BaseModel):
    title: Optional[str] = None
    department: Optional[str] = None

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v2/persons",
    tags=["persons"],
    dependencies=[Depends(verify_api_key)],
)


async def _load_person_out(db: AsyncSession, person: Person) -> PersonOut:
    """Eagerly load all relations and build PersonOut response."""
    # Names
    names_rows = (await db.execute(
        select(PersonName)
        .where(PersonName.person_id == person.id)
        .order_by(PersonName.id)
    )).scalars().all()

    # Contact details
    cd_rows = (await db.execute(
        select(ContactDetail)
        .where(ContactDetail.person_id == person.id)
        .order_by(ContactDetail.id)
    )).scalars().all()

    # Positions with org names and details
    pos_rows = (await db.execute(
        select(Position)
        .where(Position.person_id == person.id)
        .order_by(Position.id)
    )).scalars().all()

    positions_out = []
    for pos in pos_rows:
        org_names = (await db.execute(
            select(OrganizationName)
            .where(OrganizationName.org_id == pos.org_id, OrganizationName.is_current == True)  # noqa: E712
        )).scalars().all()
        pos_details = (await db.execute(
            select(PositionDetail).where(PositionDetail.position_id == pos.id)
        )).scalars().all()
        positions_out.append(PositionOut(
            id=pos.id,
            org_id=pos.org_id,
            status=pos.status,
            org_names=[OrgNameOut(id=on.id, language=on.language, name=on.name, is_current=on.is_current) for on in org_names],
            details=[PositionDetailOut(id=d.id, language=d.language, title=d.title, department=d.department) for d in pos_details],
        ))

    return PersonOut(
        id=person.id,
        external_id=person.external_id,
        notes=person.notes,
        birthday=person.birthday,
        created_at=person.created_at,
        updated_at=person.updated_at,
        names=[n.__dict__ for n in names_rows],  # from_attributes handles this
        contact_details=[cd.__dict__ for cd in cd_rows],
        positions=positions_out,
    )


@router.get("", response_model=List[PersonListItem])
async def list_persons(
    q: Optional[str] = Query(None, description="Search by name"),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    if q:
        # Search in current person names
        matched_name_ids = (await db.execute(
            select(PersonName.person_id)
            .where(PersonName.is_current == True, PersonName.full_name.ilike(f"%{q}%"))  # noqa: E712
        )).scalars().all()
        # Also search in org names (via Position → OrganizationName)
        matched_org_ids = (await db.execute(
            select(Position.person_id)
            .join(OrganizationName, OrganizationName.org_id == Position.org_id)
            .where(OrganizationName.is_current == True, OrganizationName.name.ilike(f"%{q}%"))  # noqa: E712
        )).scalars().all()
        all_ids = set(matched_name_ids) | set(matched_org_ids)
        stmt = select(Person).where(Person.id.in_(all_ids))
    else:
        stmt = select(Person).order_by(Person.created_at.desc()).limit(limit).offset(offset)

    persons = (await db.execute(stmt)).scalars().all()

    items = []
    for p in persons:
        name_row = (await db.execute(
            select(PersonName.full_name, PersonName.family_name)
            .where(PersonName.person_id == p.id, PersonName.is_current == True)  # noqa: E712
            .order_by(PersonName.id.asc())
            .limit(1)
        )).first()
        primary_name = name_row[0] if name_row else None
        family_name = name_row[1] if name_row else None

        # Country: home address first, then work address
        country_row = (await db.execute(
            select(ContactDetail.country_code)
            .where(
                ContactDetail.person_id == p.id,
                ContactDetail.detail_type.in_(["address_home", "address_work"]),
                ContactDetail.country_code.isnot(None),
            )
            .order_by(
                # address_home sorts before address_work
                ContactDetail.detail_type.asc(),
                ContactDetail.id.asc(),
            )
            .limit(1)
        )).first()
        country_code = country_row[0] if country_row else None

        items.append(PersonListItem(
            id=p.id,
            external_id=p.external_id,
            primary_name=primary_name,
            family_name=family_name,
            country_code=country_code,
            created_at=p.created_at,
        ))
    return items


@router.get("/{person_ext_id}", response_model=PersonOut)
async def get_person(person_ext_id: str, db: AsyncSession = Depends(get_db)):
    person = await db.scalar(select(Person).where(Person.external_id == person_ext_id))
    if not person:
        raise HTTPException(404, "Person not found")
    return await _load_person_out(db, person)


@router.patch("/{person_ext_id}", response_model=PersonOut)
async def update_person(
    person_ext_id: str,
    body: PersonUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update person-level fields (currently: birthday)."""
    person = await db.scalar(select(Person).where(Person.external_id == person_ext_id))
    if not person:
        raise HTTPException(404, "Person not found")
    data = body.model_dump(exclude_unset=True)
    if "birthday" in data:
        # Empty string clears the birthday (stored as NULL).
        person.birthday = data["birthday"] or None
    await db.flush()
    return await _load_person_out(db, person)


@router.post("", response_model=PersonOut, status_code=status.HTTP_201_CREATED)
async def create_person(body: PersonCreate, db: AsyncSession = Depends(get_db)):
    """Create a person manually without a business card."""
    person = Person(external_id=str(uuid.uuid4()), notes=body.notes)
    db.add(person)
    await db.flush()

    for n in body.names:
        db.add(PersonName(
            person_id=person.id,
            language=n.get("language", "ja"),
            name_type=n.get("name_type", "primary"),
            family_name=n.get("family_name"),
            given_name=n.get("given_name"),
            full_name=n.get("full_name", ""),
            is_current=True,
            valid_from=date.today(),
            source="manual",
        ))
    await db.flush()
    return await _load_person_out(db, person)


@router.delete("/{person_ext_id}", status_code=204)
async def delete_person(person_ext_id: str, db: AsyncSession = Depends(get_db)):
    person = await db.scalar(select(Person).where(Person.external_id == person_ext_id))
    if not person:
        raise HTTPException(404, "Person not found")
    await db.delete(person)


@router.post("/{primary_ext_id}/merge", response_model=MergeResult)
async def merge_persons(
    primary_ext_id: str,
    body: MergeRequest,
    db: AsyncSession = Depends(get_db),
):
    """Merge N source persons into primary. All cards, names, contact details,
    and positions are reassigned. Sources are deleted. Returns merged PersonOut
    and count of duplicate contact details detected."""

    # Load primary
    primary = await db.scalar(select(Person).where(Person.external_id == primary_ext_id))
    if not primary:
        raise HTTPException(404, "Primary person not found")

    # Filter out primary from source_ids (idempotent)
    source_ext_ids = [sid for sid in body.source_ids if sid != primary_ext_id]
    if not source_ext_ids:
        return MergeResult(
            person=await _load_person_out(db, primary),
            duplicate_contact_count=0,
        )

    # Load source persons — 404 if any missing
    sources = []
    for ext_id in source_ext_ids:
        p = await db.scalar(select(Person).where(Person.external_id == ext_id))
        if not p:
            raise HTTPException(404, f"Source person not found: {ext_id}")
        sources.append(p)

    source_ids = [p.id for p in sources]

    # Reassign all child rows to primary
    for table, col in [
        (Card, Card.person_id),
        (PersonName, PersonName.person_id),
        (ContactDetail, ContactDetail.person_id),
        (Position, Position.person_id),
    ]:
        await db.execute(
            update(table)
            .where(col.in_(source_ids))
            .values({col: primary.id})
        )

    # Expire stale ORM relationship collections on source persons so SQLAlchemy
    # does not attempt to cascade-delete already-reassigned child rows.
    for p in sources:
        db.expire(p, ["names", "contact_details", "positions", "cards"])

    # Concatenate notes
    source_notes = [p.notes for p in sources if p.notes]
    if source_notes:
        existing = primary.notes or ""
        combined = "\n".join(filter(None, [existing] + source_notes))
        primary.notes = combined

    await db.flush()

    # Count duplicate contact details: same (detail_type, lower(trim(value)))
    dup_count_row = await db.execute(
        select(func.count())
        .select_from(
            select(ContactDetail.detail_type, func.lower(func.trim(ContactDetail.value)))
            .where(ContactDetail.person_id == primary.id)
            .group_by(ContactDetail.detail_type, func.lower(func.trim(ContactDetail.value)))
            .having(func.count() > 1)
            .subquery()
        )
    )
    duplicate_contact_count = dup_count_row.scalar() or 0

    # Explicitly delete PersonRelationship rows where source persons are the
    # target (to_person_id). relationships_from is covered by cascade="all,
    # delete-orphan", but relationships_to has no cascade, so these must be
    # cleaned up manually before deleting the source persons.
    await db.execute(
        sa_delete(PersonRelationship).where(PersonRelationship.to_person_id.in_(source_ids))
    )

    # Delete source persons (relationships_from cascades; relationships_to cleaned up above)
    for p in sources:
        await db.delete(p)

    await db.flush()

    person_out = await _load_person_out(db, primary)
    return MergeResult(person=person_out, duplicate_contact_count=duplicate_contact_count)


# ── Inline editing endpoints ──────────────────────────────────────────────────

@router.patch("/{person_ext_id}/names/{name_id}", response_model=PersonNameOut)
async def update_person_name(
    person_ext_id: str,
    name_id: int,
    body: PersonNameUpdate,
    db: AsyncSession = Depends(get_db),
):
    person = await db.scalar(select(Person).where(Person.external_id == person_ext_id))
    if not person:
        raise HTTPException(404, "Person not found")
    name = await db.get(PersonName, name_id)
    if not name or name.person_id != person.id:
        raise HTTPException(404, "Name not found")
    for field, val in body.model_dump(exclude_none=True).items():
        setattr(name, field, val)
    name.source = "manual"
    await db.flush()
    await db.refresh(name)
    return name


@router.post("/{person_ext_id}/contact-details", response_model=ContactDetailOut, status_code=201)
async def add_contact_detail(
    person_ext_id: str,
    body: ContactDetailUpdate,
    db: AsyncSession = Depends(get_db),
):
    person = await db.scalar(select(Person).where(Person.external_id == person_ext_id))
    if not person:
        raise HTTPException(404, "Person not found")
    detail = ContactDetail(
        person_id=person.id,
        detail_type=body.detail_type or "phone_work",
        value=body.value or "",
        label=body.label,
        is_primary=False,
    )
    db.add(detail)
    await db.flush()
    await db.refresh(detail)
    return detail


@router.patch("/{person_ext_id}/contact-details/{detail_id}", response_model=ContactDetailOut)
async def update_contact_detail(
    person_ext_id: str,
    detail_id: int,
    body: ContactDetailUpdate,
    db: AsyncSession = Depends(get_db),
):
    person = await db.scalar(select(Person).where(Person.external_id == person_ext_id))
    if not person:
        raise HTTPException(404, "Person not found")
    detail = await db.get(ContactDetail, detail_id)
    if not detail or detail.person_id != person.id:
        raise HTTPException(404, "Contact detail not found")
    for field, val in body.model_dump(exclude_none=True).items():
        setattr(detail, field, val)
    await db.flush()
    await db.refresh(detail)
    return detail


@router.delete("/{person_ext_id}/contact-details/{detail_id}", status_code=204)
async def delete_contact_detail(
    person_ext_id: str,
    detail_id: int,
    db: AsyncSession = Depends(get_db),
):
    person = await db.scalar(select(Person).where(Person.external_id == person_ext_id))
    if not person:
        raise HTTPException(404, "Person not found")
    detail = await db.get(ContactDetail, detail_id)
    if not detail or detail.person_id != person.id:
        raise HTTPException(404, "Contact detail not found")
    await db.delete(detail)


@router.patch("/{person_ext_id}/positions/{position_id}/details/{detail_id}", response_model=PositionDetailOut)
async def update_position_detail(
    person_ext_id: str,
    position_id: int,
    detail_id: int,
    body: PositionDetailUpdate,
    db: AsyncSession = Depends(get_db),
):
    person = await db.scalar(select(Person).where(Person.external_id == person_ext_id))
    if not person:
        raise HTTPException(404, "Person not found")
    detail = await db.get(PositionDetail, detail_id)
    if not detail or detail.position_id != position_id:
        raise HTTPException(404, "Position detail not found")
    for field, val in body.model_dump(exclude_none=True).items():
        setattr(detail, field, val)
    await db.flush()
    await db.refresh(detail)
    return detail


@router.patch("/{person_ext_id}/positions/{position_id}/org-name/{org_name_id}", response_model=OrgNameOut)
async def update_org_name(
    person_ext_id: str,
    position_id: int,
    org_name_id: int,
    body: OrgNameUpdate,
    db: AsyncSession = Depends(get_db),
):
    person = await db.scalar(select(Person).where(Person.external_id == person_ext_id))
    if not person:
        raise HTTPException(404, "Person not found")
    # Verify the position belongs to this person
    pos = await db.get(Position, position_id)
    if not pos or pos.person_id != person.id:
        raise HTTPException(404, "Position not found")
    org_name = await db.get(OrganizationName, org_name_id)
    if not org_name or org_name.org_id != pos.org_id:
        raise HTTPException(404, "Org name not found")
    if body.name is not None:
        org_name.name = body.name
        org_name.source = "manual"
    await db.flush()
    await db.refresh(org_name)
    return org_name
