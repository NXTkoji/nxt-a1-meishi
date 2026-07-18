# Duplicate Check & Manual Export — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an inline duplicate merge editor to the scan review step, and replace automatic sync-on-confirm with a user-initiated export flow backed by a persistent sync history table.

**Architecture:** Two independent phases — Phase A (Manual Export) adds `CardSyncHistory` to the DB, removes auto-sync from the legacy v1 confirm endpoint, adds filter params to the cards API, and builds the export selection + destination UI. Phase B (Duplicate Check) enriches the match result with the person's external UUID, adds `DuplicateFieldEditor` inline in `ScanPage`, and sends a composed merged `ParsedCard` to the existing confirm endpoint. The phases share no code and can be shipped independently in any order.

**Tech Stack:** Python 3.11 + FastAPI + SQLAlchemy async + Alembic (backend); React 19 + TypeScript + TanStack Query (frontend); Tailwind CSS for styling (follow existing class patterns).

**Deployment note:** After any backend change run `./deploy.sh` from the project root — the backend runs as a LaunchAgent and must be reloaded via launchctl. Never run uvicorn manually.

---

## Scope note

This spec covers two independent subsystems. They are combined here for convenience but can be executed in either order. Phase A (Tasks 1–9) is the export flow. Phase B (Tasks 10–12) is the duplicate check.

---

## File map

| File | Action | Purpose |
|---|---|---|
| `app/db/models.py` | Modify | Add `CardSyncHistory` ORM model + relationship on `Card` |
| `migrations/versions/c3d4e5f6a7b8_add_card_sync_history.py` | Create | Alembic migration for the new table |
| `app/routers/confirm.py` | Modify | Remove auto-sync calls (v1 endpoint stub) |
| `app/schemas/api.py` | Modify | Add export schemas; add `synced_destinations` to `CardListItem` |
| `app/routers/v2/cards.py` | Modify | Add `q`, `year`, `month`, `date`, `occasion_id`, `not_exported` filter params |
| `app/routers/v2/export.py` | Create | `POST /api/v2/export` — run export, record sync history |
| `app/main.py` | Modify | Register export router |
| `frontend/src/types/index.ts` | Modify | Add `CardSyncHistory`, export destination types |
| `frontend/src/api/index.ts` | Modify | Add `listCardsFiltered`, `runExport`, `listOccasions` export |
| `frontend/src/App.tsx` | Modify | Add `/export` route |
| `frontend/src/pages/CollectionPage.tsx` | Modify | Add "Export" button |
| `frontend/src/pages/ExportPage.tsx` | Create | Filter + card selection screen |
| `frontend/src/components/ExportDestinationSelector.tsx` | Create | Destination multi-select + results |
| `app/schemas/parsed_card.py` | Modify | Add `person_external_id` to `MatchResult` |
| `app/services/contact_matcher.py` | Modify | Populate `person_external_id` on match results |
| `frontend/src/components/DuplicateFieldEditor.tsx` | Create | Two-column drag merge editor |
| `frontend/src/pages/ScanPage.tsx` | Modify | Show `DuplicateFieldEditor` inline when `matchConfidence >= 0.55` |
| `frontend/src/i18n.ts` | Modify | Add strings for export UI and duplicate panel |

---

## Phase A — Manual Export

---

### Task 1: CardSyncHistory DB model + migration

**Files:**
- Modify: `app/db/models.py`
- Create: `migrations/versions/c3d4e5f6a7b8_add_card_sync_history.py`

- [ ] **Step 1: Add CardSyncHistory model and relationship to `app/db/models.py`**

Open `app/db/models.py`. Add the `CardSyncHistory` class at the end of the file, and add the `sync_history` relationship to `Card`.

In the `Card` class, after the `my_company_links` relationship (around line 337), add:
```python
    sync_history: Mapped[List["CardSyncHistory"]] = relationship(
        back_populates="card",
        cascade="all, delete-orphan",
        order_by="CardSyncHistory.synced_at.desc()",
    )
```

At the end of the file, add:
```python
# ---------------------------------------------------------------------------
# Card Sync History  (one row per export event per card per destination)
# ---------------------------------------------------------------------------

class CardSyncHistory(Base):
    """
    Records every manual export event.
    destination values: odoo, google_contacts, onedrive
    result values: created, updated, error
    """
    __tablename__ = "card_sync_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id"), nullable=False)
    destination: Mapped[str] = mapped_column(String(32), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    result: Mapped[str] = mapped_column(String(16), nullable=False)  # created, updated, error
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    card: Mapped["Card"] = relationship(back_populates="sync_history")
```

- [ ] **Step 2: Create the Alembic migration**

Create `migrations/versions/c3d4e5f6a7b8_add_card_sync_history.py`:

```python
"""add_card_sync_history

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-04-25

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'card_sync_history',
        sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
        sa.Column('card_id', sa.Integer(), sa.ForeignKey('cards.id'), nullable=False),
        sa.Column('destination', sa.String(32), nullable=False),
        sa.Column('synced_at', sa.DateTime(), nullable=False),
        sa.Column('result', sa.String(16), nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
    )
    op.create_index('ix_card_sync_history_card_id', 'card_sync_history', ['card_id'])


def downgrade() -> None:
    op.drop_index('ix_card_sync_history_card_id', table_name='card_sync_history')
    op.drop_table('card_sync_history')
```

- [ ] **Step 3: Deploy and verify migration runs**

```bash
cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi"
./deploy.sh
```

Expected: deploy output shows "Database migrations up to date." without errors. Verify in SQLite:
```bash
sqlite3 nxt_a1.db ".tables" | grep sync
```
Expected output: `card_sync_history`

- [ ] **Step 4: Commit**

```bash
cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi"
git add app/db/models.py migrations/versions/c3d4e5f6a7b8_add_card_sync_history.py
git commit -m "feat: add CardSyncHistory table for manual export tracking"
```

---

### Task 2: Remove auto-sync from v1 confirm endpoint

**Files:**
- Modify: `app/routers/confirm.py`

The legacy v1 `/api/v1/confirm` endpoint fires Odoo + Google + OneDrive sync automatically on confirm. Per the spec, export is now always user-initiated. Replace the sync logic with a 410 Gone response so any old client gets a clear error.

- [ ] **Step 1: Replace confirm.py body**

Replace the entire content of `app/routers/confirm.py` with:

```python
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.auth import verify_api_key
from fastapi import Depends

router = APIRouter(prefix="/api/v1", dependencies=[Depends(verify_api_key)])


@router.post("/confirm")
async def confirm_card_deprecated():
    """
    Removed: automatic sync on confirm is no longer supported.
    Use POST /api/v2/export to export cards explicitly.
    """
    return JSONResponse(
        status_code=410,
        content={"detail": "Auto-sync on confirm has been removed. Use POST /api/v2/export."},
    )
```

- [ ] **Step 2: Deploy and verify**

```bash
cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi"
./deploy.sh
```

```bash
curl -s -X POST http://localhost:8000/api/v1/confirm \
  -H "Authorization: Bearer $(grep VITE_API_KEY .env | cut -d= -f2)" \
  -H "Content-Type: application/json" -d '{}' | python3 -m json.tool
```
Expected: `{"detail": "Auto-sync on confirm has been removed. Use POST /api/v2/export."}`

- [ ] **Step 3: Commit**

```bash
git add app/routers/confirm.py
git commit -m "feat: remove auto-sync from v1 confirm — export is now user-initiated"
```

---

### Task 3: Export schemas + update CardListItem

**Files:**
- Modify: `app/schemas/api.py`

- [ ] **Step 1: Add export schemas and update CardListItem in `app/schemas/api.py`**

In the `CardListItem` class, add `synced_destinations` after `front_image_path`:
```python
class CardListItem(BaseModel):
    id: int
    external_id: str
    person_id: int
    received_date: Optional[date]
    sync_status: str
    created_at: datetime
    # Denormalized for list view
    person_name: Optional[str] = None
    front_image_path: Optional[str] = None
    # Destinations that have a successful sync record (most recent per destination)
    synced_destinations: List[str] = Field(default_factory=list)

    model_config = {"from_attributes": True}
```

