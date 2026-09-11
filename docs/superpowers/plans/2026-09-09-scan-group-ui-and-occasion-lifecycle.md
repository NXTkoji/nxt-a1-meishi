# Scan Group UI & Occasion Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Scan → Group stage self-explanatory and reliable, and stop occasions from losing their name (and searchability) when deleted.

**Architecture:** Backend first — one Alembic column (`cards.occasion_label`), a stamp-on-delete in the occasions router, a card count on `OccasionOut`, and an occasion branch in the `?q=` search. Then the frontend, starting with the rotation cache-bust fix (which unblocks confident manual testing of everything after it), then tile sizing, then the two-row grouping layout, then copy and i18n.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async + Alembic + pytest (backend); React 19 + TypeScript + TanStack Query + Tailwind + Vite (frontend). No frontend test runner exists — frontend verification is `tsc -b` plus explicit browser checks.

**Spec:** `docs/superpowers/specs/2026-09-09-scan-group-ui-and-occasion-lifecycle-design.md`

---

## Before you start

**Branch off `main`, not the current branch.** The spec was committed on `docs/community-edition-spec`; implementation must not land there.

```bash
git fetch origin
git checkout -b feat/scan-group-ui-and-occasions origin/main
git checkout docs/community-edition-spec -- docs/superpowers/specs/2026-09-09-scan-group-ui-and-occasion-lifecycle-design.md docs/superpowers/plans/2026-09-09-scan-group-ui-and-occasion-lifecycle.md
git commit -m "docs: bring scan-group-ui spec and plan onto the feature branch"
```

**Two facts that will bite you if you forget them:**

1. **Frontend source edits do nothing until you build.** Run `cd frontend && npm run build` (this also runs `tsc -b`). The bundle filename is content-hashed, so the browser will serve the old one — **hard-refresh with Cmd+Shift+R** every time.
2. **Backend changes need the LaunchAgent reloaded**, not just restarted:
   ```bash
   launchctl unload ~/Library/LaunchAgents/co.nxta.nxt-a1-backend.plist
   launchctl load  ~/Library/LaunchAgents/co.nxta.nxt-a1-backend.plist
   ```
   Two supervisors manage uvicorn on :8000. Confirm the live process with `lsof -ti :8000 | xargs ps -p` rather than trusting the log tail.

**Never run migrations against `~/.nxt-a1/meishi.db` by hand.** The app runs Alembic at startup. Tests use a throwaway temp SQLite file via the `client_with_test_db` fixture; entering `TestClient` as a context manager would trigger lifespan and hit the production DB, which is why `tests/conftest.py` deliberately does not.

---

## File Structure

**Backend — create:**

| File | Responsibility |
|---|---|
| `migrations/versions/f2a3b4c5d6e7_add_occasion_label_to_cards.py` | Adds one nullable column |
| `tests/test_occasion_lifecycle.py` | All seven occasion tests (delete-stamps, count, search, DTO fallback) |

**Backend — modify:**

| File | Change |
|---|---|
| `app/db/models.py:326` | `Card.occasion_label` column |
| `app/routers/v2/occasions.py:25` | `list_occasions` returns `card_count` |
| `app/routers/v2/occasions.py:55` | `delete_occasion` stamps the label before deleting |
| `app/schemas/api.py:299` | `OccasionOut.card_count` |
| `app/routers/v2/cards.py:112` | occasion branch in the `?q=` search |
| `app/services/legacy_card.py:110` | fall back to `occasion_label` |

**Frontend — create:**

| File | Responsibility |
|---|---|
| `frontend/src/lib/occasionGrouping.ts` | Sole owner of the `event_date ?? created_at` fallback and the year/month bucketing. Used by both the Scan picker and Settings, so the rule lives in exactly one place. |

**Frontend — modify:**

| File | Change |
|---|---|
| `frontend/src/components/DropZone.tsx` | `compact` prop |
| `frontend/src/components/CardOutlineSelector.tsx` | i18n + instruction copy |
| `frontend/src/pages/ScanPage.tsx` | cache-bust fix, tiles, two rows, help panel, readiness gate, tips, occasion picker grouping |
| `frontend/src/pages/SettingsPage.tsx` | occasion year/month tree, delete warning with count |
| `frontend/src/pages/CollectionPage.tsx` | search placeholder only |
| `frontend/src/types/index.ts` | `Occasion.card_count` |
| `frontend/src/i18n.ts` | ~30 new keys × 3 languages |

`ScanPage.tsx` is 1409 lines and doing a lot. This plan does **not** restructure it — the changes are localised and a split would balloon the diff. Extracting `occasionGrouping.ts` is the one exception, justified because two pages need the same rule.

---

## Task 1: Add the `occasion_label` column

**Files:**
- Create: `migrations/versions/f2a3b4c5d6e7_add_occasion_label_to_cards.py`
- Modify: `app/db/models.py:326`

- [ ] **Step 1: Confirm the current Alembic head is what this migration chains onto**

Run:
```bash
cd nxt-a1-meishi && venv/bin/python3 -c "
import os,re
d='migrations/versions'; revs={}; downs=set()
for f in os.listdir(d):
    if not f.endswith('.py'): continue
    s=open(os.path.join(d,f)).read()
    r=re.search(r'^revision(?::.*?)? = [\'\"]([^\'\"]+)', s, re.M)
    dn=re.search(r'^down_revision(?::.*?)? = [\'\"]([^\'\"]+)', s, re.M)
    if r: revs[r.group(1)]=f
    if dn: downs.add(dn.group(1))
print('HEAD:', [r for r in revs if r not in downs])
"
```
Expected: `HEAD: ['e1f2a3b4c5d6']`

If it prints anything else, someone landed a migration after this plan was written — use **that** revision as `down_revision` below instead of `e1f2a3b4c5d6`.

- [ ] **Step 2: Write the migration**

Create `migrations/versions/f2a3b4c5d6e7_add_occasion_label_to_cards.py`:

```python
"""add_occasion_label_to_cards

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-09-09

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f2a3b4c5d6e7'
down_revision: Union[str, None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('cards', sa.Column('occasion_label', sa.String(256), nullable=True))


def downgrade() -> None:
    op.drop_column('cards', 'occasion_label')
```

No backfill: every existing card has a live `occasion_id`, so the resolution rule (link wins) already returns the right name for them.

- [ ] **Step 3: Add the column to the model**

In `app/db/models.py`, immediately after the `occasion_id` line (currently line 326):

```python
    occasion_id: Mapped[Optional[int]] = mapped_column(ForeignKey("occasions.id"))
    # Plain-text occasion name, stamped in when the linked Occasion is deleted so the
    # card keeps a human-readable record of where it came from. Only consulted when
    # occasion_id is NULL — a live link always wins, so renames still propagate.
    occasion_label: Mapped[Optional[str]] = mapped_column(String(256))
```

- [ ] **Step 4: Verify the model imports and the schema builds**

Run:
```bash
cd nxt-a1-meishi && PYTHONPATH=. venv/bin/python3 -c "
from app.db.models import Card
print('occasion_label' in Card.__table__.columns)
"
```
Expected: `True`

- [ ] **Step 5: Commit**

```bash
git add app/db/models.py migrations/versions/f2a3b4c5d6e7_add_occasion_label_to_cards.py
git commit -m "feat: add cards.occasion_label for preserving occasion names on delete"
```

---

## Task 2: Stamp the label when an occasion is deleted

**Files:**
- Create: `tests/test_occasion_lifecycle.py`
- Modify: `app/routers/v2/occasions.py:55-60`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_occasion_lifecycle.py`:

```python
"""Occasion lifecycle: deleting an occasion must not lose the occasion name.

These are data-backed tests, not signature smoke tests — the delete path has
real cards riding on it (48 on one occasion in the live database).
"""
import asyncio

import pytest

from app.db.session import get_db
from app.main import app


def _seed(client, occasion_name="RI Convention", n_cards=3, label=None):
    """Create one person, one occasion, and n cards linked to it.

    Returns (occasion_id, [card_ids]).
    """
    holder = {}

    async def _run():
        from app.db.models import Card, Occasion, Person

        async for db in app.dependency_overrides[get_db]():
            person = Person(external_id="p-occ")
            db.add(person)
            await db.flush()

            occ = Occasion(name=occasion_name)
            db.add(occ)
            await db.flush()

            ids = []
            for i in range(n_cards):
                card = Card(
                    external_id=f"c-occ-{i}",
                    person_id=person.id,
                    occasion_id=occ.id,
                    occasion_label=label,
                )
                db.add(card)
                await db.flush()
                ids.append(card.id)

            holder["occasion_id"] = occ.id
            holder["card_ids"] = ids
            await db.commit()
            break

    asyncio.run(_run())
    return holder["occasion_id"], holder["card_ids"]


def _load_cards(client, card_ids):
    """Re-read cards from a fresh session so we see committed state."""
    holder = {}

    async def _run():
        from sqlalchemy import select

        from app.db.models import Card

        async for db in app.dependency_overrides[get_db]():
            rows = (await db.execute(select(Card).where(Card.id.in_(card_ids)))).scalars().all()
            holder["rows"] = [
                {"id": c.id, "occasion_id": c.occasion_id, "occasion_label": c.occasion_label}
                for c in rows
            ]
            break

    asyncio.run(_run())
    return holder["rows"]


