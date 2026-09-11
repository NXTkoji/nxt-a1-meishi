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
from sqlalchemy import delete as sa_delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import verify_api_key
from app.db.models import (
    Card,
    ContactDetail,
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
    CountOut,
    MergeRequest,
    MergeResult,
    OrgNameOut,
    PersonCreate,
    PersonFacet,
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


async def _person_ids_matching(db: AsyncSession, q: str) -> set[int]:
    """Person ids whose current name or current organisation name matches q.

    Shared by list_persons and count_persons so the two cannot disagree — a pager
    sized from /count that does not match the rows the list returns is worse than no
    pager at all.

    The ids are materialised into a Python set and fed to an `IN (...)`, which is
    bounded by the number of persons in the database, not by `limit`. That is fine at
    the scale this app runs at (hundreds).

    At tens of thousands of rows the fix is NOT a correlated subquery: that would move
    the same work inside the outer query without reducing it. Both predicates here are
    `ilike('%q%')`, a leading-wildcard match, which no B-tree index can serve — every
    row of person_names and organization_names is scanned either way. The real answer
    at that scale is a full-text index (SQLite FTS5) over the two name columns, with
    this function querying that instead of LIKE.
    """
    like = f"%{q}%"
    # Current person names.
    matched_name_ids = (await db.execute(
        select(PersonName.person_id)
        .where(PersonName.is_current == True, PersonName.full_name.ilike(like))  # noqa: E712
    )).scalars().all()
    # Current organisation names, reached through the person's positions.
    matched_org_ids = (await db.execute(
        select(Position.person_id)
        .join(OrganizationName, OrganizationName.org_id == Position.org_id)
        .where(OrganizationName.is_current == True, OrganizationName.name.ilike(like))  # noqa: E712
    )).scalars().all()
    return set(matched_name_ids) | set(matched_org_ids)


# ── Country groups ────────────────────────────────────────────────────────────
#
# The Persons tab shows people grouped by country, each group sorted by family name.
# GET /facets lists the groups with their counts; each group then pages through its
# own people with GET ?country=XX, already in display order. The two helpers below are
# the single SQL encoding of "which country" and "which order" — facets, list and count
# all go through them, so a group header's count cannot disagree with the group's rows.

# The ?country= value selecting persons with NO derived country. A query string cannot
# carry a null, and "none" cannot collide with a real code: the pattern admits only two
# UPPERCASE letters or this exact lowercase word.
COUNTRY_NONE = "none"
COUNTRY_PATTERN = r"^([A-Z]{2}|none)$"
COUNTRY_DESCRIPTION = (
    "Restrict to one country group: an uppercase ISO alpha-2 code (the person's home "
    "address country, else work address country), or 'none' for persons with neither. "
    "With this set, rows come back in the tab's display order (family name) instead of "
    "newest first."
)


def _person_country():
    """First non-null country_code among address_home, then address_work.

    A correlated scalar subquery on Person: usable in WHERE, and as a column of a
    select over Person (person_facets groups by it that way).

    Must agree with the batched country fold in list_persons, which fills each row's
    country_code in Python from the same three predicates and the same ordering.
    test_person_country_filter_agrees_with_row_country pins the two against each other.

    `detail_type ASC` encodes home-before-work only because "address_home" <
    "address_work" alphabetically; renaming either type silently changes the rule.
    NULL codes are excluded in the WHERE rather than skipped afterwards, because a
    NULL home address would otherwise sort first and win.
    """
    return (
        select(ContactDetail.country_code)
        .where(
            ContactDetail.person_id == Person.id,
            ContactDetail.detail_type.in_(["address_home", "address_work"]),
            ContactDetail.country_code.isnot(None),
        )
        .order_by(ContactDetail.detail_type.asc(), ContactDetail.id.asc())
        .limit(1)
        .correlate(Person)
        .scalar_subquery()
    )


def _person_sort_name():
    """The tab's display order: family name, else full name, of the lowest-id current name.

    Lowercased so "adams" sorts before "Baker". The key follows the frontend's
    `(family_name ?? primary_name ?? '').toLowerCase()`, with two known differences:
      * SQLite's lower() folds ASCII only.
      * SQLite compares Unicode code points, where localeCompare uses the locale's
        collation. With an "en" locale the two agree on CJK names; with "ja" or
        "zh-TW" they do not (李/王 and 陳/林 order differently), because those locales
        collate by reading or stroke order.
    So CJK names are ordered by code point, not by the UI locale's stroke or reading
    order. This is intentional: the frontend renders this server order as it arrives,
    with no client re-sort, because a re-sort would move rows already on screen when a
    later page arrives. A Japanese or Traditional Chinese UI therefore shows CJK names
    in code-point order.

    "Lowest-id current name" is the same row the batched name fold in list_persons
    picks, so the key a person is sorted by belongs to the name the row displays.
    A person with no current name yields NULL, which SQLite sorts FIRST under ASC —
    the frontend's '' fallback sorts those first too.
    """
    return (
        select(func.lower(func.coalesce(PersonName.family_name, PersonName.full_name)))
        .where(PersonName.person_id == Person.id, PersonName.is_current == True)  # noqa: E712
        .order_by(PersonName.id.asc())
        .limit(1)
        .correlate(Person)
        .scalar_subquery()
    )


async def _filter_persons(db: AsyncSession, stmt, *, q: Optional[str], country: Optional[str]):
    """Apply the Persons tab's filter set to a select whose FROM contains Person.

    Single owner of the WHERE clause that list_persons, count_persons and person_facets
    share, so the three cannot disagree on who is in a group. person_facets passes
    country=None: it produces the country groups rather than consuming one.

    `country` is pattern-validated at each endpoint, so it is either None, the
    COUNTRY_NONE sentinel, or two uppercase letters.
    """
    if q:
        stmt = stmt.where(Person.id.in_(await _person_ids_matching(db, q)))
    if country == COUNTRY_NONE:
        # `== None` would also compile to IS NULL, but say it explicitly: a plain
        # `= :param` bound to NULL is never true in SQL.
        stmt = stmt.where(_person_country().is_(None))
    elif country is not None:
        stmt = stmt.where(_person_country() == country)
    return stmt


@router.get("", response_model=List[PersonListItem])
async def list_persons(
    q: Optional[str] = Query(None, description="Search by name or organisation"),
    country: Optional[str] = Query(None, pattern=COUNTRY_PATTERN, description=COUNTRY_DESCRIPTION),
    # ge=1 is not cosmetic: SQLite reads LIMIT -1 as "no limit", so ?limit=-1 used to
    # return every row. The 500 cap matches GET /api/v2/cards and is what bounds the
    # two batched lookups below — both the IN (...) parameter count and the rows held
    # in memory. It was 200, which is under the live person count, so the tail of the
    # collection was unreachable at any offset the frontend asked for.
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """One page of the collection's Persons tab, optionally filtered by ?q= and ?country=.

    Shape: build one ordered, paginated statement (search, browse and country-group
    requests all share it) → fetch the page of Person rows → two batched lookups keyed
    on that page's ids, one for names and one for countries → assemble the list items
    in the page's order. Fixed cost of three queries regardless of page size; the
    country filter and sort-name key are subqueries inside the first statement, not
    extra statements.
    """
    stmt = await _filter_persons(db, select(Person), q=q, country=country)

    # ORDER BY has to be a *total* order or LIMIT/OFFSET paging is unsound: rows that
    # tie on the leading key may come back in a different order per query, so page 2
    # can repeat or skip a row from page 1. Person.id breaks every tie in both branches.
    if country is None:
        # Browse and search: newest first, unchanged — existing callers rely on it.
        # Every person from one import batch ties on created_at.
        stmt = stmt.order_by(Person.created_at.desc(), Person.id.desc())
    else:
        # A country group is paged in the order the tab DISPLAYS it. Groups load one
        # page at a time, so if the server paged newest-first and the client sorted by
        # name, "Load more" would insert people into the middle of the rows already on
        # screen. Paging in display order means each page only ever appends.
        # Same-family-name persons tie on the sort key, hence the id tiebreak.
        stmt = stmt.order_by(_person_sort_name().asc(), Person.id.asc())
    stmt = stmt.limit(limit).offset(offset)

    persons = (await db.execute(stmt)).scalars().all()
    person_ids = [p.id for p in persons]
    if not person_ids:
        # Nothing to look up; also keeps the two `IN ()` statements below off the wire.
        return []

    # Names for the whole page in ONE query. This used to be a per-person SELECT ...
    # LIMIT 1 inside the item loop, costing one sequential round trip per row.
    #
    # The ORDER BY is the entire correctness argument: `person_id, id` means the first
    # row seen for a person is its lowest-id current name, which is exactly the row the
    # old LIMIT 1 returned. `setdefault` keeps that first row and ignores the rest.
    # Unlike the cards list there is no per-row language preference to apply here, so
    # this is a plain fold rather than a picker function.
    name_rows = (await db.execute(
        select(PersonName.person_id, PersonName.full_name, PersonName.family_name)
        .where(
            PersonName.person_id.in_(person_ids),
            PersonName.is_current == True,  # noqa: E712
        )
        .order_by(PersonName.person_id.asc(), PersonName.id.asc())
    )).all()
    # Singular "name": this dict holds only the WINNING name per person. cards.py:303
    # uses `names_by_person` for a dict of every candidate name per person, which the
    # picker then chooses from — same identifier, different shape, in two files a
    # reader opens side by side. Keep the two names distinct.
    name_by_person: dict[int, tuple[Optional[str], Optional[str]]] = {}
    for pid, full, family in name_rows:
        name_by_person.setdefault(pid, (full, family))

    # Countries for the whole page in ONE query — same story, same fold.
    #
    # This fold and _person_country are two encodings of one rule: this one fills the
    # row's country_code, that one decides which ?country= group the row is in. Change
    # them together; test_person_country_filter_agrees_with_row_country catches drift.
    #
    # The preference is "home address, else work address", and it is encoded as
    # `detail_type ASC`. That works only because "address_home" sorts before
    # "address_work" alphabetically: renaming either detail_type (to "address_office",
    # say) silently changes which country a person shows. NULL country codes are
    # filtered out in SQL rather than skipped in the fold, because a NULL home address
    # would otherwise sort first and win.
    country_rows = (await db.execute(
        select(ContactDetail.person_id, ContactDetail.country_code)
        .where(
            ContactDetail.person_id.in_(person_ids),
            ContactDetail.detail_type.in_(["address_home", "address_work"]),
            ContactDetail.country_code.isnot(None),
        )
        .order_by(
            ContactDetail.person_id.asc(),
            ContactDetail.detail_type.asc(),
            ContactDetail.id.asc(),
        )
    )).all()
    country_by_person: dict[int, str] = {}
    for pid, code in country_rows:
        country_by_person.setdefault(pid, code)

    return [
        PersonListItem(
            id=p.id,
            external_id=p.external_id,
            # A person with no current name at all keeps both fields null, exactly as
            # the old `if name_row else None` did.
            primary_name=name_by_person.get(p.id, (None, None))[0],
            family_name=name_by_person.get(p.id, (None, None))[1],
            country_code=country_by_person.get(p.id),
            created_at=p.created_at,
        )
        for p in persons
    ]


# /facets and /count MUST precede GET /{person_ext_id} below. FastAPI matches routes in
# declaration order, so declared after it, "facets" and "count" would be captured as
# person external IDs and these endpoints would answer 404 "Person not found".
@router.get("/facets", response_model=List[PersonFacet])
async def person_facets(
    q: Optional[str] = Query(None, description="Search by name or organisation"),
    db: AsyncSession = Depends(get_db),
):
    """Country groups with counts: codes ascending, the no-country group last.

    One statement (plus the two id lookups _person_ids_matching runs first when ?q= is
    given), however many persons exist. Each group's count equals what
    GET ?country=<code> (or ?country=none for the null group) returns, because all
    three endpoints filter through _filter_persons and derive the country through
    _person_country.

    Deliberately takes no ?country=: this endpoint PRODUCES the groups that filter
    consumes. FastAPI ignores unknown query params, so ?country= here is a silent no-op.
    """
    # Derive each person's country once in an inner select, then group the outer one
    # by that column. Grouping directly by the scalar subquery would make SQLAlchemy
    # render the whole subquery again in GROUP BY, a second copy of the rule in the SQL.
    inner = select(Person.id, _person_country().label("country_code"))
    inner = (await _filter_persons(db, inner, q=q, country=None)).subquery()
    code = inner.c.country_code

    stmt = (
        select(code, func.count().label("count"))
        .group_by(code)
        # Null last, then codes ascending. `code IS NULL` is 0 for real codes and 1 for
        # the null group, so sorting on it first pushes null to the end. This matches
        # the frontend's current group comparator (`if (!a) return 1`). A plain ASC
        # would put null FIRST, which is SQLite's default for NULLs.
        .order_by(code.is_(None).asc(), code.asc())
    )

    rows = (await db.execute(stmt)).all()
    return [PersonFacet(country_code=r.country_code, count=r.count) for r in rows]


@router.get("/count", response_model=CountOut)
async def count_persons(
    q: Optional[str] = Query(None, description="Search by name or organisation"),
    country: Optional[str] = Query(None, pattern=COUNTRY_PATTERN, description=COUNTRY_DESCRIPTION),
    db: AsyncSession = Depends(get_db),
):
    """How many persons match ?q= and ?country=, without fetching any rows.

    Takes the same filters as list_persons minus limit/offset, so the caller can size a
    pager before requesting a page.
    """
    stmt = await _filter_persons(db, select(func.count(Person.id)), q=q, country=country)
    # COUNT(...) with no GROUP BY always yields a row holding an integer, so the
    # `or 0` is belt-and-braces against a driver returning None, not a real NULL case.
    return CountOut(total=await db.scalar(stmt) or 0)


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