At the end of `app/schemas/api.py`, add:
```python
# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

class ExportRequest(BaseModel):
    card_external_ids: List[str]
    destinations: List[str]  # e.g. ["odoo", "google_contacts"]


class ExportResultItem(BaseModel):
    card_external_id: str
    destination: str
    result: str          # created, updated, error
    error_message: Optional[str] = None


class ExportResponse(BaseModel):
    results: List[ExportResultItem]
```

- [ ] **Step 2: Commit**

```bash
git add app/schemas/api.py
git commit -m "feat: add export schemas and synced_destinations to CardListItem"
```

---

### Task 4: Cards list filter params

**Files:**
- Modify: `app/routers/v2/cards.py`

Add `q`, `year`, `month`, `date`, `occasion_id`, `not_exported` query params to `GET /api/v2/cards`. The text search (`q`) searches across person names, org names, contact details, position titles/departments — all in one query using a subquery-based EXISTS approach to avoid duplicating card rows.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cards_filter.py`:
```python
"""Smoke tests for card list filter params."""
import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_cards_filter_not_exported_returns_200(client: AsyncClient):
    resp = await client.get("/api/v2/cards?not_exported=true")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.anyio
async def test_cards_filter_q_returns_200(client: AsyncClient):
    resp = await client.get("/api/v2/cards?q=test")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.anyio
async def test_cards_filter_year_returns_200(client: AsyncClient):
    resp = await client.get("/api/v2/cards?year=2026")
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_cards_filter_occasion_returns_200(client: AsyncClient):
    resp = await client.get("/api/v2/cards?occasion_id=999")
    assert resp.status_code == 200
    assert resp.json() == []
