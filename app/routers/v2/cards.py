"""
Cards router — browse and manage confirmed cards.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import Integer, and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import verify_api_key
from app.db.models import Card, CardMyCompany, CardSide, CardSyncHistory, Person, PersonName
from app.db.session import get_db
from app.schemas.api import CardFacet, CardListItem, CardOut, CardSideOut, CountOut
from app.services import image_store
from app.services.contact_sync import auto_sync_card

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v2/cards",
    tags=["cards"],
    dependencies=[Depends(verify_api_key)],
)


def _bucket_date():
    """The date a card is filed under: received_date, or created_at when it is NULL.

    Every date rule in this module derives from this one expression, so a card can
    never land in one bucket for the facets query and a different one for ?month=.
    """
    return func.coalesce(Card.received_date, Card.created_at)


def _bucket_year():
    """Year of the bucket date, as a real Python int. Spec §3.

    strftime + cast, not extract: extract() returns a float on SQLite, and coalesce
    across a Date and a DateTime column needs a consistent text form. strftime('%Y', …)
    yields a zero-padded string that casts cleanly to Integer and works on both column
    types. This is the ONLY encoding of the bucketing rule — both the month/year
    filters below and the facets GROUP BY use it, so they cannot disagree.
    """
    return func.cast(func.strftime('%Y', _bucket_date()), Integer)


def _bucket_month():
    """Month (1-12) of the bucket date, as a real Python int. See _bucket_year."""
    return func.cast(func.strftime('%m', _bucket_date()), Integer)


def _apply_card_filters(
    stmt,
    *,
    person_id: Optional[int] = None,
    occasion_id: Optional[int] = None,
    my_company_id: Optional[int] = None,
    q: Optional[str] = None,
    year: Optional[int] = None,
    month: Optional[str] = None,
    date: Optional[str] = None,
    not_exported: bool = False,
):
    """Apply the shared Collection/Export filter set to a Card select.

    Single owner of four rules that list_cards, count_cards and card_facets must agree on:
      * date bucketing — received_date, falling back to created_at when null
      * what ?q= matches — current person name, contact details, position title/department
        and current organization name
      * not_exported — a card counts as exported only when it has sync history to
        "odoo" or "google_contacts" whose result is "created" or "updated"
      * soft-deleted cards are excluded — this helper owns that predicate, so callers
        must NOT repeat it on their own select
    If list_cards, count_cards and card_facets ever disagree on any of these rules, a
    month header count will not match the cards that appear when it is expanded.
    """
    from datetime import date as date_type

    from app.db.models import ContactDetail, Organization, OrganizationName, Position, PositionDetail

    # Soft-deleted cards are never visible through any of these endpoints.
    stmt = stmt.where(Card.deleted_at.is_(None))

    if person_id:
        stmt = stmt.where(Card.person_id == person_id)
    if occasion_id:
        stmt = stmt.where(Card.occasion_id == occasion_id)
    if my_company_id:
        mc_subq = select(CardMyCompany.card_id).where(CardMyCompany.my_company_id == my_company_id)
        stmt = stmt.where(Card.id.in_(mc_subq))

    # Date filters. All three go through the _bucket_* expressions, which are the
    # single encoding of "received_date, falling back to created_at" — the same
    # expressions card_facets groups by, so a facet count and its ?month= query
    # match by construction rather than by coincidence.
    if date:
        d = date_type.fromisoformat(date)
        stmt = stmt.where(func.date(_bucket_date()) == d)
    elif month:
        y, m = int(month[:4]), int(month[5:7])
        stmt = stmt.where(and_(_bucket_year() == y, _bucket_month() == m))
    elif year:
        stmt = stmt.where(_bucket_year() == year)

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
        text_subq = select(PersonName.person_id).where(
            PersonName.person_id == Card.person_id,
            PersonName.is_current == True,  # noqa: E712
            PersonName.full_name.ilike(like),
        )
        contact_subq = select(ContactDetail.person_id).where(
            ContactDetail.person_id == Card.person_id,
            ContactDetail.value.ilike(like),
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

    return stmt


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
    # _apply_card_filters owns the soft-delete predicate — do not repeat it here.
    stmt = (
        select(Card)
        .order_by(Card.created_at.desc())
        .limit(limit)
        .offset(offset)
        .options(selectinload(Card.sides))
    )
    stmt = _apply_card_filters(
        stmt,
        person_id=person_id, occasion_id=occasion_id, my_company_id=my_company_id,
        q=q, year=year, month=month, date=date, not_exported=not_exported,
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


# ---------------------------------------------------------------------------
# Facets and count. MUST be declared before GET /{card_ext_id}, or FastAPI
# matches "facets" and "count" as card external IDs.
# ---------------------------------------------------------------------------


@router.get("/facets", response_model=List[CardFacet])
async def card_facets(
    person_id: Optional[int] = Query(None),
    occasion_id: Optional[int] = Query(None),
    my_company_id: Optional[int] = Query(None, description="Filter by Met As (my company) ID"),
    q: Optional[str] = Query(None, description="Full-text search across names, org, contacts, titles"),
    not_exported: bool = Query(False, description="Only cards with no sync history to odoo or google_contacts"),
    db: AsyncSession = Depends(get_db),
):
    """Year/month buckets with counts, newest first.

    One GROUP BY — the response stays small however many cards exist, which is what
    lets the Collection tree be complete at any scale.

    Deliberately takes no year/month/date filter: this endpoint *produces* the date
    buckets that those filters consume.
    """
    y = _bucket_year().label("year")
    m = _bucket_month().label("month")

    # No deleted_at filter here — _apply_card_filters owns it.
    stmt = select(y, m, func.count(Card.id).label("count"))
    stmt = _apply_card_filters(
        stmt,
        person_id=person_id, occasion_id=occasion_id, my_company_id=my_company_id,
        q=q, not_exported=not_exported,
    )
    stmt = stmt.group_by(y, m).order_by(y.desc(), m.desc())

    rows = (await db.execute(stmt)).all()
    return [CardFacet(year=r.year, month=r.month, count=r.count) for r in rows]


@router.get("/count", response_model=CountOut)
async def count_cards(
    person_id: Optional[int] = Query(None),
    occasion_id: Optional[int] = Query(None),
    my_company_id: Optional[int] = Query(None, description="Filter by Met As (my company) ID"),
    q: Optional[str] = Query(None, description="Full-text search across names, org, contacts, titles"),
    year: Optional[int] = Query(None),
    month: Optional[str] = Query(None, description="YYYY-MM"),
    date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    not_exported: bool = Query(False, description="Only cards with no sync history to odoo or google_contacts"),
    db: AsyncSession = Depends(get_db),
):
    """How many cards match a filter set, without fetching any rows.

    Takes the same filters as list_cards minus limit/offset, so the caller can size a
    pager before requesting a page.
    """
    # _apply_card_filters owns the soft-delete predicate — do not repeat it here.
    stmt = select(func.count(Card.id))
    stmt = _apply_card_filters(
        stmt,
        person_id=person_id, occasion_id=occasion_id, my_company_id=my_company_id,
        q=q, year=year, month=month, date=date, not_exported=not_exported,
    )
    return CountOut(total=await db.scalar(stmt) or 0)


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
    # get_db commits the session after this handler returns and before
    # Starlette runs background tasks, so auto_sync_card always sees this
    # update once it opens its own session — no explicit commit needed here.
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
    # Resize/hash in memory, but keep the filesystem untouched until the row is
    # committed — writing first would strand the file if the insert failed.
    prepared = image_store.prepare_permanent_bytes(card_ext_id, side_order, data)

    side = CardSide(
        card_id=card.id,
        side_order=side_order,
        image_path=prepared.relative_path,
        image_filename=prepared.filename,
        image_hash=prepared.sha256,
        width_px=prepared.width_px,
        height_px=prepared.height_px,
    )
    db.add(side)
    await db.commit()
    image_store.write_prepared(prepared)
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

    # Keep the file in step with its side_order. The store is laid out as
    # {card_ext_id}/{side_order}.jpg, so renumbering rows without renaming files
    # leaves names that no longer mean what they look like.
    moves: list[tuple[str, str]] = []
    for s in sides:
        new_path = f"{card_ext_id}/{s.side_order}.jpg"
        if s.image_path != new_path:
            moves.append((s.image_path, new_path))
            s.image_path = new_path
            s.image_filename = f"{s.side_order}.jpg"

    await db.commit()
    try:
        image_store.relocate_permanent_images(moves)
    except OSError:
        # Rows are already committed; a partial move leaves rows pointing at
        # files that have not been renamed yet, so make it loud.
        logger.exception("Failed to relocate images for card %s after promote", card_ext_id)


@router.delete("/{card_ext_id}/sides/{side_order}", status_code=204)
async def delete_card_side(
    card_ext_id: str,
    side_order: int,
    db: AsyncSession = Depends(get_db),
):
    """Remove one image side from a card (not allowed if it's the only side)."""
    card = await _load_card(db, card_ext_id)
    sides = (await db.execute(
        select(CardSide).where(CardSide.card_id == card.id)
    )).scalars().all()
    side = next((s for s in sides if s.side_order == side_order), None)
    if not side:
        raise HTTPException(404, "Side not found")
    if len(sides) <= 1:
        raise HTTPException(400, "Cannot delete the only image of a card")

    removed_path = side.image_path
    remaining = sorted((s for s in sides if s.id != side.id), key=lambda s: s.side_order)

    # Close the gap: side_order must stay the contiguous run 0..n-1. Leaving a
    # hole is not merely untidy — export resolves "back" as side_order 1, so a
    # card left at orders {0, 2} cannot export its second image at all.
    await db.delete(side)
    await db.flush()
    # Park on negative values first: the final numbers overlap the current ones,
    # and uq_card_side_order is checked per statement, not at commit.
    for s in remaining:
        s.side_order = -(s.side_order + 1)
    await db.flush()

    moves: list[tuple[str, str]] = []
    for new_order, s in enumerate(remaining):
        s.side_order = new_order
        new_path = f"{card_ext_id}/{new_order}.jpg"
        if s.image_path != new_path:
            moves.append((s.image_path, new_path))
            s.image_path = new_path
            s.image_filename = f"{new_order}.jpg"

    # Touch the filesystem only once the rows are committed: deleting first
    # would leave a live row pointing at a missing file, and not deleting at all
    # (the original behaviour) orphaned the file forever, since add_card_side
    # hands out max(side_order)+1 and never reuses a freed slot.
    await db.commit()
    try:
        image_store.relocate_permanent_images(moves)
        # The removed side's own file goes last, and only if the renumbering
        # did not just write a surviving side into that same name.
        if removed_path not in {dst for _, dst in moves}:
            image_store.delete_permanent_image(removed_path)
    except OSError:
        logger.exception("Failed to reconcile images for card %s after side delete", card_ext_id)


@router.delete("/{card_ext_id}", status_code=204)
async def delete_card(card_ext_id: str, db: AsyncSession = Depends(get_db)):
    from datetime import datetime
    card = await db.scalar(select(Card).where(Card.external_id == card_ext_id, Card.deleted_at.is_(None)))
    if not card:
        raise HTTPException(404, "Card not found")
    card.deleted_at = datetime.utcnow()