def test_delete_stamps_label_onto_cards(client_with_test_db):
    occ_id, card_ids = _seed(client_with_test_db)

    resp = client_with_test_db.delete(f"/api/v2/occasions/{occ_id}")
    assert resp.status_code == 204

    for row in _load_cards(client_with_test_db, card_ids):
        assert row["occasion_id"] is None
        assert row["occasion_label"] == "RI Convention"


def test_delete_does_not_delete_cards(client_with_test_db):
    occ_id, card_ids = _seed(client_with_test_db, n_cards=5)

    client_with_test_db.delete(f"/api/v2/occasions/{occ_id}")

    rows = _load_cards(client_with_test_db, card_ids)
    assert len(rows) == 5


def test_delete_preserves_existing_label(client_with_test_db):
    """A card that already carries a label from an earlier deletion is not overwritten."""
    occ_id, card_ids = _seed(
        client_with_test_db, occasion_name="Second Event", label="Original Event"
    )

    client_with_test_db.delete(f"/api/v2/occasions/{occ_id}")

    for row in _load_cards(client_with_test_db, card_ids):
        assert row["occasion_label"] == "Original Event"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd nxt-a1-meishi && venv/bin/python3 -m pytest tests/test_occasion_lifecycle.py -v`

Expected: all three FAIL. `test_delete_stamps_label_onto_cards` fails on `assert row["occasion_label"] == "RI Convention"` — it is `None`, because nothing stamps it yet.

- [ ] **Step 3: Stamp the label in the delete endpoint**

In `app/routers/v2/occasions.py`, change the imports at the top:

```python
from sqlalchemy import select, update
```

and add `Card` to the models import:

```python
from app.db.models import Card, Occasion
```

Then replace the `delete_occasion` body (currently lines 55-60):

```python
@router.delete("/{occasion_id}", status_code=204)
async def delete_occasion(occasion_id: int, db: AsyncSession = Depends(get_db)):
    occ = await db.get(Occasion, occasion_id)
    if not occ:
        raise HTTPException(404, "Occasion not found")

    # Stamp the name onto every card that points here BEFORE deleting, so the card
    # keeps a readable record. SQLAlchemy will null out occasion_id as part of the
    # delete; occasion_label is a different column and survives that.
    # Cards that already carry a label (from an earlier deletion) are left alone.
    await db.execute(
        update(Card)
        .where(Card.occasion_id == occasion_id, Card.occasion_label.is_(None))
        .values(occasion_label=occ.name)
        .execution_options(synchronize_session=False)
    )

    await db.delete(occ)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd nxt-a1-meishi && venv/bin/python3 -m pytest tests/test_occasion_lifecycle.py -v`

Expected: 3 passed.

- [ ] **Step 5: Run the whole backend suite for regressions**

Run: `cd nxt-a1-meishi && venv/bin/python3 -m pytest tests/ -q`

Expected: all pass. If `test_legacy_card.py` fails, stop — that is Task 5's territory and means the DTO already reads the new column somewhere unexpected.

- [ ] **Step 6: Commit**

```bash
git add app/routers/v2/occasions.py tests/test_occasion_lifecycle.py
git commit -m "feat: preserve occasion name on cards when the occasion is deleted"
```

---

## Task 3: Report a card count per occasion

**Files:**
- Modify: `app/schemas/api.py:299-307`, `app/routers/v2/occasions.py:25-31`
- Test: `tests/test_occasion_lifecycle.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_occasion_lifecycle.py`:

```python
def test_list_returns_card_count(client_with_test_db):
    occ_id, _ = _seed(client_with_test_db, n_cards=4)

    resp = client_with_test_db.get("/api/v2/occasions")
    assert resp.status_code == 200

    row = next(o for o in resp.json() if o["id"] == occ_id)
    assert row["card_count"] == 4


def test_card_count_excludes_soft_deleted_cards(client_with_test_db):
    from datetime import datetime

    occ_id, card_ids = _seed(client_with_test_db, n_cards=3)

    async def _soft_delete_one():
        from app.db.models import Card

        async for db in app.dependency_overrides[get_db]():
            card = await db.get(Card, card_ids[0])
            card.deleted_at = datetime.utcnow()
            await db.commit()
            break

    asyncio.run(_soft_delete_one())

    resp = client_with_test_db.get("/api/v2/occasions")
    row = next(o for o in resp.json() if o["id"] == occ_id)
    assert row["card_count"] == 2
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd nxt-a1-meishi && venv/bin/python3 -m pytest tests/test_occasion_lifecycle.py -k card_count -v`

Expected: FAIL with `KeyError: 'card_count'`.

- [ ] **Step 3: Add the field to the schema**

In `app/schemas/api.py`, `OccasionOut` becomes:

```python
class OccasionOut(BaseModel):
    id: int
    name: str
    event_date: Optional[date]
    location: Optional[str]
    notes: Optional[str]
    created_at: datetime
    # Live (not soft-deleted) cards using this occasion. Filled in by list_occasions;
    # defaults to 0 for the create/update responses, which do not compute it.
    card_count: int = 0

    model_config = {"from_attributes": True}
```

- [ ] **Step 4: Compute it in `list_occasions`**

In `app/routers/v2/occasions.py`, extend the imports:

```python
from sqlalchemy import func, select, update
```

Replace `list_occasions` (currently lines 25-31):

```python
@router.get("", response_model=List[OccasionOut])
async def list_occasions(db: AsyncSession = Depends(get_db)):
    # Correlated count of live cards per occasion — one query, no N+1.
    card_count = (
        select(func.count(Card.id))
        .where(Card.occasion_id == Occasion.id, Card.deleted_at.is_(None))
        .scalar_subquery()
    )
    rows = (await db.execute(
        select(Occasion, card_count).order_by(
            Occasion.event_date.desc().nullslast(), Occasion.created_at.desc()
        )
    )).all()

    out = []
    for occ, count in rows:
        item = OccasionOut.model_validate(occ)
        item.card_count = count
        out.append(item)
    return out
```

- [ ] **Step 5: Run to verify they pass**

Run: `cd nxt-a1-meishi && venv/bin/python3 -m pytest tests/test_occasion_lifecycle.py -v`

Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add app/schemas/api.py app/routers/v2/occasions.py tests/test_occasion_lifecycle.py
git commit -m "feat: report live card count per occasion"
```

---

## Task 4: Make occasion names searchable

Occasion names are currently searched **nowhere**. `?q=` covers person names, contact values, position titles/departments and organisation names only. Without this task, Task 2's stamped label would be visible on a card but impossible to find.

**Files:**
- Modify: `app/routers/v2/cards.py:44-50` (imports), `app/routers/v2/cards.py:152-159` (the `or_`)
- Test: `tests/test_occasion_lifecycle.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_occasion_lifecycle.py`:

```python
def test_search_matches_linked_occasion(client_with_test_db):
    """Typing an occasion name finds its cards while the link is live."""
    _seed(client_with_test_db, occasion_name="RI Convention", n_cards=3)

    resp = client_with_test_db.get("/api/v2/cards", params={"q": "Convention"})
    assert resp.status_code == 200
    assert len(resp.json()) == 3


def test_search_matches_orphaned_label(client_with_test_db):
    """The same query returns the same cards after the occasion is deleted."""
    occ_id, _ = _seed(client_with_test_db, occasion_name="RI Convention", n_cards=3)

    before = client_with_test_db.get("/api/v2/cards", params={"q": "Convention"}).json()
    assert len(before) == 3

    client_with_test_db.delete(f"/api/v2/occasions/{occ_id}")

    after = client_with_test_db.get("/api/v2/cards", params={"q": "Convention"}).json()
    assert len(after) == 3
    assert {c["id"] for c in after} == {c["id"] for c in before}


def test_search_does_not_match_unrelated_occasion(client_with_test_db):
    _seed(client_with_test_db, occasion_name="RI Convention", n_cards=2)

    resp = client_with_test_db.get("/api/v2/cards", params={"q": "Rotary"})
    assert resp.json() == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd nxt-a1-meishi && venv/bin/python3 -m pytest tests/test_occasion_lifecycle.py -k search -v`

Expected: `test_search_matches_linked_occasion` and `test_search_matches_orphaned_label` FAIL (0 results, not 3). `test_search_does_not_match_unrelated_occasion` passes vacuously — keep it, it guards the new clause against being too broad.

- [ ] **Step 3: Add `Occasion` to the local import block**

In `app/routers/v2/cards.py`, inside `list_cards` (currently lines 45-50):

```python
    from app.db.models import (
        CardMyCompany, ContactDetail, Occasion, Organization, OrganizationName,
        PersonName as PersonNameModel, Position, PositionDetail,
        CardSyncHistory,
    )
```

- [ ] **Step 4: Add the occasion branch to the search**

In the same function, replace the final `stmt = stmt.where(or_(...))` of the `if q:` block (currently lines 152-159):

```python
        # Occasion — matches the linked occasion's name, or the label stamped onto
        # the card when that occasion was deleted. Covering both sides of the link
        # means a search returns the same cards before and after a deletion.
        occasion_subq = select(Occasion.id).where(
            Occasion.id == Card.occasion_id,
            Occasion.name.ilike(like),
        )

        stmt = stmt.where(
            or_(
                exists(text_subq),
                exists(contact_subq),
                exists(pos_subq),
                exists(org_subq),
                exists(occasion_subq),
                Card.occasion_label.ilike(like),
            )
        )
```