```

- [ ] **Step 2: Run tests to confirm they fail (endpoint doesn't have these params yet)**

```bash
cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi"
python -m pytest tests/test_cards_filter.py -v 2>&1 | head -40
```
Expected: ImportError or connection error (no test client setup yet) — or the tests may pass trivially if the endpoint ignores unknown params. Either way, proceed to implementation.

- [ ] **Step 3: Replace `list_cards` in `app/routers/v2/cards.py`**

Replace the entire `list_cards` function (from `@router.get("")` through the closing `return items`) with:

```python
@router.get("", response_model=List[CardListItem])
async def list_cards(
    person_id: Optional[int] = Query(None),
    occasion_id: Optional[int] = Query(None),
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
        ContactDetail, Organization, OrganizationName,
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

    # not_exported: no sync history to odoo or google_contacts
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
        from app.db.models import CardSyncHistory as CSH
        sh_stmt = (
            select(CSH)
            .where(
                CSH.card_id.in_(card_ids),
                CSH.result.in_(["created", "updated"]),
            )
            .order_by(CSH.synced_at.desc())
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
```

Also add the missing import at the top of `cards.py` — `PersonName` is already imported but we need to alias it vs the model import. The file already imports `from app.db.models import Card, CardSide, Person, PersonName` — the new function refers to `PersonName` for the name lookup (as before) and `PersonNameModel` inside the filter. Make sure the alias in the function body is consistent: use `PersonName` for the `_get_name` helper (which was already there) and the local `PersonNameModel` alias inside the filter block (imported inside the function).

- [ ] **Step 4: Deploy and smoke test**

```bash
./deploy.sh
```

```bash
curl -s "http://localhost:8000/api/v2/cards?not_exported=true&limit=5" \
  -H "Authorization: Bearer $(grep VITE_API_KEY .env | cut -d= -f2)" | python3 -m json.tool
```
Expected: JSON array (possibly empty). Each item should have a `synced_destinations` field (empty list `[]`).

```bash
curl -s "http://localhost:8000/api/v2/cards?q=test&limit=5" \
  -H "Authorization: Bearer $(grep VITE_API_KEY .env | cut -d= -f2)" | python3 -m json.tool
```
Expected: JSON array.

- [ ] **Step 5: Commit**

```bash
git add app/routers/v2/cards.py
git commit -m "feat: add full-text search and date/export filters to GET /api/v2/cards"
```

---

### Task 5: Export API endpoint

**Files:**
- Create: `app/routers/v2/export.py`
- Modify: `app/main.py`

This endpoint accepts a list of card external IDs and destination names, calls the existing sync services for each combination, and records results in `CardSyncHistory`.

The existing sync services (`sync_to_odoo`, `sync_to_google`) use the old `app/models/card.py` `Card` model. We bridge by converting the v2 DB data into that format.

- [ ] **Step 1: Create `app/routers/v2/export.py`**

```python
"""
Export router — user-initiated card export to external destinations.

POST /api/v2/export
  Body: { card_external_ids: [...], destinations: ["odoo", "google_contacts"] }
  Returns: { results: [{ card_external_id, destination, result, error_message }] }
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import verify_api_key
from app.db.models import (
    Card,
    CardSyncHistory,
    ContactDetail,
    Organization,
    OrganizationName,
    Person,
    PersonName,
    Position,
    PositionDetail,
)
from app.db.session import get_db
from app.schemas.api import ExportRequest, ExportResponse, ExportResultItem

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v2/export",
    tags=["export"],
    dependencies=[Depends(verify_api_key)],
)


# ---------------------------------------------------------------------------
# Bridge: convert v2 DB Card → legacy models.card.Card for sync services
# ---------------------------------------------------------------------------

def _build_legacy_card(
    db_card: Card,
    person: Person,
    contact_details: list[ContactDetail],
    positions: list[Position],
) -> "LegacyCard":
    """Convert v2 DB objects into the legacy app.models.card.Card Pydantic model."""
    from app.models.card import (
        Address,
        Card as LegacyCard,
        Email,
        Person as LegacyPerson,
        PersonName as LegacyName,
        Phone,
        Position as LegacyPosition,
        Social,
    )

    names = [
        LegacyName(
            value=n.full_name,
            language=n.language,
            type=n.name_type,
        )
        for n in person.names
        if n.is_current
    ]

    legacy_positions = []
    for pos in positions:
        org_name_ja = next(
            (on.name for on in pos.organization.names if on.language == "ja" and on.is_current),
            next((on.name for on in pos.organization.names if on.is_current), ""),
        )
        org_name_en = next(
            (on.name for on in pos.organization.names if on.language == "en" and on.is_current),
            "",
        )
        title_ja = next(
            (pd.title or "" for pd in pos.details if pd.language == "ja"), ""
        )
        title_en = next(
            (pd.title or "" for pd in pos.details if pd.language == "en"), ""
        )
        dept = next(
            (pd.department or "" for pd in pos.details if pd.language == "ja"),
            next((pd.department or "" for pd in pos.details), ""),
        )
        legacy_positions.append(LegacyPosition(
            company=org_name_ja,
            company_english=org_name_en,
            title=title_ja,
            title_english=title_en,
            department=dept,
        ))

    phones, emails, addresses = [], [], []
    website = ""
    social = Social()
    for cd in contact_details:
        t = cd.detail_type
        if t in ("phone_work", "phone_mobile", "phone_fax"):
            kind = t.replace("phone_", "")
            phones.append(Phone(value=cd.value, type=kind, label=cd.label or ""))
        elif t in ("email_work", "email_personal"):
            kind = t.replace("email_", "")
            emails.append(Email(value=cd.value, type=kind))
        elif t in ("address_work", "address_home"):
            kind = t.replace("address_", "")
            addresses.append(Address(type=kind, full=cd.value))
        elif t == "url_website":
            website = cd.value
        elif t == "social_wechat":
            social.wechat = cd.value
        elif t == "social_line":
            social.line = cd.value
        elif t == "social_linkedin":
            social.linkedin = cd.value

    legacy_person = LegacyPerson(
        names=names,
        positions=legacy_positions,
        phones=phones,
        emails=emails,
        addresses=addresses,
        website=website,
        social=social,
    )

    return LegacyCard(person=legacy_person)


async def _load_full_card(db: AsyncSession, card_ext_id: str) -> Card | None:
    return await db.scalar(
        select(Card)
        .where(Card.external_id == card_ext_id, Card.deleted_at.is_(None))
        .options(
            selectinload(Card.sides),
            selectinload(Card.person).selectinload(Person.names),
            selectinload(Card.person).selectinload(Person.contact_details),
            selectinload(Card.person).selectinload(Person.positions)
                .selectinload(Position.details),
            selectinload(Card.person).selectinload(Person.positions)
                .selectinload(Position.organization)
                .selectinload(Organization.names),
        )
    )


async def _export_one(
    db: AsyncSession,
    card: Card,
    destination: str,
) -> ExportResultItem:
    """Run one export and persist the result to CardSyncHistory."""
    ext_id = card.external_id
    person = card.person
    contact_details = person.contact_details
    positions = person.positions

    result = "error"
    error_message = None

    try:
        legacy = _build_legacy_card(card, person, contact_details, positions)

        if destination == "odoo":
            from app.services.odoo_sync import sync_to_odoo
            existing_odoo_id = card.odoo_partner_id
            odoo_id = await sync_to_odoo(legacy, existing_odoo_id)
            if odoo_id:
                result = "updated" if existing_odoo_id else "created"
                # Update card's odoo_partner_id
                card.odoo_partner_id = odoo_id
                card.odoo_sync_at = datetime.utcnow()
            else:
                result = "error"
                error_message = "sync_to_odoo returned None"

        elif destination == "google_contacts":
            from app.services.google_contacts import sync_to_google
            existing_resource = person.google_resource
            resource_name = await sync_to_google(legacy, existing_resource)
            if resource_name:
                result = "updated" if existing_resource else "created"
                person.google_resource = resource_name
                card.google_sync_at = datetime.utcnow()
            else:
                result = "error"
                error_message = "sync_to_google returned None"

        else:
            result = "error"
            error_message = f"Unknown destination: {destination}"

    except Exception as exc:
        logger.exception("Export failed for card %s → %s", ext_id, destination)
        result = "error"
        error_message = str(exc)

    db.add(CardSyncHistory(
        card_id=card.id,
        destination=destination,
        result=result,
        error_message=error_message,
    ))

    return ExportResultItem(
        card_external_id=ext_id,
        destination=destination,
        result=result,
        error_message=error_message,
    )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("", response_model=ExportResponse)
async def run_export(body: ExportRequest, db: AsyncSession = Depends(get_db)):
    """
    Export the specified cards to the chosen destinations.
    Runs each (card, destination) pair, records the result, and returns a summary.
    Cards or destinations that error do not block the rest.
    """
    results: List[ExportResultItem] = []

    for ext_id in body.card_external_ids:
        card = await _load_full_card(db, ext_id)
        if not card:
            for dest in body.destinations:
                results.append(ExportResultItem(
                    card_external_id=ext_id,
                    destination=dest,
                    result="error",
                    error_message="Card not found",
                ))
            continue

        for dest in body.destinations:
            item = await _export_one(db, card, dest)
            results.append(item)

    return ExportResponse(results=results)
```

- [ ] **Step 2: Register the export router in `app/main.py`**

In `app/main.py`, add the import alongside the other v2 imports:
```python
from app.routers.v2 import (
    sessions as v2_sessions,
    cards as v2_cards,
    persons as v2_persons,
    organizations as v2_organizations,
    occasions as v2_occasions,
    corrections as v2_corrections,
    settings as v2_settings,
    export as v2_export,
)
```

And register it after the other v2 routers:
```python
app.include_router(v2_export.router)
```

- [ ] **Step 3: Deploy and smoke test**

```bash
./deploy.sh
```

```bash
# Should return empty results array (no cards with these IDs)
curl -s -X POST http://localhost:8000/api/v2/export \
  -H "Authorization: Bearer $(grep VITE_API_KEY .env | cut -d= -f2)" \
  -H "Content-Type: application/json" \
  -d '{"card_external_ids": ["nonexistent-id"], "destinations": ["odoo"]}' \
  | python3 -m json.tool
```
Expected:
```json
{
  "results": [
    {
      "card_external_id": "nonexistent-id",
      "destination": "odoo",
      "result": "error",
      "error_message": "Card not found"
    }
  ]
}
```

- [ ] **Step 4: Commit**

```bash
git add app/routers/v2/export.py app/main.py
git commit -m "feat: add POST /api/v2/export endpoint with sync history recording"
```

---

### Task 6: Frontend types + API client additions

**Files:**
- Modify: `frontend/src/types/index.ts`
- Modify: `frontend/src/api/index.ts`

- [ ] **Step 1: Add export types to `frontend/src/types/index.ts`**

After the `MyCompany` interface, add:
```typescript
export interface CardSyncBadge {
  destination: string   // "odoo" | "google_contacts"
}

export interface ExportResultItem {
  card_external_id: string
  destination: string
  result: 'created' | 'updated' | 'error'
  error_message?: string
}

export interface ExportResponse {
  results: ExportResultItem[]
}
```

Also update `CardListItem` to include `synced_destinations`:
```typescript
export interface CardListItem {
  id: number
  external_id: string
  person_id: number
  received_date?: string
  sync_status: string
  created_at: string
  // Denormalized for list view
  person_name?: string
  front_image_path?: string
  synced_destinations: string[]   // add this line
}
```

- [ ] **Step 2: Add API functions to `frontend/src/api/index.ts`**

Replace the existing `listCards` function with one that accepts all filter params:
```typescript
export const listCards = (params?: {
  person_id?: number
  occasion_id?: number
  q?: string
  year?: number
  month?: string        // "YYYY-MM"
  date?: string         // "YYYY-MM-DD"
  not_exported?: boolean
  offset?: number
  limit?: number
}) => {
  const qs = new URLSearchParams()
  if (params?.person_id) qs.set('person_id', String(params.person_id))
  if (params?.occasion_id) qs.set('occasion_id', String(params.occasion_id))
  if (params?.q) qs.set('q', params.q)
  if (params?.year) qs.set('year', String(params.year))
  if (params?.month) qs.set('month', params.month)
  if (params?.date) qs.set('date', params.date)
  if (params?.not_exported) qs.set('not_exported', 'true')
  if (params?.offset) qs.set('offset', String(params.offset))
  if (params?.limit) qs.set('limit', String(params.limit))
  return get<CardListItem[]>(`/api/v2/cards?${qs}`)
}
```

Add the export function at the end (before `export * from './sessions'`):
```typescript
export const runExport = (body: {
  card_external_ids: string[]
  destinations: string[]
}) => post<import('../types').ExportResponse>('/api/v2/export', body)
```

- [ ] **Step 3: Build and check for TypeScript errors**

```bash
cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi/frontend"
npm run build 2>&1 | tail -20
```
Expected: Build succeeds with 0 TypeScript errors.

- [ ] **Step 4: Commit**

```bash
cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi"
git add frontend/src/types/index.ts frontend/src/api/index.ts
git commit -m "feat: add export types and listCards filter params to frontend API client"
```

---

### Task 7: i18n strings for export UI

**Files:**
- Modify: `frontend/src/i18n.ts`

- [ ] **Step 1: Add export strings to both `ja` and `en` objects in `i18n.ts`**

In the `ja` block, add after the existing entries:
```typescript
    // Export flow
    navExport: 'エクスポート',
    exportBtn: 'エクスポート',
    exportTitle: 'カードをエクスポート',
    exportSearchPlaceholder: '名前・会社・メール・電話で検索…',
    exportFilterYear: '年',
    exportFilterMonth: '月',
    exportFilterDate: '日付',
    exportFilterOccasion: '場面',
    exportFilterNotExported: '未エクスポートのみ',
    exportClearFilter: '✕',
    exportSelectAll: (n: number) => `${n} 件を全選択`,
    exportDeselectAll: '選択解除',
    exportNextBtn: (n: number) => `次へ: 宛先を選択 (${n} 件) →`,
    exportDestTitle: '宛先を選択',
    exportDestOdoo: 'Odoo',
    exportDestGoogle: 'Google Contacts',
    exportDestNotConfigured: '未設定',
    exportDestSetup: '設定 →',
    exportRunBtn: (n: number, dest: string) => `${n} 件を ${dest} にエクスポート`,
    exportResultCreated: '✓ 作成',
    exportResultUpdated: '✓ 更新',
    exportResultError: '✗ エラー',
    exportBackToList: '← カード一覧に戻る',
    exportAlreadySynced: '同期済み',

    // Duplicate check panel
    dupPanelTitle: '既存の連絡先が見つかりました',
    dupExisting: '既存',
    dupNewCard: '新しい名刺',
    dupDragHint: '右のフィールドを左にドラッグして取り込む',
    dupNotDuplicate: '別人として保存 →',
    dupDiscard: '新しい名刺を破棄',
    dupConfirmMerge: 'マージして保存',
```

In the `en` block, add after the existing entries:
```typescript
    // Export flow
    navExport: 'Export',
    exportBtn: 'Export',
    exportTitle: 'Export Cards',
    exportSearchPlaceholder: 'Search by name, company, email, phone…',
    exportFilterYear: 'Year',
    exportFilterMonth: 'Month',
    exportFilterDate: 'Date',
    exportFilterOccasion: 'Occasion',
    exportFilterNotExported: 'Not yet exported',
    exportClearFilter: '✕',
    exportSelectAll: (n: number) => `Select all ${n}`,
    exportDeselectAll: 'Deselect all',
    exportNextBtn: (n: number) => `Next: Choose destinations (${n} cards) →`,
    exportDestTitle: 'Choose destinations',
    exportDestOdoo: 'Odoo',
    exportDestGoogle: 'Google Contacts',
    exportDestNotConfigured: 'Not configured',
    exportDestSetup: 'Set up →',
    exportRunBtn: (n: number, dest: string) => `Export ${n} cards to ${dest}`,
    exportResultCreated: '✓ Created',
    exportResultUpdated: '✓ Updated',
    exportResultError: '✗ Error',
    exportBackToList: '← Back to card list',
    exportAlreadySynced: 'already synced',

    // Duplicate check panel
    dupPanelTitle: 'Existing contact found',
    dupExisting: 'Existing',
    dupNewCard: 'New card',
    dupDragHint: 'Drag fields from right column into the left to apply them',
    dupNotDuplicate: 'Not a duplicate →',
    dupDiscard: 'Discard new card',
    dupConfirmMerge: 'Confirm merge',
```

- [ ] **Step 2: Build to verify no TypeScript errors**

```bash
cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi/frontend"
npm run build 2>&1 | tail -10
```
Expected: 0 errors.

- [ ] **Step 3: Commit**

```bash
cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi"
git add frontend/src/i18n.ts
git commit -m "feat: add i18n strings for export flow and duplicate check panel"
```

---

### Task 8: ExportPage + ExportDestinationSelector

**Files:**
- Create: `frontend/src/pages/ExportPage.tsx`
- Create: `frontend/src/components/ExportDestinationSelector.tsx`

These are two logical screens in the same flow. `ExportPage` handles filter + card selection. `ExportDestinationSelector` handles destination multi-select + running the export + showing results.

- [ ] **Step 1: Create `frontend/src/components/ExportDestinationSelector.tsx`**

```typescript
/**
 * ExportDestinationSelector
 *
 * Second step of the export flow:
 *   - Multi-select destination checkboxes (Odoo, Google Contacts)
 *   - "Export N cards to …" button
 *   - Inline result list after export runs
 */
import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { runExport } from '../api'
import { useLang } from '../LangContext'
import type { ExportResultItem } from '../types'

interface Destination {
  key: string
  label: string
  configured: boolean
}

const DESTINATIONS: Destination[] = [
  { key: 'odoo', label: 'Odoo', configured: true },
  { key: 'google_contacts', label: 'Google Contacts', configured: true },
]

export function ExportDestinationSelector({
  cardExternalIds,
  onBack,
  onDone,
}: {
  cardExternalIds: string[]
  onBack: () => void
  onDone: () => void
}) {
  const { t } = useLang()
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [results, setResults] = useState<ExportResultItem[] | null>(null)

  const exportMutation = useMutation({
    mutationFn: () =>
      runExport({ card_external_ids: cardExternalIds, destinations: [...selected] }),
    onSuccess: (data) => setResults(data.results),
  })

  const toggle = (key: string) =>
    setSelected(prev => {
      const next = new Set(prev)
      next.has(key) ? next.delete(key) : next.add(key)
      return next
    })

  const destLabel = [...selected]
    .map(k => DESTINATIONS.find(d => d.key === k)?.label ?? k)
    .join(' + ')

  // If export ran, show results
  if (results) {
    const grouped: Record<string, ExportResultItem[]> = {}
    for (const r of results) {
      grouped[r.card_external_id] = grouped[r.card_external_id] ?? []
      grouped[r.card_external_id].push(r)
    }

    return (
      <div className="max-w-2xl mx-auto py-6 px-4 space-y-4">
        <h2 className="text-base font-semibold">{t.exportDestTitle}</h2>
        <div className="rounded-xl border border-gray-200 bg-white divide-y divide-gray-100 overflow-hidden">
          {Object.entries(grouped).map(([cardId, items]) => (
            <div key={cardId} className="px-4 py-3 flex items-center justify-between gap-4">
              <span className="text-sm text-gray-700 font-mono truncate max-w-[200px]">{cardId.slice(0, 8)}…</span>
              <div className="flex gap-2 flex-wrap justify-end">
                {items.map(item => (
                  <span
                    key={item.destination}
                    className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                      item.result === 'error'
                        ? 'bg-red-100 text-red-700'
                        : 'bg-green-100 text-green-700'
                    }`}
                    title={item.error_message ?? ''}
                  >
                    {item.destination}: {
                      item.result === 'created' ? t.exportResultCreated :
                      item.result === 'updated' ? t.exportResultUpdated :
                      t.exportResultError
                    }
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
        <button onClick={onDone} className="btn-secondary text-sm">
          {t.exportBackToList}
        </button>
      </div>
    )
  }

  return (
    <div className="max-w-2xl mx-auto py-6 px-4 space-y-4">
      <div className="flex items-center gap-3">
        <button onClick={onBack} className="text-sm text-blue-600 hover:text-blue-800">← Back</button>
        <h2 className="text-base font-semibold">{t.exportDestTitle}</h2>
      </div>

      <div className="rounded-xl border border-gray-200 bg-white divide-y divide-gray-100 overflow-hidden">
        {DESTINATIONS.map(dest => (
          <label
            key={dest.key}
            className={`flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-gray-50 ${!dest.configured ? 'opacity-50' : ''}`}
          >
            <input
              type="checkbox"
              checked={selected.has(dest.key)}
              disabled={!dest.configured}
              onChange={() => toggle(dest.key)}
              className="rounded border-gray-300"
            />
            <span className="text-sm font-medium text-gray-800">{dest.label}</span>
            {!dest.configured && (
              <span className="ml-auto text-xs text-gray-400">
                {t.exportDestNotConfigured}{' '}
                <a href="/settings" className="text-blue-500 underline">{t.exportDestSetup}</a>
              </span>
            )}
          </label>
        ))}
      </div>

      <div className="text-xs text-gray-400 text-center">
        {cardExternalIds.length} card{cardExternalIds.length !== 1 ? 's' : ''} selected
      </div>

      <button
        disabled={selected.size === 0 || exportMutation.isPending}
        onClick={() => exportMutation.mutate()}
        className="btn-primary w-full py-3 text-sm disabled:opacity-50"
      >
        {exportMutation.isPending
          ? 'Exporting…'
          : t.exportRunBtn(cardExternalIds.length, destLabel || '…')}
      </button>

      {exportMutation.isError && (
        <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded px-3 py-2">
          Export failed: {(exportMutation.error as Error).message}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Create `frontend/src/pages/ExportPage.tsx`**

```typescript
/**
 * ExportPage — two-step export flow.
 *
 * Step 1 (selection): Filter cards, select which ones to export.
 * Step 2 (destinations): Choose destinations, run export, view results.
 *
 * This is a full page — not a modal. Route: /export
 */
import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { listCards, listOccasions } from '../api'
import { useLang } from '../LangContext'
import { ExportDestinationSelector } from '../components/ExportDestinationSelector'
import type { CardListItem, Occasion } from '../types'

type Step = 'select' | 'destinations'

const DEST_BADGE_LABELS: Record<string, string> = {
  odoo: 'Odoo',
  google_contacts: 'Google',
}

export function ExportPage() {
  const { t } = useLang()
  const [step, setStep] = useState<Step>('select')
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())

  // Filters
  const [q, setQ] = useState('')
  const [yearFilter, setYearFilter] = useState<number | undefined>()
  const [monthFilter, setMonthFilter] = useState<string | undefined>()
  const [dateFilter, setDateFilter] = useState<string | undefined>()
  const [occasionFilter, setOccasionFilter] = useState<number | undefined>()
  const [notExported, setNotExported] = useState(false)

  const queryParams = {
    q: q || undefined,
    year: yearFilter,
    month: monthFilter,
    date: dateFilter,
    occasion_id: occasionFilter,
    not_exported: notExported || undefined,
    limit: 500,
  }

  const { data: cards = [], isLoading } = useQuery<CardListItem[]>({
    queryKey: ['export-cards', queryParams],
    queryFn: () => listCards(queryParams),
  })

  const { data: occasions = [] } = useQuery<Occasion[]>({
    queryKey: ['occasions'],
    queryFn: listOccasions,
  })

  // Active filter chips
  type Chip = { label: string; clear: () => void }
  const chips = useMemo<Chip[]>(() => {
    const out: Chip[] = []
    if (yearFilter) out.push({ label: `${t.exportFilterYear}: ${yearFilter}`, clear: () => setYearFilter(undefined) })
    if (monthFilter) out.push({ label: `${t.exportFilterMonth}: ${monthFilter}`, clear: () => setMonthFilter(undefined) })
    if (dateFilter) out.push({ label: `${t.exportFilterDate}: ${dateFilter}`, clear: () => setDateFilter(undefined) })
    if (occasionFilter) {
      const occ = occasions.find(o => o.id === occasionFilter)
      out.push({ label: occ?.name ?? `Occasion #${occasionFilter}`, clear: () => setOccasionFilter(undefined) })
    }
    if (notExported) out.push({ label: t.exportFilterNotExported, clear: () => setNotExported(false) })
    return out
  }, [yearFilter, monthFilter, dateFilter, occasionFilter, notExported, occasions, t])

  const toggleCard = (extId: string) =>
    setSelectedIds(prev => {
      const next = new Set(prev)
      next.has(extId) ? next.delete(extId) : next.add(extId)
      return next
    })

  const selectAll = () => setSelectedIds(new Set(cards.map(c => c.external_id)))
  const deselectAll = () => setSelectedIds(new Set())

  const currentYear = new Date().getFullYear()
  const yearOptions = Array.from({ length: 5 }, (_, i) => currentYear - i)

  if (step === 'destinations') {
    return (
      <ExportDestinationSelector
        cardExternalIds={[...selectedIds]}
        onBack={() => setStep('select')}
        onDone={() => { window.location.href = '/collection' }}
      />
    )
  }

  return (
    <div className="max-w-4xl mx-auto py-6 px-4 space-y-4">
      <h1 className="text-lg font-semibold text-gray-900">{t.exportTitle}</h1>

      {/* Search bar */}
      <input
        type="search"
        value={q}
        onChange={e => setQ(e.target.value)}
        placeholder={t.exportSearchPlaceholder}
        className="w-full border border-gray-300 rounded-lg px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
      />

      {/* Filter controls */}
      <div className="flex flex-wrap gap-2 items-center">
        {/* Year picker */}
        <select
          value={yearFilter ?? ''}
          onChange={e => setYearFilter(e.target.value ? Number(e.target.value) : undefined)}
          className="border border-gray-300 rounded px-2 py-1 text-xs"
        >
          <option value="">{t.exportFilterYear}</option>
          {yearOptions.map(y => <option key={y} value={y}>{y}</option>)}
        </select>

        {/* Month picker */}
        <input
          type="month"
          value={monthFilter ?? ''}
          onChange={e => setMonthFilter(e.target.value || undefined)}
          className="border border-gray-300 rounded px-2 py-1 text-xs"
          placeholder={t.exportFilterMonth}
        />

        {/* Date picker */}
        <input
          type="date"
          value={dateFilter ?? ''}
          onChange={e => setDateFilter(e.target.value || undefined)}
          className="border border-gray-300 rounded px-2 py-1 text-xs"
          placeholder={t.exportFilterDate}
        />

        {/* Occasion picker */}
        <select
          value={occasionFilter ?? ''}
          onChange={e => setOccasionFilter(e.target.value ? Number(e.target.value) : undefined)}
          className="border border-gray-300 rounded px-2 py-1 text-xs"
        >
          <option value="">{t.exportFilterOccasion}</option>
          {occasions.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}
        </select>

        {/* Not-exported toggle */}
        <label className="flex items-center gap-1.5 text-xs text-gray-600 cursor-pointer">
          <input
            type="checkbox"
            checked={notExported}
            onChange={e => setNotExported(e.target.checked)}
            className="rounded border-gray-300"
          />
          {t.exportFilterNotExported}
        </label>
      </div>

      {/* Active filter chips */}
      {chips.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {chips.map((chip, i) => (
            <span
              key={i}
              className="inline-flex items-center gap-1 bg-blue-100 text-blue-700 text-xs px-2 py-0.5 rounded-full"
            >
              {chip.label}
              <button onClick={chip.clear} className="hover:text-blue-900">{t.exportClearFilter}</button>
            </span>
          ))}
        </div>
      )}

      {/* Bulk actions */}
      <div className="flex items-center gap-3 text-sm">
        <button onClick={selectAll} className="text-blue-600 hover:text-blue-800">
          {t.exportSelectAll(cards.length)}
        </button>
        <span className="text-gray-300">|</span>
        <button onClick={deselectAll} className="text-gray-500 hover:text-gray-700">
          {t.exportDeselectAll}
        </button>
        <span className="text-gray-400 ml-auto text-xs">
          {isLoading ? 'Loading…' : `${cards.length} cards`}
        </span>
      </div>

      {/* Card list */}
      <div className="rounded-xl border border-gray-200 bg-white divide-y divide-gray-100 overflow-hidden">
        {cards.map(card => (
          <label
            key={card.external_id}
            className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-gray-50"
          >
            <input
              type="checkbox"
              checked={selectedIds.has(card.external_id)}
              onChange={() => toggleCard(card.external_id)}
              className="rounded border-gray-300 shrink-0"
            />
            {card.front_image_path && (
              <img
                src={`/api/v2/images/${card.front_image_path}`}
                alt=""
                className="h-10 w-auto rounded border border-gray-100 object-contain bg-gray-50 shrink-0"
              />
            )}
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-gray-800 truncate">
                {card.person_name ?? '—'}
              </p>
              <p className="text-xs text-gray-400">
                {card.received_date ?? card.created_at.slice(0, 10)}
              </p>
            </div>
            {/* Sync badges */}
            <div className="flex gap-1 shrink-0">
              {(card.synced_destinations ?? []).map(dest => (
                <span
                  key={dest}
                  className="text-xs bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded"
                  title={t.exportAlreadySynced}
                >
                  {DEST_BADGE_LABELS[dest] ?? dest}
                </span>
              ))}
            </div>
          </label>
        ))}
        {!isLoading && cards.length === 0 && (
          <div className="px-4 py-8 text-center text-sm text-gray-400">No cards match the current filters.</div>
        )}
      </div>

      {/* Footer */}
      <div className="sticky bottom-4">
        <button
          disabled={selectedIds.size === 0}
          onClick={() => setStep('destinations')}
          className="btn-primary w-full py-3 text-sm shadow-lg disabled:opacity-40"
        >
          {t.exportNextBtn(selectedIds.size)}
        </button>
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Commit**

```bash
cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi"
git add frontend/src/pages/ExportPage.tsx frontend/src/components/ExportDestinationSelector.tsx
git commit -m "feat: add ExportPage and ExportDestinationSelector components"
```

---

### Task 9: Wire up /export route + CollectionPage Export button

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/pages/CollectionPage.tsx`

- [ ] **Step 1: Add export route to `App.tsx`**

In `App.tsx`, import ExportPage at the top:
```typescript
import { ExportPage } from './pages/ExportPage'
```

In the `useRoute` function, add before the final `return 'collection'`:
```typescript
  if (path.startsWith('/export')) return 'export'
```

In the `Shell` component's `<main>` block, add `ExportPage` to the routing:
```typescript
      <main>
        {route === 'scan' ? <ScanPage />
          : route === 'settings' ? <SettingsPage />
          : route === 'export' ? <ExportPage />
          : route === 'card-detail' ? <CardDetailPage />
          : route === 'person-detail' ? <PersonDetailPage />
          : <CollectionPage />}
      </main>
```

In the `<nav>` links, add an Export link alongside Scan:
```typescript
          <a href="/export" className={`text-sm pb-px ${route === 'export' ? 'text-blue-600 font-medium border-b-2 border-blue-600' : 'text-gray-600 hover:text-gray-900'}`}>
            {t.navExport}
          </a>
```

Also update the document title map:
```typescript
      export: `${t.navExport} — ${t.appName}`,
```

- [ ] **Step 2: Add Export button to `CollectionPage.tsx`**

In `CollectionPage.tsx`, find the section with the view toggle buttons (cards/persons) and add an Export link button alongside it. Locate the heading row and add:

```typescript
          <a
            href="/export"
            className="btn-sm ml-auto"
          >
            {t.exportBtn}
          </a>
```

Place it in the flex row that contains the view toggle, e.g. directly before the `</div>` that closes the control bar.

- [ ] **Step 3: Build and verify**

```bash
cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi/frontend"
npm run build 2>&1 | tail -10
```
Expected: 0 TypeScript errors.

- [ ] **Step 4: Deploy and smoke-test in browser**

```bash
cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi"
./deploy.sh
```

Open `http://localhost:8000/export` in the browser. Expected: Export page loads with search bar, filter controls, and card list. Selecting cards and clicking "Next" navigates to the destination chooser.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.tsx frontend/src/pages/CollectionPage.tsx
git commit -m "feat: add /export route and Export nav button"
```

---

## Phase B — Duplicate Check

---

### Task 10: Add person_external_id to MatchResult

**Files:**
- Modify: `app/schemas/parsed_card.py`
- Modify: `app/services/contact_matcher.py`

The frontend needs the person's `external_id` (UUID string) to call `GET /api/v2/persons/{external_id}` and load the existing person's data for the left column. Currently `MatchResult` only has the internal integer `person_id`. We add `person_external_id` here.

- [ ] **Step 1: Update `MatchResult` in `app/schemas/parsed_card.py`**

Replace the existing `MatchResult` class with:
```python
class MatchResult(BaseModel):
    is_existing: bool = False
    person_id: Optional[int] = None           # local DB persons.id (internal)
    person_external_id: Optional[str] = None  # local DB persons.external_id (UUID)
    match_confidence: float = 0.0
    match_method: Optional[str] = None    # email, phone, name_exact, name_fuzzy
    matched_name: Optional[str] = None
```

- [ ] **Step 2: Update `contact_matcher.py` to populate `person_external_id`**

In `app/services/contact_matcher.py`, add a helper to fetch `external_id` by `person_id`. Add this function after the existing `_display_name` helper:

```python
async def _external_id(db: AsyncSession, person_id: int) -> Optional[str]:
    from sqlalchemy import select as _select
    from app.db.models import Person as _Person
    return await db.scalar(_select(_Person.external_id).where(_Person.id == person_id))
```

Then update each `return MatchResult(...)` call that sets `person_id` to also set `person_external_id`. There are three such calls (email match, phone match, name match). For each one:

**Email match** (around line 204):
```python
        ext_id = await _external_id(db, person_id)
        return MatchResult(
            is_existing=True,
            person_id=person_id,
            person_external_id=ext_id,
            match_confidence=1.0,
            match_method="email",
            matched_name=display,
        )
```

**Phone match** (around line 218):
```python
        ext_id = await _external_id(db, person_id)
        return MatchResult(
            is_existing=True,
            person_id=person_id,
            person_external_id=ext_id,
            match_confidence=0.95,
            match_method="phone",
            matched_name=display,
        )
```

**Name match** (around line 237):
```python
        ext_id = await _external_id(db, best[0])
        return MatchResult(
            is_existing=True,
            person_id=best[0],
            person_external_id=ext_id,
            match_confidence=best[2],
            match_method="name_fuzzy",
            matched_name=best[1],
        )
```

- [ ] **Step 3: Update frontend `MatchResult` type in `types/index.ts`**

Add `person_external_id` to the `MatchResult` interface:
```typescript
export interface MatchResult {
  is_existing: boolean
  person_id?: number
  person_external_id?: string   // add this line
  match_confidence: number
  match_method?: string
  matched_name?: string
}
```

Also update the `CardGroup` type in `ScanPage.tsx` to store it:
In `ScanPage.tsx`, add `matchPersonExtId?: string` to the `CardGroup` interface (alongside `matchPersonId`):
```typescript
interface CardGroup {
  tempCardId: string
  images: SessionImage[]
  parsed?: ParsedCard
  matchPersonId?: number
  matchPersonExtId?: string     // add this line
  matchName?: string
  matchConfidence?: number
  // ...rest unchanged
```

And in the `analyzeSession` event handler where match results are stored (the `result` event branch in `startAnalysis`):
```typescript
                    matchPersonId: event.match?.person_id,
                    matchPersonExtId: event.match?.person_external_id,  // add this line
```

Also update `SavedGroupState` to include `matchPersonExtId`:
```typescript
type SavedGroupState = Pick<
  CardGroup,
  'tempCardId' | 'parsed' | 'status' | 'matchPersonId' | 'matchPersonExtId' | 'matchName' | 'matchConfidence' |
  'myCompanyIds' | 'occasionId' | 'receivedDate' | 'notes' | 'error'
>
```

- [ ] **Step 4: Deploy and verify**

```bash
cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi"
./deploy.sh
```

```bash
# Trigger an analysis that produces a match (or use any existing match)
# and check the SSE output includes person_external_id
# Alternatively just verify the schema compiles by restarting without error
curl -s http://localhost:8000/api/v1/health | python3 -m json.tool
```
Expected: `{"status": "ok"}`

- [ ] **Step 5: Commit**

```bash
git add app/schemas/parsed_card.py app/services/contact_matcher.py \
        frontend/src/types/index.ts frontend/src/pages/ScanPage.tsx
git commit -m "feat: add person_external_id to MatchResult for duplicate check panel"
```

---

### Task 11: DuplicateFieldEditor component

**Files:**
- Create: `frontend/src/components/DuplicateFieldEditor.tsx`

This component shows an inline two-column editor:
- **Left column**: existing person's current fields (fetched from DB)
- **Right column**: new card's parsed fields (from `ParsedCard`)

Green highlight on right-column fields that differ from or are absent in the left column.
Drag from right to left to copy a field. ✕ on either side deletes the field.
Three action buttons: "Not a duplicate", "Discard new card", "Confirm merge".

The component manages a `ParsedCard` as its merged left-column state. It converts the existing `Person` DB record into a `ParsedCard`-shaped object so both columns share the same type.

- [ ] **Step 1: Create `frontend/src/components/DuplicateFieldEditor.tsx`**

```typescript
/**
 * DuplicateFieldEditor
 *
 * Inline two-column field editor shown when a scanned card matches an
 * existing person (matchConfidence >= 0.55).
 *
 * Left column  — existing person record (converted to ParsedCard shape)
 * Right column — new card from Claude OCR
 *
 * Drag a field from right → left to copy it into the existing record.
 * ✕ on either side removes the field from that column.
 * Green highlight on right-column fields that are new or different from left.
 *
 * Callbacks:
 *   onNotDuplicate()   — dismiss panel, save as new person
 *   onDiscard()        — remove this card from the session entirely
 *   onMerge(merged)    — save: merged ParsedCard is used to update existing person
 */
import { useState, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getPerson } from '../api'
import { useLang } from '../LangContext'
import type {
  ParsedCard,
  ParsedContactDetail,
  ParsedName,
  ParsedPosition,
  Person,
} from '../types'

// ---------------------------------------------------------------------------
// Conversion: Person (DB shape) → ParsedCard (editor shape)
// All confidence values are set to 1.0 for existing data.
// ---------------------------------------------------------------------------

function personToParsedCard(person: Person): ParsedCard {
  const names: ParsedName[] = person.names
    .filter(n => n.is_current)
    .map(n => ({
      language: n.language,
      name_type: n.name_type,
      family_name: n.family_name ? { value: n.family_name, confidence: 1.0 } : undefined,
      given_name: n.given_name ? { value: n.given_name, confidence: 1.0 } : undefined,
      full_name: { value: n.full_name, confidence: 1.0 },
    }))

  const positions: ParsedPosition[] = person.positions
    .filter(p => p.status === 'current')
    .map(pos => ({
      org_names: pos.org_names
        .filter(on => on.is_current)
        .map(on => ({ language: on.language, name: { value: on.name, confidence: 1.0 } })),
      details: pos.details.map(pd => ({
        language: pd.language,
        title: pd.title ? { value: pd.title, confidence: 1.0 } : undefined,
        department: pd.department ? { value: pd.department, confidence: 1.0 } : undefined,
      })),
    }))

  const contact_details: ParsedContactDetail[] = person.contact_details.map(cd => ({
    detail_type: cd.detail_type,
    value: { value: cd.value, confidence: 1.0 },
    label: cd.label,
  }))

  return {
    names,
    positions,
    contact_details,
    languages_detected: [...new Set(names.map(n => n.language))],
    overall_confidence: 1.0,
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fieldKey(cd: ParsedContactDetail): string {
  return `${cd.detail_type}:${cd.value.value}`
}

function isNewOrDifferent(
  right: ParsedContactDetail,
  leftDetails: ParsedContactDetail[],
): boolean {
  const sameType = leftDetails.filter(l => l.detail_type === right.detail_type)
  if (sameType.length === 0) return true  // type doesn't exist on left
  return !sameType.some(l => l.value.value === right.value.value)
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ContactDetailRow({
  cd,
  highlight,
  onDelete,
  draggable,
  onDragStart,
}: {
  cd: ParsedContactDetail
  highlight: boolean
  onDelete: () => void
  draggable?: boolean
  onDragStart?: (e: React.DragEvent) => void
}) {
  return (
    <div
      className={`flex items-center gap-2 px-2 py-1 rounded text-xs group ${highlight ? 'bg-green-50 border border-green-200' : 'bg-gray-50 border border-gray-200'}`}
      draggable={draggable}
      onDragStart={onDragStart}
    >
      {draggable && (
        <span className="cursor-grab text-gray-400 select-none" title="Drag to apply">⠿</span>
      )}
      <span className="text-gray-400 shrink-0 w-24 truncate">{cd.detail_type.replace(/_/g, ' ')}</span>
      <span className={`flex-1 font-medium truncate ${highlight ? 'text-green-700' : 'text-gray-800'}`}>
        {cd.value.value}
      </span>
      <button
        onClick={onDelete}
        className="ml-auto text-gray-300 hover:text-red-400 opacity-0 group-hover:opacity-100 transition-opacity"
        title="Remove field"
      >✕</button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function DuplicateFieldEditor({
  personExtId,
  newCard,
  matchName,
  matchConfidence,
  onNotDuplicate,
  onDiscard,
  onMerge,
}: {
  personExtId: string
  newCard: ParsedCard
  matchName?: string
  matchConfidence?: number
  onNotDuplicate: () => void
  onDiscard: () => void
  onMerge: (mergedCard: ParsedCard) => void
}) {
  const { t } = useLang()

  const { data: person, isLoading } = useQuery<Person>({
    queryKey: ['person', personExtId],
    queryFn: () => getPerson(personExtId),
  })

  // Left column state — starts as the existing person's data
  const [leftCard, setLeftCard] = useState<ParsedCard | null>(null)
  useEffect(() => {
    if (person && !leftCard) {
      setLeftCard(personToParsedCard(person))
    }
  }, [person, leftCard])

  const [isDragOver, setIsDragOver] = useState(false)

  if (isLoading || !leftCard) {
    return (
      <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-6 text-center text-sm text-amber-600">
        Loading existing contact…
      </div>
    )
  }

  const handleDropFromRight = (cd: ParsedContactDetail) => {
    setLeftCard(prev => {
      if (!prev) return prev
      // Remove any existing entry with the same type+value (dedup), then append
      const filtered = prev.contact_details.filter(l => fieldKey(l) !== fieldKey(cd))
      return { ...prev, contact_details: [...filtered, cd] }
    })
  }

  const deleteLeft = (cd: ParsedContactDetail) => {
    setLeftCard(prev => {
      if (!prev) return prev
      return { ...prev, contact_details: prev.contact_details.filter(l => fieldKey(l) !== fieldKey(cd)) }
    })
  }

  const deleteRight = (cd: ParsedContactDetail) => {
    // We track deleted right-side fields so they don't show as suggestions
    setRightDeleted(prev => new Set(prev).add(fieldKey(cd)))
  }

  const [rightDeleted, setRightDeleted] = useState<Set<string>>(new Set())
  const rightDetails = newCard.contact_details.filter(cd => !rightDeleted.has(fieldKey(cd)))

  return (
    <div className="rounded-xl border border-amber-300 bg-amber-50 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 bg-amber-100 border-b border-amber-200">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-amber-800">
            {t.dupPanelTitle}
          </span>
          <span className="text-xs text-amber-600">
            {matchName} ({Math.round((matchConfidence ?? 0) * 100)}%)
          </span>
        </div>
        <button
          onClick={onNotDuplicate}
          className="text-xs text-blue-600 hover:text-blue-800 font-medium"
        >
          {t.dupNotDuplicate}
        </button>
      </div>

      {/* Two-column editor */}
      <div className="grid grid-cols-2 gap-0 divide-x divide-amber-200">
        {/* Left: existing person */}
        <div
          className={`p-3 space-y-1 min-h-[120px] transition-colors ${isDragOver ? 'bg-green-50' : ''}`}
          onDragOver={e => { e.preventDefault(); setIsDragOver(true) }}
          onDragLeave={e => {
            if (!e.currentTarget.contains(e.relatedTarget as Node)) setIsDragOver(false)
          }}
          onDrop={e => {
            e.preventDefault()
            setIsDragOver(false)
            const raw = e.dataTransfer.getData('cd')
            if (raw) {
              try { handleDropFromRight(JSON.parse(raw)) } catch { /* ignore */ }
            }
          }}
        >
          <p className="text-xs font-semibold text-gray-500 mb-2 uppercase tracking-wide">
            {t.dupExisting}
          </p>
          {/* Names (display-only, not editable in MVP) */}
          {leftCard.names.slice(0, 1).map((n, i) => (
            <div key={i} className="text-sm font-medium text-gray-800 mb-1">{n.full_name.value}</div>
          ))}
          {/* Contact details */}
          {leftCard.contact_details.map((cd, i) => (
            <ContactDetailRow
              key={`${fieldKey(cd)}-${i}`}
              cd={cd}
              highlight={false}
              onDelete={() => deleteLeft(cd)}
            />
          ))}
          {isDragOver && (
            <div className="border-2 border-dashed border-green-400 rounded px-2 py-1 text-xs text-green-600 text-center">
              Drop here
            </div>
          )}
        </div>

        {/* Right: new card */}
        <div className="p-3 space-y-1">
          <p className="text-xs font-semibold text-gray-500 mb-2 uppercase tracking-wide">
            {t.dupNewCard}
          </p>
          {/* Names */}
          {newCard.names.slice(0, 1).map((n, i) => (
            <div key={i} className="text-sm font-medium text-gray-800 mb-1">{n.full_name.value}</div>
          ))}
          {/* Contact details */}
          {rightDetails.map((cd, i) => {
            const highlight = isNewOrDifferent(cd, leftCard.contact_details)
            return (
              <ContactDetailRow
                key={`${fieldKey(cd)}-${i}`}
                cd={cd}
                highlight={highlight}
                onDelete={() => deleteRight(cd)}
                draggable
                onDragStart={e => {
                  e.dataTransfer.setData('cd', JSON.stringify(cd))
                  e.dataTransfer.effectAllowed = 'copy'
                }}
              />
            )
          })}
          {rightDetails.length === 0 && (
            <p className="text-xs text-gray-400 italic">No additional fields</p>
          )}
          <p className="text-xs text-gray-400 mt-2">{t.dupDragHint}</p>
        </div>
      </div>

      {/* Footer actions */}
      <div className="flex items-center justify-between gap-2 px-4 py-3 bg-amber-50 border-t border-amber-200">
        <button
          onClick={onDiscard}
          className="text-xs text-red-500 hover:text-red-700"
        >
          {t.dupDiscard}
        </button>
        <button
          onClick={() => onMerge(leftCard)}
          className="btn-primary text-sm px-4 py-1.5"
        >
          {t.dupConfirmMerge}
        </button>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Build to check for TypeScript errors**

```bash
cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi/frontend"
npm run build 2>&1 | tail -20
```
Expected: 0 TypeScript errors.

- [ ] **Step 3: Commit**

```bash
cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi"
git add frontend/src/components/DuplicateFieldEditor.tsx
git commit -m "feat: add DuplicateFieldEditor two-column merge component"
```

---

### Task 12: Integrate DuplicateFieldEditor into ScanPage

**Files:**
- Modify: `frontend/src/pages/ScanPage.tsx`

The `DuplicateFieldEditor` appears **inline within the review step**, inside `CardGroupCard`, immediately below the `ParsedCardEditor`. It is shown when `group.matchConfidence >= 0.55` AND `group.matchPersonExtId` is set AND the user has not already dismissed it ("not a duplicate" action sets a dismissed flag).

Three actions:
- **"Not a duplicate →"**: set a per-group `dupDismissed: true` flag. Card saves as new person (`matchPersonId` cleared).
- **"Discard new card"**: remove this group from the session (set `group.status = 'discarded'` and filter it out on confirm).
- **"Confirm merge"**: update `group.parsed` with the merged `ParsedCard`, keep `matchPersonId`. Confirm proceeds normally.

- [ ] **Step 1: Add `dupDismissed` to `CardGroup` type and `SavedGroupState`**

In `ScanPage.tsx`, add to the `CardGroup` interface:
```typescript
  dupDismissed?: boolean   // user clicked "Not a duplicate"
  discarded?: boolean      // user clicked "Discard new card"
```

Add `'dupDismissed' | 'discarded'` to `SavedGroupState`:
```typescript
type SavedGroupState = Pick<
  CardGroup,
  'tempCardId' | 'parsed' | 'status' | 'matchPersonId' | 'matchPersonExtId' | 'matchName' | 'matchConfidence' |
  'dupDismissed' | 'discarded' |
  'myCompanyIds' | 'occasionId' | 'receivedDate' | 'notes' | 'error'
>
```

- [ ] **Step 2: Update `handleConfirm` to skip discarded groups**

In `handleConfirm`, update the filter to exclude discarded groups:
```typescript
    const cards: CardDraft[] = groups
      .filter(g => g.parsed && !g.discarded)
      .map(g => ({
        temp_card_id: g.tempCardId,
        parsed: g.parsed!,
        match_person_id: g.matchPersonId,
        my_company_ids: g.myCompanyIds,
        occasion_id: g.occasionId,
        received_date: g.receivedDate,
        notes: g.notes,
      }))
```

Also update the "Save N cards" button count label:
```typescript
              {t.saveN(groups.filter(g => g.parsed && !g.discarded).length)}
```

- [ ] **Step 3: Add `DuplicateFieldEditor` import and show it in `CardGroupCard`**

At the top of `ScanPage.tsx`, add the import:
```typescript
import { DuplicateFieldEditor } from '../components/DuplicateFieldEditor'
```

In `CardGroupCard`'s props interface, add:
```typescript
  onDupNotDuplicate: (groupId: string) => void
  onDupDiscard: (groupId: string) => void
  onDupMerge: (groupId: string, mergedCard: ParsedCard) => void
```

Inside `CardGroupCard`'s JSX, find the `{group.parsed && stage === 'review' && ...}` section (around line 1098) and add the `DuplicateFieldEditor` block directly after `ParsedCardEditor`:

```typescript
          {group.parsed && stage === 'review' && (
            <ParsedCardEditor parsed={group.parsed} onChange={onParsedChange} onCorrection={onCorrection} />
          )}
          {/* Duplicate check panel — shown when confident match exists and not dismissed */}
          {group.parsed && stage === 'review' &&
           group.matchPersonExtId &&
           (group.matchConfidence ?? 0) >= 0.55 &&
           !group.dupDismissed &&
           !group.discarded && (
            <div className="mt-3">
              <DuplicateFieldEditor
                personExtId={group.matchPersonExtId}
                newCard={group.parsed}
                matchName={group.matchName}
                matchConfidence={group.matchConfidence}
                onNotDuplicate={() => onDupNotDuplicate(group.tempCardId)}
                onDiscard={() => onDupDiscard(group.tempCardId)}
                onMerge={merged => onDupMerge(group.tempCardId, merged)}
              />
            </div>
          )}
```

- [ ] **Step 4: Wire up the three callbacks from `ScanPage` to `CardGroupCard`**

In `ScanPage`, add three handlers (alongside the existing `onParsedChange` / `onMetaChange` pattern):

```typescript
              onDupNotDuplicate={groupId =>
                setGroups(prev =>
                  prev.map(g => g.tempCardId === groupId
                    ? { ...g, dupDismissed: true, matchPersonId: undefined, matchPersonExtId: undefined }
                    : g
                  )
                )
              }
              onDupDiscard={groupId =>
                setGroups(prev =>
                  prev.map(g => g.tempCardId === groupId ? { ...g, discarded: true } : g)
                )
              }
              onDupMerge={(groupId, mergedCard) =>
                setGroups(prev =>
                  prev.map(g => g.tempCardId === groupId
                    ? { ...g, parsed: mergedCard, dupDismissed: true }
                    : g
                  )
                )
              }
```

Also update the `CardGroupCard` call-site in ScanPage to include the three new props. The `CardGroupCard` invocation is already a large block around line 696 — add the three props to it.

- [ ] **Step 5: Build and check for TypeScript errors**

```bash
cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi/frontend"
npm run build 2>&1 | tail -20
```
Expected: 0 TypeScript errors.

- [ ] **Step 6: Deploy and end-to-end smoke test**

```bash
cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi"
./deploy.sh
```

Manual smoke test:
1. Open `http://localhost:8000/scan`
2. Upload a card image of a person already in the DB
3. Proceed through grouping → analysis
4. In review stage, if confidence ≥ 55%: the `DuplicateFieldEditor` panel should appear below the `ParsedCardEditor`
5. Verify left column shows existing person's contact details
6. Verify different/new fields on the right are highlighted green
7. Drag a right-column field to the left — verify it appears in left column
8. Click "Confirm merge" — verify the scan completes and the card is saved under the existing person

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/ScanPage.tsx
git commit -m "feat: integrate DuplicateFieldEditor into scan review step"
```

---

## Self-Review Checklist

### Spec coverage

| Spec requirement | Task |
|---|---|
| Duplicate panel shown inline in review step at confidence ≥ 0.55 | Task 12 |
| Two-column editor: left=existing, right=new card | Task 11 |
| Green highlight for new/different right-column fields | Task 11 |
| Drag ⠿ from right to left | Task 11 |
| ✕ delete on either column | Task 11 |
| "Not a duplicate →" action | Task 12 |
| "Discard new card" action | Task 12 |
| "Confirm merge" action — saves left column, links card image | Task 12 |
| Append-only name history preserved | Handled by existing `_upsert_person_names` in sessions.py |
| New Card + CardSide record linked to existing person | Handled by existing confirm endpoint (match_person_id) |
| No automatic export triggered on confirm | Task 2 (v1 confirm → 410) |
| Export never triggered automatically | Task 2 + existing v2 sessions confirm has no sync calls |
| Export flow: Card List → Export Selection → Destinations → Result | Tasks 8–9 |
| Full-text search across all fields | Task 4 |
| Filter chips: year, month, date, occasion, not_exported | Tasks 4, 8 |
| AND logic for all filters | Task 4 |
| Checkbox selection per row | Task 8 |
| Sync history badges per row | Tasks 1, 3, 4, 8 |
| Select all N / Deselect all | Task 8 |
| Footer disabled until ≥1 selected | Task 8 |
| Destination multi-select with unconfigured grayed + Setup link | Task 8 ExportDestinationSelector |
| "already synced" informational note | Task 8 |
| Dynamic export button label | Task 8 |
| Results shown inline on destination screen | Task 8 |
| ← Back to card list | Task 8 |
| CardSyncHistory table | Task 1 |
| OneDrive NOT in export UI | Confirmed — only odoo and google_contacts in DESTINATIONS |

### Items out of scope (per spec §3)

- Duplicate check against Odoo/Google (external DBs)
- Field-level export selection
- Automatic re-export on card edit
- OneDrive as selectable export destination

### Known limitations in this plan

- The `DuplicateFieldEditor` merges only `contact_details` via drag-and-drop. Names and positions are displayed but not interactively merged (the spec says "drag a field from new card to existing" but doesn't explicitly require names to be draggable — names are already handled by the confirm upsert). This is intentional MVP scope — names from the left column (existing person) are preserved by the confirm flow.
- The `ExportDestinationSelector` hardcodes `configured: true` for both destinations. A future task should check actual credential availability from the settings API.