- [ ] **Step 5: Run to verify they pass**

Run: `cd nxt-a1-meishi && venv/bin/python3 -m pytest tests/test_occasion_lifecycle.py -v`

Expected: 8 passed.

- [ ] **Step 6: Commit**

```bash
git add app/routers/v2/cards.py tests/test_occasion_lifecycle.py
git commit -m "feat: search cards by occasion name, live or orphaned"
```

---

## Task 5: Keep the occasion name in the Google Contacts export

`legacy_card.py` builds the DTO that `google_contacts.py` reads. Without this, a deleted occasion drops out of your exported contacts even though the card still shows it.

**Files:**
- Modify: `app/services/legacy_card.py:110`
- Test: `tests/test_occasion_lifecycle.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_occasion_lifecycle.py`:

```python
def test_legacy_card_falls_back_to_label(client_with_test_db):
    """The DTO fed to the Google Contacts sync survives an occasion deletion."""
    holder = {}

    async def _run():
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        from app.db.models import Card, CardMyCompany, Person
        from app.services.legacy_card import build_legacy_card

        async for db in app.dependency_overrides[get_db]():
            card = await db.scalar(
                select(Card)
                .where(Card.id == holder["card_id"])
                .options(
                    selectinload(Card.occasion),
                    selectinload(Card.my_company_links).selectinload(CardMyCompany.my_company),
                    selectinload(Card.person).selectinload(Person.names),
                    selectinload(Card.person).selectinload(Person.contact_details),
                    selectinload(Card.person).selectinload(Person.positions),
                )
            )
            legacy = build_legacy_card(
                card, card.person, card.person.contact_details, card.person.positions
            )
            holder["occasion_name"] = legacy.occasion_name
            break

    occ_id, card_ids = _seed(client_with_test_db, occasion_name="RI Convention", n_cards=1)
    holder["card_id"] = card_ids[0]

    client_with_test_db.delete(f"/api/v2/occasions/{occ_id}")
    asyncio.run(_run())

    assert holder["occasion_name"] == "RI Convention"
```

`build_legacy_card(db_card, person, contact_details, positions)` is the real signature
(`app/services/legacy_card.py:10`); the loader options above mirror what
`contact_sync.py:60-79` already uses. If `build_legacy_card` raises `MissingGreenlet`,
a relationship it touches is not eagerly loaded — add that `selectinload` rather than
switching the test to a lazy session.

- [ ] **Step 2: Run to verify it fails**

Run: `cd nxt-a1-meishi && venv/bin/python3 -m pytest tests/test_occasion_lifecycle.py -k legacy -v`

Expected: FAIL — `occasion_name` is `""` because the link is gone and nothing reads the label.

- [ ] **Step 3: Add the fallback**

In `app/services/legacy_card.py`, replace line 110:

```python
    # A live link wins so renames propagate; occasion_label is the snapshot left
    # behind when the occasion was deleted. occasion_location has no snapshot —
    # location is a property of the occasion, not of the card — so it goes empty.
    occasion_name = db_card.occasion.name if db_card.occasion else (db_card.occasion_label or "")
    occasion_location = db_card.occasion.location or "" if db_card.occasion else ""
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd nxt-a1-meishi && venv/bin/python3 -m pytest tests/ -q`

Expected: all pass, 9 in `test_occasion_lifecycle.py`.

- [ ] **Step 5: Deploy the backend and smoke-test against real data**

```bash
launchctl unload ~/Library/LaunchAgents/co.nxta.nxt-a1-backend.plist
launchctl load  ~/Library/LaunchAgents/co.nxta.nxt-a1-backend.plist
sleep 3
curl -s localhost:8000/api/v1/health
curl -s localhost:8000/api/v2/occasions | head -c 400
```

Expected: health OK; the occasions JSON now carries `card_count`, with `RI Convention` reporting **48**.

Then confirm the new search path works on live data:
```bash
curl -s 'localhost:8000/api/v2/cards?q=Convention&limit=100' | venv/bin/python3 -c "import json,sys; print(len(json.load(sys.stdin)))"
```
Expected: `48`.

**Do not delete a real occasion to test the stamp.** The unit tests cover it.

- [ ] **Step 6: Commit**

```bash
git add app/services/legacy_card.py tests/test_occasion_lifecycle.py
git commit -m "feat: fall back to occasion_label in the Google Contacts DTO"
```

---

## Task 6: Fix rotation not showing after grouping

**The bug.** The backend rotates the file **in place** on disk (`app/routers/v2/sessions.py:248`), so the stored image really is rotated. The display is stale because there are two independent cache-bust maps:

| Owner | State | Bumped by |
|---|---|---|
| `ScanPage` | `imgCacheBust` (`ScanPage.tsx:467`) | rotations in the **Ungrouped** row |
| `CardGroupCard` | `localCacheBust` (`ScanPage.tsx:1107`), starts `{}` | rotations **inside that group** |

`CardGroupCard` never receives `imgCacheBust`. An image rotated while ungrouped, then moved into a group, renders with **no `?t=`** — so the browser serves its cached pre-rotation copy.

**Files:**
- Modify: `frontend/src/pages/ScanPage.tsx` — the `CardGroupCard` props, its `localCacheBust` state, its `<LightboxImage src>`, its two rotate handlers, and the `<CardGroupCard>` call site.

- [ ] **Step 1: Add `imgCacheBust` to the `CardGroupCard` props type**

In the props type object (around line 1082-1105), add after `splitFeedback`:

```ts
  splitFeedback: Record<number, string>
  /** Shared with ScanPage so a rotation done while the image was ungrouped is still
   *  reflected once it lands in a group. Must not be duplicated into local state. */
  imgCacheBust: Record<number, number>
```

- [ ] **Step 2: Destructure it and delete the local state**

In the `CardGroupCard` parameter destructuring (line ~1079), add `imgCacheBust` to the list. Then **delete** this line entirely (line ~1107):

```ts
  const [localCacheBust, setLocalCacheBust] = useState<Record<number, number>>({})
```

- [ ] **Step 3: Use the shared map in the image URL**

Replace the `<LightboxImage src>` inside `CardGroupCard` (line ~1197):

```tsx
                <LightboxImage
                  src={`/api/v2/sessions/${sessionId}/temp/${img.image_filename}${imgCacheBust[img.id] ? `?t=${imgCacheBust[img.id]}` : ''}`}
                  alt={`side ${img.side_order}`}
                  className="h-28 w-auto rounded border border-gray-200 object-contain bg-gray-50"
                />
```

- [ ] **Step 4: Drop the now-redundant local bumps from the rotate handlers**

`onRotateImage` is `handleRotate`, which already bumps `imgCacheBust` (`ScanPage.tsx:467-471`). Replace both rotate buttons' handlers inside `CardGroupCard` (lines ~1216-1236):

```tsx
                    <button
                      className="bg-gray-100 text-xs px-1.5 py-0.5 rounded text-gray-600 hover:bg-gray-300"
                      onClick={() => onRotateImage(img, 'ccw')}
                      title="Rotate 90° counter-clockwise"
                    >
                      ↺
                    </button>
                    <button
                      className="bg-gray-100 text-xs px-1.5 py-0.5 rounded text-gray-600 hover:bg-gray-300"
                      onClick={() => onRotateImage(img)}
                      title="Rotate 90° clockwise"
                    >
                      ↻
                    </button>
```

(Task 7 restyles these buttons. Keep the classes as-is here so this task stays a pure bug fix.)

- [ ] **Step 5: Pass the prop at the call site**

In the `<CardGroupCard ... />` JSX (around line 810-820), add alongside `splitFeedback`:

```tsx
              splitFeedback={splitFeedback}
              imgCacheBust={imgCacheBust}
```

- [ ] **Step 6: Build and verify the types**

Run: `cd nxt-a1-meishi/frontend && npm run build`

Expected: no errors. If `useState` is now unused in `CardGroupCard`, it is still used elsewhere in the file — do not remove the import without checking.

- [ ] **Step 7: Verify in the browser — this is the actual test**

`tsc` passing proves nothing here; the bug is a browser cache behaviour. Hard-refresh (**Cmd+Shift+R**), then:

1. Start a scan and upload a photo of two cards.
2. In the **Ungrouped** row, click ↻ once. The thumbnail visibly rotates.
3. Click **1 per card (single-sided)**.
4. **The image inside the card group must appear rotated.**

Before this fix, step 4 shows the original unrotated orientation. Also re-check the previously-working path: rotate an image *inside* a group and confirm it still updates.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/pages/ScanPage.tsx
git commit -m "fix: show rotations done before grouping by sharing one cache-bust map"
```

---

## Task 7: Fixed-size tiles and larger icon buttons

**Two problems.** Icon buttons are `text-xs px-1.5 py-0.5` (~18px), too small to hit. And images are `h-24/h-28 w-auto`, so rotating swaps the aspect ratio, the tile's width changes, and every button below and after it shifts — you cannot click ↻ twice without re-aiming.

**Fix:** a fixed `w-32 h-32` tile with `object-contain`. Landscape fills the width, portrait fills the height, the footprint never changes.

**Files:**
- Modify: `frontend/src/pages/ScanPage.tsx` — the Ungrouped tile (lines ~734-782) and the group tile (lines ~1180-1245)

- [ ] **Step 1: Add a shared button class constant**

Near the top of `ScanPage.tsx`, after the imports and before the `CardGroup` interface:

```ts
// Icon buttons on image tiles. ~36px so they are comfortably clickable, and fixed
// size so a tile's controls never reflow when the image rotates.
const ICON_BTN = 'h-9 min-w-9 px-2 rounded text-lg leading-none flex items-center justify-center disabled:opacity-50'
```

- [ ] **Step 2: Rewrite the Ungrouped tile**

Replace the body of the `ungrouped.map(img => (...))` block (lines ~734-782) with:

```tsx
              <div
                key={img.id}
                className="relative w-32 cursor-grab active:cursor-grabbing"
                draggable
                onDragStart={e => {
                  e.dataTransfer.setData('imgId', String(img.id))
                  e.dataTransfer.setData('fromGroupId', '__ungrouped__')
                  e.dataTransfer.effectAllowed = 'move'
                }}
              >
                {/* Fixed 128px box: rotating swaps the image's aspect ratio but the
                    tile keeps its footprint, so the buttons below never move. */}
                <div className="w-32 h-32 rounded border border-gray-200 bg-gray-50 flex items-center justify-center overflow-hidden">
                  <LightboxImage
                    src={`/api/v2/sessions/${session?.external_id}/temp/${img.image_filename}${imgCacheBust[img.id] ? `?t=${imgCacheBust[img.id]}` : ''}`}
                    alt={img.image_filename}
                    className="max-w-full max-h-full object-contain"
                  />
                </div>
                <div className="flex gap-1 mt-1">
                  <button
                    className={`${ICON_BTN} bg-yellow-100 text-yellow-700 hover:bg-yellow-400 hover:text-gray-900`}
                    disabled={splittingIds.has(img.id)}
                    onClick={() => handleSplit(img)}
                    title={t.splitCards}
                  >
                    {splittingIds.has(img.id) ? '…' : '✂️'}
                  </button>
                  <button
                    className={`${ICON_BTN} bg-gray-100 text-gray-600 hover:bg-gray-300`}
                    onClick={() => handleRotate(img, 'ccw')}
                    title={t.rotateCcw}
                  >
                    ↺
                  </button>
                  <button
                    className={`${ICON_BTN} bg-gray-100 text-gray-600 hover:bg-gray-300`}
                    onClick={() => handleRotate(img)}
                    title={t.rotateCw}
                  >
                    ↻
                  </button>
                </div>
                {splitFeedback[img.id] && (
                  <div className="absolute top-0 left-0 right-0 bg-black/70 text-white text-xs text-center py-0.5 rounded-t">
                    {splitFeedback[img.id]}
                  </div>
                )}
                <p className="text-xs text-gray-500 mt-1 truncate w-32">{img.image_filename}</p>
              </div>
```

- [ ] **Step 3: Rewrite the group tile**

Inside `CardGroupCard`, replace the `<LightboxImage>` and the button row (lines ~1195-1240):

```tsx
                <div className="w-32 h-32 rounded border border-gray-200 bg-gray-50 flex items-center justify-center overflow-hidden">
                  <LightboxImage
                    src={`/api/v2/sessions/${sessionId}/temp/${img.image_filename}${imgCacheBust[img.id] ? `?t=${imgCacheBust[img.id]}` : ''}`}
                    alt={`side ${img.side_order}`}
                    className="max-w-full max-h-full object-contain"
                  />
                </div>
                <p className="text-xs text-gray-400 mt-1">{sideLabel(img.side_order ?? 0)}</p>
                {(stage === 'grouping' || stage === 'review') && (
                  <div className="flex gap-1 justify-center mt-0.5">
                    {stage === 'grouping' && (
                      <>
                        <button
                          className={`${ICON_BTN} bg-yellow-100 text-yellow-700 hover:bg-yellow-400 hover:text-gray-900`}
                          disabled={splittingIds.has(img.id)}
                          onClick={() => onSplitImage(img, group.tempCardId)}
                          title={t.splitCards}
                        >
                          {splittingIds.has(img.id) ? '…' : '✂️'}
                        </button>
                        <button
                          className={`${ICON_BTN} bg-blue-100 text-blue-700 hover:bg-blue-500 hover:text-white`}
                          onClick={() => setCropImg(img)}
                          title={t.cropImage}
                        >
                          ⬚
                        </button>
                      </>
                    )}
                    <button
                      className={`${ICON_BTN} bg-gray-100 text-gray-600 hover:bg-gray-300`}
                      onClick={() => onRotateImage(img, 'ccw')}
                      title={t.rotateCcw}
                    >
                      ↺
                    </button>
                    <button
                      className={`${ICON_BTN} bg-gray-100 text-gray-600 hover:bg-gray-300`}
                      onClick={() => onRotateImage(img)}
                      title={t.rotateCw}
                    >
                      ↻
                    </button>
                  </div>
                )}
```

Also widen the tile wrapper: the `<div key={img.id} className="text-center relative ...">` becomes `className="text-center relative w-32 ..."`, and the empty-slot placeholder `h-28 w-20` becomes `w-32 h-32`.

- [ ] **Step 4: Add the three tooltip keys to i18n**

These were hardcoded English. In **each** of the `ja`, `en` and `zh-TW` blocks of `frontend/src/i18n.ts`, add next to `splitCards`:

```ts
    // ja
    rotateCcw: '左に90°回転',
    rotateCw: '右に90°回転',
    cropImage: '画像を切り抜く',
```
```ts
    // en
    rotateCcw: 'Rotate 90° counter-clockwise',
    rotateCw: 'Rotate 90° clockwise',
    cropImage: 'Crop image',
```
```ts
    // zh-TW
    rotateCcw: '逆時針旋轉 90°',
    rotateCw: '順時針旋轉 90°',
    cropImage: '裁切影像',
```

`tsc -b` enforces key parity across the three blocks — a missing key is a compile error, so no separate parity check is needed.

- [ ] **Step 5: Build**

Run: `cd nxt-a1-meishi/frontend && npm run build`
Expected: no errors.

- [ ] **Step 6: Verify in the browser**

Hard-refresh, then:
1. Upload a landscape photo and a portrait photo. **Both tiles are the same size**, images letterboxed inside.
2. Put the cursor on ↻ and **click four times without moving the mouse**. The image rotates four times; the button does not move.
3. Repeat inside a card group.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/ScanPage.tsx frontend/src/i18n.ts
git commit -m "fix: fixed-size image tiles so rotate buttons stop moving, larger icon targets"
```

---

## Task 8: Split Ungrouped into two rows with per-row grouping buttons

This is the streamlining. Today all three grouping buttons sit in one bar and the user must guess which applies. After this task each row shows only the buttons that can work on it.

**Files:**
- Modify: `frontend/src/pages/ScanPage.tsx` — `autoGroup1`/`autoGroup2`/`autoPairByPosition` signatures (lines ~272-390), the Ungrouped section (lines ~706-786)
- Modify: `frontend/src/i18n.ts`

- [ ] **Step 1: Parameterise the three grouping functions**

They currently close over `ungrouped`. Change each to take the images it should act on. Replace lines ~272-296:

```ts
  const autoGroup1 = async (images: SessionImage[]) => {
    // Each image becomes its own single-sided card
    const keepGroups = groups.filter(g => g.images.length > 0)
    setGroups(keepGroups)
    for (const img of images) {
      const id = crypto.randomUUID()
      setGroups(prev => [...prev, newGroup(id)])
      await assignToGroup(img, id, 0)
    }
  }

  const autoGroup2 = async (images: SessionImage[]) => {
    // Pair every 2 images as front+back of one card
    const keepGroups = groups.filter(g => g.images.length > 0)
    setGroups(keepGroups)
    for (let i = 0; i < images.length; i += 2) {
      const id = crypto.randomUUID()
      setGroups(prev => [...prev, newGroup(id)])
      await assignToGroup(images[i], id, 0)
      if (images[i + 1]) await assignToGroup(images[i + 1], id, 1)
    }
  }
```

In `autoPairByPosition`, replace its signature and first lines:

```ts
  const autoPairByPosition = async (images: SessionImage[]) => {
    const keepGroups = groups.filter(g => g.images.length > 0)
    setGroups(keepGroups)
    const imgs = [...images]
```

The rest of `autoPairByPosition` is unchanged — including its handling of a lone trailing prefix (each of its positions becomes a single-sided card), which is what makes it correct for a fronts-only batch too.

- [ ] **Step 2: Derive the two lists**

After the `ungrouped` state declaration (line ~136) — or anywhere before the JSX — add:

```ts
  // Display-only partition of `ungrouped`. Images produced by the scissors carry a
  // _cardN suffix; everything else is a whole photo that has not been split.
  // NOTE: `separated` is a SUBSET of `ungrouped`, never an addition to it.
  const separated = ungrouped.filter(i => getCardPos(i.image_filename) !== null)
  const unsplit   = ungrouped.filter(i => getCardPos(i.image_filename) === null)
```

`getCardPos` is already defined at line ~300. **Move its declaration above this** (it is a `const` arrow function, so it is not hoisted and will throw a TDZ error otherwise).

- [ ] **Step 3: Extract the tile into a local component**

Both rows render an identical tile. Rather than duplicating the JSX from Task 7, extract it. Add above the `ScanPage` component:

```tsx
/** One draggable image tile in the Ungrouped or Separated cards row. */
function UngroupedTile({
  img, sessionId, cacheBust, splitting, feedback, onSplit, onRotate, splitTitle, t,
}: {
  img: SessionImage
  sessionId: string
  cacheBust?: number
  splitting: boolean
  feedback?: string
  onSplit: (img: SessionImage) => void
  onRotate: (img: SessionImage, direction?: 'cw' | 'ccw') => void
  splitTitle: string
  t: ReturnType<typeof useLang>['t']
}) {
  return (
    <div
      className="relative w-32 cursor-grab active:cursor-grabbing"
      draggable
      onDragStart={e => {
        e.dataTransfer.setData('imgId', String(img.id))
        e.dataTransfer.setData('fromGroupId', '__ungrouped__')
        e.dataTransfer.effectAllowed = 'move'
      }}
    >
      <div className="w-32 h-32 rounded border border-gray-200 bg-gray-50 flex items-center justify-center overflow-hidden">
        <LightboxImage
          src={`/api/v2/sessions/${sessionId}/temp/${img.image_filename}${cacheBust ? `?t=${cacheBust}` : ''}`}
          alt={img.image_filename}
          className="max-w-full max-h-full object-contain"
        />
      </div>
      <div className="flex gap-1 mt-1">
        <button
          className={`${ICON_BTN} bg-yellow-100 text-yellow-700 hover:bg-yellow-400 hover:text-gray-900`}
          disabled={splitting}
          onClick={() => onSplit(img)}
          title={splitTitle}
        >
          {splitting ? '…' : '✂️'}
        </button>
        <button
          className={`${ICON_BTN} bg-gray-100 text-gray-600 hover:bg-gray-300`}
          onClick={() => onRotate(img, 'ccw')}
          title={t.rotateCcw}
        >
          ↺
        </button>
        <button
          className={`${ICON_BTN} bg-gray-100 text-gray-600 hover:bg-gray-300`}
          onClick={() => onRotate(img)}
          title={t.rotateCw}
        >
          ↻
        </button>
      </div>
      {feedback && (
        <div className="absolute top-0 left-0 right-0 bg-black/70 text-white text-xs text-center py-0.5 rounded-t">
          {feedback}
        </div>
      )}
      <p className="text-xs text-gray-500 mt-1 truncate w-32">{img.image_filename}</p>
    </div>
  )
}
```

- [ ] **Step 4: Replace the single Ungrouped section with two rows**

Replace the whole `{ungrouped.length > 0 && (<section>…</section>)}` block (lines ~706-786):

```tsx
      {/* Ungrouped — whole photos that have not been split */}
      {unsplit.length > 0 && (
        <section className="space-y-2">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className="text-sm font-medium text-gray-700">{t.ungroupedN(unsplit.length)}</h2>
              <p className="text-xs text-gray-400">{t.ungroupedHint}</p>
            </div>
            {stage === 'grouping' && (
              <div className="flex gap-1 shrink-0">
                <button onClick={() => autoGroup1(unsplit)} className="btn-sm">{t.autoGroup1}</button>
                <button onClick={() => autoGroup2(unsplit)} className="btn-sm">{t.autoGroup2}</button>
              </div>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            {unsplit.map(img => (
              <UngroupedTile
                key={img.id}
                img={img}
                sessionId={session?.external_id ?? ''}
                cacheBust={imgCacheBust[img.id]}
                splitting={splittingIds.has(img.id)}
                feedback={splitFeedback[img.id]}
                onSplit={handleSplit}
                onRotate={handleRotate}
                splitTitle={t.splitCards}
                t={t}
              />
            ))}
          </div>
        </section>
      )}

      {/* Separated cards — output of the scissors */}
      {separated.length > 0 && (
        <section className="space-y-2">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className="text-sm font-medium text-gray-700">{t.separatedN(separated.length)}</h2>
              <p className="text-xs text-gray-400">{t.separatedHint}</p>
            </div>
            {stage === 'grouping' && (
              <div className="flex gap-1 shrink-0">
                <button
                  onClick={() => autoPairByPosition(separated)}
                  className="btn-primary text-sm"
                >
                  {t.autoPairByPos}
                </button>
                <button onClick={() => autoGroup1(separated)} className="btn-sm">{t.autoGroup1}</button>
              </div>
            )}
          </div>
          {hasMixedCropState && (
            <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2">
              ⚠️ {t.mixedCropWarning}
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            {separated.map(img => (
              <UngroupedTile
                key={img.id}
                img={img}
                sessionId={session?.external_id ?? ''}
                cacheBust={imgCacheBust[img.id]}
                splitting={splittingIds.has(img.id)}
                feedback={splitFeedback[img.id]}
                onSplit={handleSplit}
                onRotate={handleRotate}
                splitTitle={t.splitCards}
                t={t}
              />
            ))}
          </div>
        </section>
      )}
```

Note what is gone: the `Start analysis` shortcut that used to live in this header and silently ran `autoGroup1()` first. Task 9 replaces it with an explicit gate. Note also that `canAutoPairByPos` is no longer needed to decide whether to show the pairing button — the Separated row's existence is the condition. Delete the `canAutoPairByPos` definition (line ~315) **and** update `hasMixedCropState`, which currently early-returns on it:

```ts
  // True when an un-split photo shares a base name with images that were split —
  // pairing by position while that photo is still whole would give wrong results.
  const hasMixedCropState = (() => {
    if (separated.length === 0) return false
    const croppedPrefixes = new Set(
      separated
        .map(i => getSourcePrefix(i.image_filename))
        .filter((p): p is string => p !== null)
    )
    if (croppedPrefixes.size === 0) return false
    return unsplit.some(i => {
      const base = i.image_filename.replace(/\.[^.]+$/, '')
      return croppedPrefixes.has(base)
    })
  })()
```

- [ ] **Step 5: Add the i18n keys**

In each of the three blocks of `frontend/src/i18n.ts`, next to `ungroupedN`:

```ts
    // ja
    separatedN: (n: number) => `分割済みの名刺 (${n}枚)`,
    ungroupedHint: 'まだ分割していない写真',
    separatedHint: '各写真の1枚目どうしが、表と裏のペアになります。',
    mixedCropWarning: '「未グループ」にまだ分割していない写真があります。分割してから「位置でペア」を使わないと、ペアが正しくなりません。',
```
```ts
    // en
    separatedN: (n: number) => `Separated cards (${n})`,
    ungroupedHint: 'Whole photos, not yet split',
    separatedHint: 'Card #1 of each photo pairs with card #1 of the next photo, as front and back.',
    mixedCropWarning: 'Some photos in Ungrouped have not been split yet. Split them before pairing by position, or the pairing will be wrong.',
```
```ts
    // zh-TW
    separatedN: (n: number) => `已分割的名片 (${n})`,
    ungroupedHint: '尚未分割的整張照片',
    separatedHint: '每張照片的第 1 張名片會互相配對，成為正面與背面。',
    mixedCropWarning: '「未分組」中還有尚未分割的照片。請先分割，再使用「依位置配對」，否則配對會出錯。',
```

`mixedCropWarning` already exists — **replace** its value in all three blocks rather than adding a duplicate key.

- [ ] **Step 6: Build**

Run: `cd nxt-a1-meishi/frontend && npm run build`
Expected: no errors. A `getCardPos is used before its declaration` error means Step 2's move was not done.

- [ ] **Step 7: Verify in the browser**

Hard-refresh, then walk the real workflow:
1. Upload two photos, each holding three cards (fronts photo, then backs photo).
2. Both appear under **Ungrouped** with `1 per card` / `Pairs of 2`. No `Pair by position` button — correct, it cannot work on unsplit photos.
3. Scissor the first photo. **Its three crops appear under Separated cards**, and the original leaves Ungrouped.
4. The amber mixed-crop warning appears (one photo split, its sibling not).
5. Scissor the second photo. The warning clears; Ungrouped disappears; six tiles under Separated cards.
6. Click **Pair by position** → three card groups, each with a front and a back, correctly matched by position.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/pages/ScanPage.tsx frontend/src/i18n.ts
git commit -m "feat: separate Ungrouped and Separated cards rows with per-row grouping buttons"
```

---

## Task 9: Help panel, readiness gate, and tips

**Files:**
- Modify: `frontend/src/pages/ScanPage.tsx`, `frontend/src/i18n.ts`

- [ ] **Step 1: Add the collapsible help panel component**

Above the `ScanPage` component:

```tsx
/** Explains the ✂️ and ↺↻ actions. Collapsed state is remembered so a returning
 *  user is not re-lectured, but it defaults to open for a first-time user. */
function GroupingHelp() {
  const { t } = useLang()
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem('scan.help.collapsed') === '1',
  )

  const toggle = () => {
    setCollapsed(prev => {
      localStorage.setItem('scan.help.collapsed', prev ? '0' : '1')
      return !prev
    })
  }

  if (collapsed) {
    return (
      <button onClick={toggle} className="text-xs text-blue-500 hover:text-blue-700">
        ⓘ {t.scanHelpShow}
      </button>
    )
  }

  return (
    <div className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 space-y-1.5">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium text-blue-900">{t.scanHelpTitle}</h3>
        <button onClick={toggle} className="text-xs text-blue-500 hover:text-blue-700">
          {t.scanHelpHide}
        </button>
      </div>
      <p className="text-xs text-blue-900/80">{t.scanHelpSplit}</p>
      <p className="text-xs text-blue-900/80">{t.scanHelpRotate}</p>
    </div>
  )
}
```

- [ ] **Step 2: Render it above the Ungrouped row**

In the `ScanPage` JSX, immediately after the `<DropZone …/>` block and **before** the Ungrouped section:

```tsx
      {stage === 'grouping' && <GroupingHelp />}
```

- [ ] **Step 3: Compute one blocked-reason value**

Both `Start analysis` buttons must agree, so derive the reason once. Add near the other derived values:

```ts
  // Images left in Ungrouped or Separated are silently dropped by the analysis, so
  // block until every one is in a group. `ungrouped` covers both rows (see Task 8).
  const analysisBlockedReason =
    ungrouped.length > 0
      ? t.analysisBlockedUngrouped(ungrouped.length)
      : groups.every(g => g.images.length === 0)
        ? t.analysisBlockedEmpty
        : null
```

- [ ] **Step 4: Use it on both Start analysis buttons**

Replace the header button in the Card groups section (lines ~795-802):

```tsx
                <button
                  disabled={analysisBlockedReason !== null}
                  onClick={startAnalysis}
                  className="btn-primary text-sm disabled:opacity-50 disabled:cursor-not-allowed"
                  title={analysisBlockedReason ?? undefined}
                >
                  {t.startAnalysis}
                </button>
```

And the footer button (lines ~876-884) identically. Then, immediately above the footer button, render the reason so it is not hidden in a tooltip:

```tsx
          {stage === 'grouping' && (
            <div className="flex flex-col items-end gap-2 pt-2">
              {analysisBlockedReason && (
                <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2 w-full">
                  ⚠️ {analysisBlockedReason}
                </p>
              )}
              <button
                disabled={analysisBlockedReason !== null}
                onClick={startAnalysis}
                className="btn-primary text-sm disabled:opacity-50 disabled:cursor-not-allowed"
                title={analysisBlockedReason ?? undefined}
              >
                {t.startAnalysis}
              </button>
            </div>
          )}
```

- [ ] **Step 5: Extend the tips**

Replace the single tip line (line ~806-810):

```tsx
          {stage === 'grouping' && (
            <ul className="text-xs text-gray-400 list-disc pl-4 space-y-0.5">
              {groups.length >= 2 && <li>{t.tipDragPair}</li>}
              <li>{t.tipFrontSide}</li>
              <li>{t.tipStartAnalysis}</li>
            </ul>
          )}
```

- [ ] **Step 6: Add the i18n keys**

In each of the three blocks:

```ts
    // ja
    scanHelpTitle: '使い方',
    scanHelpShow: '使い方を表示',
    scanHelpHide: '隠す',
    scanHelpSplit: '✂️ 分割 — 複数の名刺が写った写真を、名刺1枚ずつの画像に切り分けます。複数名刺の写真は解析前に必ず分割してください。',
    scanHelpRotate: '↺ ↻ 回転 — 文字が横書きで読める向きに画像を回転します。',
    analysisBlockedUngrouped: (n: number) => `${n}枚の画像がまだグループに入っていません。複数の名刺が写った写真を分割してから、上のボタンでグループ化してください。`,
    analysisBlockedEmpty: '解析する名刺がありません。',
    tipDragPair: '画像を他の名刺にドラッグすると、表と裏のペアになります。',
    tipFrontSide: '名前と連絡先が載っている面を「表」にしてください。⇅ 入れ替え で変更できます。',
    tipStartAnalysis: 'すべての名刺が正しく並んだら「解析開始」を押してください。',
```
```ts
    // en
    scanHelpTitle: 'How this works',
    scanHelpShow: 'How this works',
    scanHelpHide: 'Hide',
    scanHelpSplit: '✂️ Split — for a photo holding several cards, cut it into one image per card. Every multi-card photo must be split before analysis.',
    scanHelpRotate: '↺ ↻ Rotate — turn an image upright so the text reads left to right.',
    analysisBlockedUngrouped: (n: number) => `${n} image${n === 1 ? ' is' : 's are'} not in a card group yet. Split any photo holding more than one card, then use the grouping buttons above.`,
    analysisBlockedEmpty: 'There are no cards to analyse yet.',
    tipDragPair: 'Drag an image from one card into another to pair them as front and back.',
    tipFrontSide: 'The side showing the name and contact details should be the Front. Use ⇅ Swap to change it.',
    tipStartAnalysis: 'When every card is positioned correctly, press Start Analysis.',
```
```ts
    // zh-TW
    scanHelpTitle: '操作說明',
    scanHelpShow: '顯示操作說明',
    scanHelpHide: '隱藏',
    scanHelpSplit: '✂️ 分割 — 將一張含多張名片的照片，切成每張名片一個影像。含多張名片的照片必須先分割才能解析。',
    scanHelpRotate: '↺ ↻ 旋轉 — 將影像轉正，讓文字能夠橫向閱讀。',
    analysisBlockedUngrouped: (n: number) => `還有 ${n} 個影像尚未加入名片群組。請先分割含多張名片的照片，再使用上方的群組按鈕。`,
    analysisBlockedEmpty: '目前沒有可解析的名片。',
    tipDragPair: '將影像拖曳到另一張名片上，即可配成正面與背面。',
    tipFrontSide: '有姓名與聯絡資訊的那一面應設為「正面」。可用 ⇅ 交換 變更。',
    tipStartAnalysis: '當所有名片都排列正確後，請按「開始解析」。',
```

- [ ] **Step 7: Build**

Run: `cd nxt-a1-meishi/frontend && npm run build`
Expected: no errors.

- [ ] **Step 8: Verify in the browser**

Hard-refresh, then:
1. Enter the grouping stage → the blue help panel is open. Click **Hide** → it collapses to a `ⓘ` link. Reload the page → **still collapsed** (localStorage). Click it → expands again.
2. Upload two photos, group only one. `Start analysis` is **disabled**, with the amber reason naming the leftover count.
3. Group the rest → the warning disappears and both `Start analysis` buttons enable together.
4. Confirm the old shortcut is gone: with images ungrouped and no groups, there is no `Start analysis` button in the Ungrouped header.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/pages/ScanPage.tsx frontend/src/i18n.ts
git commit -m "feat: grouping help panel, readiness gate before analysis, expanded tips"
```

---

## Task 10: Instructions and i18n for the card outline screen

`CardOutlineSelector` is hardcoded English while the rest of the app is ja/en/zh-TW. Since this task adds copy, move the whole component onto `useLang()`.

**Files:**
- Modify: `frontend/src/components/CardOutlineSelector.tsx`, `frontend/src/i18n.ts`

- [ ] **Step 1: Add the i18n keys**

In each of the three blocks:

```ts
    // ja
    outlineCancel: 'キャンセル',
    outlineUndo: '取り消し',
    outlineUndoHint: '最後に指定した名刺を取り消します',
    outlineDetecting: '角を検出中…',
    outlineTapHeader: (n: number) => `各名刺の中心をクリックしてください（${n}枚検出）`,
    outlineDragHeader: (n: number) => `各名刺を囲むようにドラッグしてください（${n}枚検出）`,
    outlineDragging: (n: number) => `名刺 ${n} — ドラッグして範囲を指定`,
    outlineSelectedN: (n: number) => `${n}枚を指定しました — 続けて指定するか、切り抜いてください`,
    outlineCropN: (n: number) => `${n}枚を切り抜く`,
    outlineAlreadyOutlined: 'その名刺はすでに指定済みです',
    outlineDetectFailed: '角の検出に失敗しました — もう一度お試しください',
    outlineHintTap: '名刺の中心を1回クリックすると、4つの角が自動で検出されます。角をドラッグして調整できます。',
    outlineHintDrag: '各名刺を囲むようにドラッグしてください',
    outlineHintReady: 'すべての名刺を指定したら、下の緑のボタンを押してください。',
    outlineHintAmber: '黄色の点線は角の推定です — 名刺の端に合わせてドラッグしてください。',
```
```ts
    // en
    outlineCancel: 'Cancel',
    outlineUndo: 'Undo',
    outlineUndoHint: 'Removes the last card you outlined.',
    outlineDetecting: 'Detecting corners…',
    outlineTapHeader: (n: number) => `Click the centre of each card (${n} detected)`,
    outlineDragHeader: (n: number) => `Drag around each card (${n} detected)`,
    outlineDragging: (n: number) => `Card ${n} — drag to define boundary`,
    outlineSelectedN: (n: number) => `${n} card${n > 1 ? 's' : ''} outlined — outline more, or crop`,
    outlineCropN: (n: number) => `Crop ${n} card${n > 1 ? 's' : ''}`,
    outlineAlreadyOutlined: 'That card is already outlined',
    outlineDetectFailed: 'Corner detection failed — try again',
    outlineHintTap: 'Click the centre of a card once — its 4 corners are found automatically. Then drag any corner to adjust.',
    outlineHintDrag: 'Press and drag to draw a box around each card',
    outlineHintReady: 'When every card is outlined, press the green button below.',
    outlineHintAmber: 'A dashed amber outline means the corners are a guess — drag them onto the card edges.',
```
```ts
    // zh-TW
    outlineCancel: '取消',
    outlineUndo: '復原',
    outlineUndoHint: '移除最後指定的名片。',
    outlineDetecting: '正在偵測邊角…',
    outlineTapHeader: (n: number) => `請點選每張名片的中心（偵測到 ${n} 張）`,
    outlineDragHeader: (n: number) => `請拖曳框選每張名片（偵測到 ${n} 張）`,
    outlineDragging: (n: number) => `名片 ${n} — 拖曳以指定範圍`,
    outlineSelectedN: (n: number) => `已指定 ${n} 張 — 可繼續指定或進行裁切`,
    outlineCropN: (n: number) => `裁切 ${n} 張名片`,
    outlineAlreadyOutlined: '該名片已經指定過了',
    outlineDetectFailed: '邊角偵測失敗 — 請再試一次',
    outlineHintTap: '在名片中心點一下，系統會自動找出四個邊角。可拖曳邊角進行微調。',
    outlineHintDrag: '請按住並拖曳，框選每一張名片',
    outlineHintReady: '所有名片都指定完成後，請按下方的綠色按鈕。',
    outlineHintAmber: '黃色虛線表示邊角是推測的 — 請拖曳到名片邊緣。',
```

- [ ] **Step 2: Wire `useLang` into the component**

At the top of `CardOutlineSelector.tsx`:

```tsx
import { useLang } from '../LangContext'
```

and as the first line of the component body:

```tsx
  const { t } = useLang()
```

- [ ] **Step 3: Replace the hardcoded strings**

Two error strings inside handlers:

```tsx
      setDetectError(t.outlineAlreadyOutlined)   // both occurrences (tap and drag)
      setDetectError(t.outlineDetectFailed)
```

The `headerText` block becomes:

```tsx
  const headerText = detecting
    ? t.outlineDetecting
    : tapMode
      ? polygons.length === 0
        ? t.outlineTapHeader(cardCount)
        : t.outlineSelectedN(polygons.length)
      : dragStart
        ? t.outlineDragging(polygons.length + 1)
        : polygons.length === 0
          ? t.outlineDragHeader(cardCount)
          : t.outlineSelectedN(polygons.length)
```

Header buttons:

```tsx
        <button onClick={onCancel} className="text-sm text-gray-300 hover:text-white">{t.outlineCancel}</button>
```
```tsx
        <button
          onClick={undoLast}
          className="text-sm text-gray-300 hover:text-white disabled:opacity-30"
          disabled={polygons.length === 0 || detecting}
          title={t.outlineUndoHint}
        >
          {t.outlineUndo}
        </button>
```

The `Detecting corners…` overlay uses `{t.outlineDetecting}`.

- [ ] **Step 4: Add the instruction bar under the header**

Immediately after the header `</div>`, before the image container:

```tsx
      {/* Standing instructions — the interaction is not discoverable without them. */}
      <div className="bg-gray-800 px-4 py-2 text-xs text-gray-300 shrink-0 space-y-1">
        <p>{tapMode ? t.outlineHintTap : t.outlineHintDrag}</p>
        {polyMeta.some(m => m.confidence === 0) && (
          <p className="text-amber-300">{t.outlineHintAmber}</p>
        )}
      </div>
```

- [ ] **Step 5: Rewrite the footer**

Replace the footer block:

```tsx
      <div className="bg-gray-900 px-4 py-3 shrink-0">
        {canCrop ? (
          <>
            <p className="text-center text-gray-400 text-xs mb-2">{t.outlineHintReady}</p>
            <button
              className="w-full bg-green-600 hover:bg-green-500 text-white font-semibold py-3 rounded-lg"
              onClick={() => onComplete(polygons)}
            >
              {t.outlineCropN(polygons.length)}
            </button>
          </>
        ) : (
          <p className="text-center text-gray-400 text-sm">
            {tapMode ? t.outlineHintTap : t.outlineHintDrag}
          </p>
        )}
      </div>
```

The `Card N` label drawn inside each SVG polygon stays as-is — it is a short marker on the image, not prose.

- [ ] **Step 6: Build**

Run: `cd nxt-a1-meishi/frontend && npm run build`
Expected: no errors.

- [ ] **Step 7: Verify in the browser**

Hard-refresh, then open the outline screen from a multi-card photo:
1. The instruction bar sits under the header and explains the click-then-adjust interaction.
2. Hovering **Undo** shows the tooltip.
3. Outline one card → the footer shows the green-button instruction above the button.
4. If a detection comes back amber/dashed, the extra amber line appears.
5. Switch the app language (nav bar) and reopen → all of it is translated, including `Cancel`, `Undo` and `Crop N cards`.
6. **Regression check:** drag a corner handle and release over the image. No spurious extra card is created — the `cornerDragRef` guard must still work.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/CardOutlineSelector.tsx frontend/src/i18n.ts
git commit -m "feat: translate the card outline screen and explain the interaction"
```

---

## Task 11: Taller drop target

**Files:**
- Modify: `frontend/src/components/DropZone.tsx`, `frontend/src/pages/ScanPage.tsx`

- [ ] **Step 1: Add the `compact` prop**

In `DropZone.tsx`:

```tsx
interface Props {
  onFiles: (files: File[]) => void
  disabled?: boolean
  /** Shrink once images exist — a full-height zone would push the card rows off-screen. */
  compact?: boolean
}

export function DropZone({ onFiles, disabled, compact }: Props) {
```

and in the `<label>` className, replace `p-10 min-h-[360px]` with:

```tsx
      className={`flex flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed transition-colors cursor-pointer select-none p-10 ${compact ? 'min-h-[200px]' : 'min-h-[720px]'}
```

- [ ] **Step 2: Pass it from ScanPage**

Replace the `<DropZone onFiles={handleFiles} />` call (line ~701):

```tsx
        <DropZone
          onFiles={handleFiles}
          compact={ungrouped.length > 0 || groups.length > 0}
        />
```

`separated` is a **subset** of `ungrouped` (Task 8, Step 2) — do not add it here.

- [ ] **Step 3: Build**

Run: `cd nxt-a1-meishi/frontend && npm run build`
Expected: no errors.

- [ ] **Step 4: Verify in the browser**

Hard-refresh, then:
1. Start a new scan. The drop zone is roughly twice its old height and easy to hit when dragging from a Finder window.
2. Drop one image. The zone **shrinks** and the Ungrouped row is visible without scrolling.
3. Drag a second file from Finder onto the shrunk zone — it still highlights and accepts.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/DropZone.tsx frontend/src/pages/ScanPage.tsx
git commit -m "feat: double the drop target height before the first upload"
```

---

## Task 12: Group occasions by year and month

Only 1 of 21 live occasions has `event_date` set — both creation paths call `createOccasion({ name })` with no date. Grouping must therefore fall back to `created_at`, and that rule lives in exactly one place.

**Files:**
- Create: `frontend/src/lib/occasionGrouping.ts`
- Modify: `frontend/src/types/index.ts`, `frontend/src/pages/ScanPage.tsx` (`OccasionPicker`), `frontend/src/pages/SettingsPage.tsx`, `frontend/src/i18n.ts`

- [ ] **Step 1: Add `card_count` to the TS type**

In `frontend/src/types/index.ts`:

```ts
export interface Occasion {
  id: number
  name: string
  event_date?: string
  location?: string
  notes?: string
  created_at: string
  /** Live cards using this occasion. Present on the list endpoint; 0 elsewhere. */
  card_count: number
}
```

- [ ] **Step 2: Write the grouping helper**

Create `frontend/src/lib/occasionGrouping.ts`:

```ts
import type { Occasion } from '../types'

export interface OccasionMonth {
  year: number
  month: number          // 1-12
  occasions: Occasion[]
}

export interface OccasionYear {
  year: number
  months: OccasionMonth[]
}

/**
 * event_date is optional and, in practice, almost never set — both creation paths
 * post only a name. created_at tracks when the event was scanned and is a good
 * proxy, so it is the fallback. This is the single owner of that rule.
 */
export function occasionPeriod(o: Occasion): { year: number; month: number } {
  const d = new Date(o.event_date ?? o.created_at)
  return { year: d.getFullYear(), month: d.getMonth() + 1 }
}

/** Newest year first, newest month first. Order within a month is preserved. */
export function groupOccasionsByMonth(occasions: Occasion[]): OccasionYear[] {
  const years = new Map<number, Map<number, Occasion[]>>()

  for (const o of occasions) {
    const { year, month } = occasionPeriod(o)
    if (!years.has(year)) years.set(year, new Map())
    const months = years.get(year)!
    if (!months.has(month)) months.set(month, [])
    months.get(month)!.push(o)
  }

  return [...years.entries()]
    .sort((a, b) => b[0] - a[0])
    .map(([year, months]) => ({
      year,
      months: [...months.entries()]
        .sort((a, b) => b[0] - a[0])
        .map(([month, os]) => ({ year, month, occasions: os })),
    }))
}

/** Flat month list across all years — for a <select>, which cannot nest optgroups. */
export function flattenOccasionMonths(occasions: Occasion[]): OccasionMonth[] {
  return groupOccasionsByMonth(occasions).flatMap(y => y.months)
}
```

- [ ] **Step 3: Add the month label key**

In each of the three i18n blocks:

```ts
    monthLabel: (y: number, m: number) => `${y}年${m}月`,          // ja
    monthLabel: (y: number, m: number) => `${y}-${String(m).padStart(2, '0')}`,  // en
    monthLabel: (y: number, m: number) => `${y}年${m}月`,          // zh-TW
```

Also add the delete warning and card-count keys:

```ts
    // ja
    occasionCardCount: (n: number) => `${n}枚`,
    occasionDeleteWarn: (name: string, n: number) =>
      `「${name}」を削除しますか？\n\nこの場面は ${n} 枚の名刺で使われています。名刺には「${name}」がテキストとして残りますが、名前の変更や絞り込みができる場面とのリンクはなくなります。`,
```
```ts
    // en
    occasionCardCount: (n: number) => `${n} card${n === 1 ? '' : 's'}`,
    occasionDeleteWarn: (name: string, n: number) =>
      `Delete "${name}"?\n\n${n} card${n === 1 ? '' : 's'} use this occasion. They will keep "${name}" as a plain text label, but will no longer be linked to an occasion you can rename or filter by.`,
```
```ts
    // zh-TW
    occasionCardCount: (n: number) => `${n} 張`,
    occasionDeleteWarn: (name: string, n: number) =>
      `要刪除「${name}」嗎？\n\n有 ${n} 張名片使用此場合。名片會保留「${name}」文字標籤，但將不再連結到可重新命名或篩選的場合。`,
```

- [ ] **Step 4: Group the Scan picker's `<optgroup>`s**

In `ScanPage.tsx`, `OccasionPicker`. Import the helper:

```tsx
import { flattenOccasionMonths } from '../lib/occasionGrouping'
```

Replace the `sorted` / `recent` / `older` block (lines ~1013-1019) with:

```tsx
  const months = flattenOccasionMonths(occasions)
```

and the `<select>` body:

```tsx
        <option value="">{t.noneOption}</option>
        {months.map(({ year, month, occasions: os }) => (
          <optgroup key={`${year}-${month}`} label={t.monthLabel(year, month)}>
            {os.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}
          </optgroup>
        ))}
```

- [ ] **Step 5: Group the Settings list into a year → month tree**

In `SettingsPage.tsx`, import:

```tsx
import { groupOccasionsByMonth } from '../lib/occasionGrouping'
```

`SettingsPage.tsx` currently imports only `useState` — widen it:

```tsx
import { useMemo, useState } from 'react'
```

Add the grouped list and its collapse state inside the settings component, next to the
other `useState`s. **Do not drive the default with a `useEffect`** — the occasions arrive
asynchronously, and an effect that seeds state from them re-runs on every render because
`occasionYears` is a fresh array each time. Instead treat the default as a rule and store
only the years the user has explicitly toggled:

```tsx
  const occasionYears = useMemo(() => groupOccasionsByMonth(occasions), [occasions])

  // Years the user has explicitly toggled. Anything absent follows the default rule:
  // the current year expanded, earlier years collapsed — so the list stays roughly one
  // screen however many years accumulate. No effect, so nothing re-runs on data arrival.
  const [occYearOverrides, setOccYearOverrides] = useState<Map<number, boolean>>(new Map())
  const thisYear = new Date().getFullYear()

  const isOccYearCollapsed = (year: number) =>
    occYearOverrides.get(year) ?? year !== thisYear

  const toggleOccYear = (year: number) =>
    setOccYearOverrides(prev => {
      const next = new Map(prev)
      next.set(year, !isOccYearCollapsed(year))
      return next
    })
```

Replace the occasions list container (line ~337-342):

```tsx
        <div className="rounded-lg border border-gray-200 bg-white overflow-hidden">
          {occasionYears.map(({ year, months }) => {
            const collapsed = isOccYearCollapsed(year)
            const yearCount = months.reduce((n, m) => n + m.occasions.length, 0)
            return (
              <div key={year}>
                <button
                  className="w-full flex items-center gap-2 text-sm font-semibold text-gray-700 px-4 py-2 bg-gray-50 border-b border-gray-100 hover:text-blue-600 text-left"
                  onClick={() => toggleOccYear(year)}
                >
                  <span className="text-xs text-gray-400">{collapsed ? '▶' : '▼'}</span>
                  <span>{year}</span>
                  <span className="text-xs text-gray-400">({yearCount})</span>
                </button>
                {!collapsed && months.map(({ month, occasions: os }) => (
                  <div key={`${year}-${month}`}>
                    <p className="text-xs font-medium text-gray-500 px-4 py-1 bg-gray-50/50">
                      {t.monthLabel(year, month)}
                    </p>
                    {os.map(o => <OccasionRow key={o.id} occasion={o} />)}
                  </div>
                ))}
              </div>
            )
          })}
          {occasions.length === 0 && (
            <p className="text-sm text-gray-400 italic px-4 py-3">—</p>
          )}
        </div>
```

- [ ] **Step 6: Show the count and warn on delete**

In `OccasionRow`, replace the `event_date` line and the delete button:

```tsx
            <p className="text-xs text-gray-400 mt-0.5">
              {occasion.event_date && <span className="mr-2">{occasion.event_date}</span>}
              {t.occasionCardCount(occasion.card_count)}
            </p>
```
```tsx
          <button
            onClick={() => {
              if (window.confirm(t.occasionDeleteWarn(occasion.name, occasion.card_count))) {
                deleteMutation.mutate()
              }
            }}
            disabled={deleteMutation.isPending}
            className="text-xs text-red-400 hover:text-red-600 disabled:opacity-50 shrink-0"
          >{t.deleteBtn}</button>
```

- [ ] **Step 7: Build**

Run: `cd nxt-a1-meishi/frontend && npm run build`
Expected: no errors. A missing `card_count` on an `Occasion` literal means Step 1 was skipped.

- [ ] **Step 8: Verify in the browser**

Backend from Task 3 must be running (`curl -s localhost:8000/api/v2/occasions | head -c 200` shows `card_count`). Hard-refresh, then:
1. **Settings** → occasions are grouped under `2026` with month sub-headers (`2026年7月` etc). Every occasion shows its card count; `RI Convention` shows 48.
2. Collapse/expand a year with the ▶/▼ toggle.
3. Click **Delete** on an occasion and **read the dialog** — it names the occasion and its card count. Press **Cancel**; do not delete real data.
4. **Scan** → open a card's Occasion dropdown. Options are grouped by month, newest first, with `なし` first. The old "Recent / All" split is gone.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/lib/occasionGrouping.ts frontend/src/types/index.ts frontend/src/pages/ScanPage.tsx frontend/src/pages/SettingsPage.tsx frontend/src/i18n.ts
git commit -m "feat: group occasions by year and month, warn with card count on delete"
```

---

## Task 13: Widen the search placeholder

`searchPlaceholder` says "by name" but search already covered company and phone number — and now covers occasions too.

**Files:**
- Modify: `frontend/src/i18n.ts:72`, `:295`, `:517`

- [ ] **Step 1: Replace the value in all three blocks**

```ts
    searchPlaceholder: '名前・会社・場面で検索…',      // ja
    searchPlaceholder: 'Search name, company, occasion…',  // en
    searchPlaceholder: '搜尋姓名、公司、場合…',        // zh-TW
```

- [ ] **Step 2: Build and verify**

Run: `cd nxt-a1-meishi/frontend && npm run build`

Then hard-refresh the Collection page and search for an occasion name (e.g. `Convention`). Expected: its cards are returned — a query that returned nothing before Task 4.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/i18n.ts
git commit -m "feat: widen the search placeholder to match what search covers"
```

---

## Final verification

- [ ] **Full backend suite**

Run: `cd nxt-a1-meishi && venv/bin/python3 -m pytest tests/ -q`
Expected: all pass.

- [ ] **Clean build**

Run: `cd nxt-a1-meishi/frontend && npm run build && npm run lint`
Expected: no errors.

- [ ] **End-to-end pass on real hardware**

Redeploy the backend, hard-refresh, and run one complete scan of a real double-sided batch: shoot fronts, shoot backs, upload both, scissor both, **Pair by position**, confirm every group has the right front and back **and the rotations you applied are visible**, run the analysis, save. Then confirm the saved cards appear in the Collection and are findable by their occasion name.

- [ ] **Update project memory**

Add to the project memory index: the two-row grouping model, the `occasion_label` resolution rule (link wins, label is the fallback), and that `?q=` now covers occasions.

---

## Self-review notes

**Spec coverage** — every section maps to a task: §3→11, §4.1→9, §4.2/4.3→8, §4.4→9, §4.5→9, §4.6→7, §5→10, §6.1→1,2,3,5, §6.2→12, §6.3→4,13, §7 (i18n) is folded into each task that adds strings, §8 (testing) into each task's verify steps, §9→6.

**Naming consistency** — `imgCacheBust` is the single cache-bust map after Task 6 (`localCacheBust` is deleted, not renamed). `separated`/`unsplit` are the derived lists throughout Tasks 8, 9 and 11; `ungrouped` remains the full array and is what the readiness gate and `DropZone compact` check. `occasion_label` is the column, `card_count` the computed field, `occasionPeriod`/`groupOccasionsByMonth`/`flattenOccasionMonths` the helper exports.

**Known ordering constraint** — Task 8 Step 2 requires moving `getCardPos` above its new use site; it is a `const` arrow function and will throw a TDZ error otherwise. Task 8 also deletes `canAutoPairByPos` and rewrites `hasMixedCropState`, which depended on it.
