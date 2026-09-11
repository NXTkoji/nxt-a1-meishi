# Collection Scalability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the 200-card ceiling that is already hiding five cards, and make Collection search reach the server so company, title, phone and occasion queries work.

**Architecture:** A cheap `GROUP BY` facets endpoint drives a complete year/month tree at any collection size; months fetch lazily and paginate. Search moves server-side, debounced and paginated. Underneath, one shared filter builder keeps list/count/facets consistent, two N+1 loops are batched, and the tables get their first indexes.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async + Alembic + pytest (backend); React 19 + TypeScript + TanStack Query + Tailwind + Vite (frontend). No frontend test runner — verification is `tsc -b` plus explicit browser checks.

**Spec:** `docs/superpowers/specs/2026-09-09-collection-scalability-design.md`

---

## Before you start

**Branch off `main`.**

```bash
git fetch origin
git checkout -b feat/collection-scalability origin/main
git checkout docs/community-edition-spec -- docs/superpowers/specs/2026-09-09-collection-scalability-design.md docs/superpowers/plans/2026-09-10-collection-scalability.md
git commit -m "docs: bring collection scalability spec and plan onto the feature branch"
```

**Relationship to the other plan.** `2026-09-09-scan-group-ui-and-occasion-lifecycle.md` Task 4 adds occasion matching to `?q=`. The two plans do not conflict — that one touches the `if q:` `or_()`, this one moves the surrounding filter code into a helper. **If that plan has already landed, its occasion branch must be carried into `_apply_card_filters` in Task 1 here.** Task 1 Step 3 says how to check.

**The rule everything depends on.** A card is bucketed by **`received_date`, falling back to `created_at` when null** — the client does this at `CollectionPage.tsx:70`, the server's `year`/`month` filters at `cards.py:78-99`. Facets must agree exactly, or a month header will say 12 and expanding it will show 11.

**Two operational facts:**

1. **Frontend edits do nothing until you build.** `cd frontend && npm run build`. The bundle is content-hashed — **hard-refresh with Cmd+Shift+R**.
2. **Backend changes need the LaunchAgent reloaded:**
   ```bash
   launchctl unload ~/Library/LaunchAgents/co.nxta.nxt-a1-backend.plist
   launchctl load  ~/Library/LaunchAgents/co.nxta.nxt-a1-backend.plist
   ```
   Confirm the live process with `lsof -ti :8000 | xargs ps -p`, not the log tail.

**Route ordering will bite you.** `cards.py:231` declares `GET /{card_ext_id}` and `persons.py:195` declares `GET /{person_ext_id}`. FastAPI matches in declaration order, so `/facets` and `/count` must be declared **before** those. A 422 or 404 on `/api/v2/cards/facets` means the catch-all swallowed it.

---

## File Structure

**Backend — create:**

| File | Responsibility |
|---|---|
| `migrations/versions/<rev>_add_collection_indexes.py` | Nine indexes |
| `tests/test_collection_scalability.py` | Facets, count, pagination, name batching, persons bounds |
| `tests/test_query_counts.py` | The performance guard — asserts query count stays bounded as rows grow |

**Backend — modify:**

| File | Change |
|---|---|
| `app/routers/v2/cards.py` | `_apply_card_filters` extraction; `/facets`; `/count`; batched name lookup |
| `app/routers/v2/persons.py` | batched name+country; bounded/ordered search; `/count`; cap 500 |
| `app/schemas/api.py` | `CardFacet`, `CountOut` |

**Frontend — create:**

| File | Responsibility |
|---|---|
| `frontend/src/hooks/useDebounced.ts` | Debounce a value. Trivial, but used by both tabs. |
| `frontend/src/components/LoadMore.tsx` | "Showing n of m" + button. Used by search results, each month, and the Persons tab — three call sites, so it earns its own file. |
| `frontend/src/components/MonthSection.tsx` | One month: header with facet count, lazy fetch on expand, its own pagination. Keeps `CollectionPage` readable. |

**Frontend — modify:**

| File | Change |
|---|---|
| `frontend/src/api/index.ts` | `listCardFacets`, `countCards`, `countPersons`, `listPersons` gains limit/offset |
| `frontend/src/pages/CollectionPage.tsx` | browse/search modes; deletes the client-side filter and cross-search |
| `frontend/src/types/index.ts` | `CardFacet` |
| `frontend/src/i18n.ts` | four new keys × 3 languages |

`CollectionPage.tsx` is 401 lines and will lose the grouping/filtering logic while gaining mode handling. Extracting `MonthSection` and `LoadMore` keeps it roughly the same size with clearer boundaries.

---

## Task 1: Extract the shared card filter builder

Pure refactor. No behaviour change — the existing tests are the proof.

**Files:**
- Modify: `app/routers/v2/cards.py:30-160`

- [ ] **Step 1: Run the existing tests and record the baseline**

Run: `cd nxt-a1-meishi && venv/bin/python3 -m pytest tests/ -q`
Expected: all pass. Note the count — it must not change in Step 5.

- [ ] **Step 2: Check whether the occasion search branch is already present**

Run: `cd nxt-a1-meishi && grep -n "occasion_label\|occasion_subq" app/routers/v2/cards.py || echo "NOT PRESENT"`

If it prints `NOT PRESENT`, the other plan has not landed — use the code in Step 3 as written.
If it prints matches, the other plan **has** landed — carry its `occasion_subq` and
`Card.occasion_label.ilike(like)` into the `or_()` inside `_apply_card_filters` below, and add
`Occasion` to its imports.

- [ ] **Step 3: Write the helper**

Add to `app/routers/v2/cards.py`, above `list_cards`:

```python
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

    Single owner of two rules that list_cards, count_cards and card_facets must agree on:
      * date bucketing — received_date, falling back to created_at when null
      * what ?q= matches
    If these three ever disagree, a month header count will not match the cards that
    appear when it is expanded.
    """
    from datetime import date as date_type

    from sqlalchemy import and_, exists, extract, or_

    from app.db.models import (
        CardMyCompany, CardSyncHistory, ContactDetail, Organization,
        OrganizationName, PersonName as PersonNameModel, Position, PositionDetail,
    )

    if person_id:
        stmt = stmt.where(Card.person_id == person_id)
    if occasion_id:
        stmt = stmt.where(Card.occasion_id == occasion_id)
    if my_company_id:
        mc_subq = select(CardMyCompany.card_id).where(CardMyCompany.my_company_id == my_company_id)
        stmt = stmt.where(Card.id.in_(mc_subq))

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

    if q:
        like = f"%{q}%"
        text_subq = select(PersonNameModel.person_id).where(
            PersonNameModel.person_id == Card.person_id,
            PersonNameModel.is_current == True,  # noqa: E712
            PersonNameModel.full_name.ilike(like),
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
```

- [ ] **Step 4: Route `list_cards` through it**

Replace everything in `list_cards` from the local `from datetime import…` import block down to the end of the `if q:` block with:

```python
    stmt = (
        select(Card)
        .where(Card.deleted_at.is_(None))
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
```

Keep the `CardSyncHistory` import that the sync-history block below still needs — it was in the
same local import block, so re-add it there:

```python
    from app.db.models import CardSyncHistory
```

- [ ] **Step 5: Run the tests — the count must be identical to Step 1**

Run: `cd nxt-a1-meishi && venv/bin/python3 -m pytest tests/ -q`
Expected: same number of tests, all passing. A refactor that changes results is a bug.

- [ ] **Step 6: Verify against live data**

```bash
launchctl unload ~/Library/LaunchAgents/co.nxta.nxt-a1-backend.plist
launchctl load  ~/Library/LaunchAgents/co.nxta.nxt-a1-backend.plist
sleep 3
curl -s -G localhost:8000/api/v2/cards --data-urlencode "limit=500" | venv/bin/python3 -c "import json,sys; print(len(json.load(sys.stdin)),'cards')"
curl -s -G localhost:8000/api/v2/cards --data-urlencode "q=Rotary" --data-urlencode "limit=500" | venv/bin/python3 -c "import json,sys; print(len(json.load(sys.stdin)),'for q=Rotary')"
curl -s -G localhost:8000/api/v2/cards --data-urlencode "month=2026-06" --data-urlencode "limit=500" | venv/bin/python3 -c "import json,sys; print(len(json.load(sys.stdin)),'for month=2026-06')"
```
Expected: `205 cards`, `85 for q=Rotary`, `78 for month=2026-06`.

**Pass `limit=500` on every one of these.** Without it the endpoint applies its default
`limit=50` and you will read 50 as the answer for anything with more than 50 matches.

- [ ] **Step 7: Commit**

```bash
git add app/routers/v2/cards.py
git commit -m "refactor: extract shared card filter builder"
```

---

## Task 2: Add `/cards/facets` and `/cards/count`

**Files:**
- Modify: `app/schemas/api.py`, `app/routers/v2/cards.py`
- Create: `tests/test_collection_scalability.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_collection_scalability.py`:

```python
"""Collection scalability: facets, counts and pagination.

The load-bearing assertion here is test_facets_counts_match_month_query — if facets
and the ?month= filter ever disagree, a month header shows one number and expanding
it shows another.
"""
import asyncio
from datetime import date, datetime

from app.db.session import get_db
from app.main import app


def _seed_cards(specs):
    """specs: list of (received_date | None, created_at). Returns [card_id]."""
    holder = {}

    async def _run():
        from app.db.models import Card, Person

        async for db in app.dependency_overrides[get_db]():
            person = Person(external_id="p-scale")
            db.add(person)
            await db.flush()

            ids = []
            for i, (recv, created) in enumerate(specs):
                card = Card(
                    external_id=f"c-scale-{i}",
                    person_id=person.id,
                    received_date=recv,
                    created_at=created,
                )
                db.add(card)
                await db.flush()
                ids.append(card.id)

            holder["ids"] = ids
            await db.commit()
            break

    asyncio.run(_run())
    return holder["ids"]


def test_facets_groups_by_received_date(client_with_test_db):
    """received_date wins over created_at for bucketing."""
    _seed_cards([(date(2026, 3, 15), datetime(2026, 9, 1))])

    facets = client_with_test_db.get("/api/v2/cards/facets").json()
    assert facets == [{"year": 2026, "month": 3, "count": 1}]


def test_facets_falls_back_to_created_at(client_with_test_db):
    _seed_cards([(None, datetime(2026, 9, 1))])

    facets = client_with_test_db.get("/api/v2/cards/facets").json()
    assert facets == [{"year": 2026, "month": 9, "count": 1}]


def test_facets_newest_first(client_with_test_db):
    _seed_cards([
        (date(2026, 3, 1), datetime(2026, 3, 1)),
        (date(2026, 9, 1), datetime(2026, 9, 1)),
        (date(2025, 12, 1), datetime(2025, 12, 1)),
    ])

    facets = client_with_test_db.get("/api/v2/cards/facets").json()
    assert [(f["year"], f["month"]) for f in facets] == [(2026, 9), (2026, 3), (2025, 12)]


def test_facets_counts_match_month_query(client_with_test_db):
    """Every facet count equals what ?month= actually returns. The guarantee."""
    _seed_cards([
        (date(2026, 9, 1), datetime(2026, 9, 1)),
        (date(2026, 9, 20), datetime(2026, 9, 20)),
        (None, datetime(2026, 8, 5)),
        (date(2025, 12, 31), datetime(2026, 1, 2)),
    ])

    facets = client_with_test_db.get("/api/v2/cards/facets").json()
    assert facets, "no facets returned"

    for f in facets:
        month = f"{f['year']}-{f['month']:02d}"
        rows = client_with_test_db.get("/api/v2/cards", params={"month": month, "limit": 500}).json()
        assert len(rows) == f["count"], f"facet {month} says {f['count']}, month query returned {len(rows)}"


def test_facets_excludes_soft_deleted(client_with_test_db):
    ids = _seed_cards([
        (date(2026, 9, 1), datetime(2026, 9, 1)),
        (date(2026, 9, 2), datetime(2026, 9, 2)),
    ])

    async def _soft_delete():
        from app.db.models import Card

        async for db in app.dependency_overrides[get_db]():
            card = await db.get(Card, ids[0])
            card.deleted_at = datetime.utcnow()
            await db.commit()
            break

    asyncio.run(_soft_delete())

    facets = client_with_test_db.get("/api/v2/cards/facets").json()
    assert facets == [{"year": 2026, "month": 9, "count": 1}]


def test_count_matches_list_length(client_with_test_db):
    _seed_cards([(date(2026, 9, i + 1), datetime(2026, 9, i + 1)) for i in range(7)])

    total = client_with_test_db.get("/api/v2/cards/count").json()["total"]
    rows = client_with_test_db.get("/api/v2/cards", params={"limit": 500}).json()
    assert total == len(rows) == 7


def test_count_respects_filters(client_with_test_db):
    _seed_cards([
        (date(2026, 9, 1), datetime(2026, 9, 1)),
        (date(2026, 8, 1), datetime(2026, 8, 1)),
    ])

    total = client_with_test_db.get("/api/v2/cards/count", params={"month": "2026-09"}).json()["total"]
    assert total == 1


def test_list_pagination_is_stable(client_with_test_db):
    """Two pages must not overlap or skip."""
    _seed_cards([(date(2026, 9, 1), datetime(2026, 9, 1, 0, i)) for i in range(30)])

    page1 = client_with_test_db.get("/api/v2/cards", params={"limit": 10, "offset": 0}).json()
    page2 = client_with_test_db.get("/api/v2/cards", params={"limit": 10, "offset": 10}).json()

    assert len(page1) == len(page2) == 10
    assert not ({c["id"] for c in page1} & {c["id"] for c in page2})


def test_month_query_paginates(client_with_test_db):
    """A month holding more rows than `limit` is fully reachable via offset."""
    _seed_cards([(date(2026, 9, 1), datetime(2026, 9, 1, 0, i)) for i in range(25)])

    first = client_with_test_db.get(
        "/api/v2/cards", params={"month": "2026-09", "limit": 20, "offset": 0}
    ).json()
    rest = client_with_test_db.get(
        "/api/v2/cards", params={"month": "2026-09", "limit": 20, "offset": 20}
    ).json()

    assert len(first) == 20
    assert len(rest) == 5
    assert not ({c["id"] for c in first} & {c["id"] for c in rest})
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd nxt-a1-meishi && venv/bin/python3 -m pytest tests/test_collection_scalability.py -v`

Expected: the facets and count tests FAIL with 422 or 404 (the `/{card_ext_id}` route is
matching `facets` as an ID). `test_list_pagination_is_stable` and `test_month_query_paginates`
should already PASS — they cover existing behaviour and guard it.

- [ ] **Step 3: Add the response schemas**

In `app/schemas/api.py`, after `CardListItem`:

```python
class CardFacet(BaseModel):
    """One year/month bucket and how many cards fall in it."""
    year: int
    month: int
    count: int


class CountOut(BaseModel):
    total: int
```

- [ ] **Step 4: Add both endpoints**

**Housekeeping while you are in the docstring:** its closing sentence reads "If these three
ever disagree" — "three" means the three caller functions, but the bullet list above it is now
four rules, so the antecedent is hard to find. Reword to name the callers explicitly.

**First, move the bucketing rule into one expression.** `_apply_card_filters` currently
encodes "received_date else created_at" with `extract` + an `is_(None)` fallback, and the
facets endpoint below would encode the same rule with `strftime` + `coalesce`. Two independent
encodings of the one rule the spec calls load-bearing is exactly the drift this design is
meant to prevent — and it would make `test_facets_counts_match_month_query` pass by
coincidence rather than by construction.

Define the bucket helpers **above `_apply_card_filters`** (not next to the endpoints), and
have the filter's own date branches use them:

```python
def _filing_date() -> ColumnElement:
    """The date a card is filed under: received_date, or created_at when it is NULL.

    Every date filter and the facets GROUP BY are built from this one expression,
    so a month header count cannot disagree with the cards that month returns.
    Spec: docs/superpowers/specs/2026-09-09-collection-scalability-design.md §3
    """
    return func.coalesce(Card.received_date, Card.created_at)


def _filing_year() -> ColumnElement:
    return func.extract('year', _filing_date())


def _filing_month() -> ColumnElement:
    return func.extract('month', _filing_date())
```

**Use `extract`, not a hand-rolled `strftime` + `cast`.** An earlier draft of this plan claimed
`extract()` returns a float on SQLite and cannot span a `Date` and a `DateTime`. That is false
on this stack — verified on SQLAlchemy 2.0.36, `func.extract('year', coalesce(...))` compiles
to exactly `CAST(STRFTIME('%Y', coalesce(...)) AS INTEGER)` and returns a Python `int`. The two
forms emit identical SQL, so use the shorter idiomatic one and drop the `Integer` import.

The real improvement here is `coalesce` replacing the previous OR-of-two-branches — that is
what collapses the rule to a single expression.

**Take no column arguments.** An earlier draft had these as `_bucket_year(col_recv=None,
col_created=None)` with a `col_recv or Card.received_date` body. That is broken: SQLAlchemy's
`ColumnElement.__bool__` raises `TypeError`, so passing an actual column — the only reason the
parameters would exist — crashes rather than works. No call site needs them.

Then replace the three date branches inside `_apply_card_filters` with:

```python
    if date:
        d = date_type.fromisoformat(date)
        stmt = stmt.where(func.date(_bucket_date()) == d)
    elif month:
        y, m = int(month[:4]), int(month[5:7])
        stmt = stmt.where(and_(_filing_year() == y, _filing_month() == m))
    elif year:
        stmt = stmt.where(_filing_year() == year)
```

This changes the SQL for an existing, working endpoint, so **verify it against live data
before moving on** — Task 1 recorded `month=2026-06` → 78 cards, id-checksum 10572:

```bash
curl -s -G localhost:8000/api/v2/cards --data-urlencode "month=2026-06" --data-urlencode "limit=500" \
  | venv/bin/python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d), sum(c['id'] for c in d))"
```
Expected: `78 10572`. A different checksum means the rewrite changed which cards match.

**Now add the endpoints.** In `app/routers/v2/cards.py`, **immediately after `list_cards` and
before the `@router.get("/{card_ext_id}")`** — declaration order decides matching:

```python
# ---------------------------------------------------------------------------
# Facets and count. MUST be declared before GET /{card_ext_id}, or FastAPI
# matches "facets" and "count" as card external IDs.
# ---------------------------------------------------------------------------


@router.get("/facets", response_model=List[CardFacet])
async def card_facets(
    person_id: Optional[int] = Query(None),
    occasion_id: Optional[int] = Query(None),
    my_company_id: Optional[int] = Query(None),
    q: Optional[str] = Query(None),
    not_exported: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """Year/month buckets with counts, newest first.

    One GROUP BY — the response stays small however many cards exist, which is what
    lets the Collection tree be complete at any scale.
    """
    y = _filing_year().label("year")
    m = _filing_month().label("month")

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
    my_company_id: Optional[int] = Query(None),
    q: Optional[str] = Query(None),
    year: Optional[int] = Query(None),
    month: Optional[str] = Query(None),
    date: Optional[str] = Query(None),
    not_exported: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(func.count(Card.id))
    stmt = _apply_card_filters(
        stmt,
        person_id=person_id, occasion_id=occasion_id, my_company_id=my_company_id,
        q=q, year=year, month=month, date=date, not_exported=not_exported,
    )
    return CountOut(total=await db.scalar(stmt) or 0)
```

Extend the imports at the top of the file:

```python
from sqlalchemy import Integer, func, select
```
```python
from app.schemas.api import CardFacet, CardListItem, CardOut, CardSideOut, CountOut
```

**On `extract`:** it compiles to `CAST(STRFTIME('%Y', …) AS INTEGER)` on SQLite and returns a
Python `int`, including across a `coalesce` of a `Date` and a `DateTime`. Verified on
SQLAlchemy 2.0.36.

- [ ] **Step 5: Run the tests**

Run: `cd nxt-a1-meishi && venv/bin/python3 -m pytest tests/test_collection_scalability.py -v`
Expected: 9 passed.

**Do not rely on `test_facets_groups_by_received_date` to guard the `cast`.** `CardFacet`
declares `year: int`, and Pydantic coerces `"03"` to `3`, so that test passes either way. If
you want to prove the cast is present, check the types over HTTP:

```bash
curl -s localhost:8000/api/v2/cards/facets | venv/bin/python3 -c "import json,sys; f=json.load(sys.stdin)[0]; print(type(f['year']).__name__, type(f['month']).__name__)"
```
Expected: `int int`.

- [ ] **Step 6: Run the whole suite**

Run: `cd nxt-a1-meishi && venv/bin/python3 -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 7: Verify against live data**

```bash
launchctl unload ~/Library/LaunchAgents/co.nxta.nxt-a1-backend.plist
launchctl load  ~/Library/LaunchAgents/co.nxta.nxt-a1-backend.plist
sleep 3
curl -s localhost:8000/api/v2/cards/facets
curl -s localhost:8000/api/v2/cards/count
```

Expected: facets list months from 2026-03 to 2026-09, and **the counts sum to 205**. Check that
sum explicitly:

```bash
curl -s localhost:8000/api/v2/cards/facets | venv/bin/python3 -c "import json,sys; print(sum(f['count'] for f in json.load(sys.stdin)))"
```
Expected: `205` — every card is in exactly one bucket, including the five the UI hides today.

- [ ] **Step 8: Commit**

```bash
git add app/schemas/api.py app/routers/v2/cards.py tests/test_collection_scalability.py
git commit -m "feat: add card facets and count endpoints"
```

---

## Decisions carried forward from Task 2 review

**Declined: extracting the shared `Query(...)` block into a `CardFilters` dependency.**
Three endpoints declare the same five filters verbatim. Attempted and reverted, for two
reasons found in the attempt:

1. `cards.py` has `from __future__ import annotations`, so every annotation is a string.
   FastAPI resolves a dependency's forward refs from `call.__globals__`, and a *class* has
   none — `app.openapi()` raises `PydanticUserError`. It needs a factory function
   (`def card_filters(...) -> CardFilters` with `Depends(card_filters)`), not a bare dataclass.
2. `tests/test_cards_filter.py` asserts `'q' in inspect.signature(list_cards).parameters`.
   Moving params into a dependency fails it.

The duplication is five `Query(...)` lines across three endpoints. Task 4's persons router
shares only `q`, so it will not copy this block — the "it'll be duplicated a fourth time"
argument does not hold. Not worth the machinery. **Leave it.**

**Known weakness, not yet addressed: `tests/test_cards_filter.py` tests nothing.**
All five of its tests assert only that query params appear in the function signature or
OpenAPI schema — they still pass with the filter bodies deleted. Two separate reviewers
flagged this. It is not in any task's scope; consider it during final review. If it is ever
rewritten, assert against the OpenAPI parameter list rather than `inspect.signature`, which
tests the actual contract and would also unblock the dependency extraction above.

**Note on `?q=` and occasions.** `_apply_card_filters` searches person names, contact values,
position titles/departments and organisation names — **not** occasion names. Occasion search
arrives with the sibling plan (`2026-09-09-scan-group-ui-and-occasion-lifecycle.md` Task 4).
Do not describe `q` as covering occasions until that lands.

---

## Task 3: Batch the card-name lookup

`_get_name` runs 1-2 queries per card inside the result loop — 10,000-20,000 sequential
queries at 10k cards.

**Files:**
- Modify: `app/routers/v2/cards.py:182-215`
- Create: `tests/test_query_counts.py`

- [ ] **Step 1: Write the behaviour test**

Append to `tests/test_collection_scalability.py`:

```python
def test_name_batch_honours_display_language(client_with_test_db):
    """display_name_language picks the matching name; otherwise lowest-id current name."""
    holder = {}

    async def _seed():
        from app.db.models import Card, Person, PersonName

        async for db in app.dependency_overrides[get_db]():
            person = Person(external_id="p-names")
            db.add(person)
            await db.flush()

            # Insertion order matters: the ja name has the lower id, so it is the fallback.
            db.add(PersonName(person_id=person.id, language="ja", name_type="legal",
                              full_name="山田太郎", is_current=True))
            db.add(PersonName(person_id=person.id, language="en", name_type="legal",
                              full_name="Taro Yamada", is_current=True))
            await db.flush()

            prefers_en = Card(external_id="c-en", person_id=person.id,
                              display_name_language="en")
            prefers_none = Card(external_id="c-none", person_id=person.id,
                                display_name_language=None)
            db.add_all([prefers_en, prefers_none])
            await db.flush()
            holder["ids"] = {"en": prefers_en.external_id, "none": prefers_none.external_id}
            await db.commit()
            break

    asyncio.run(_seed())

    rows = client_with_test_db.get("/api/v2/cards", params={"limit": 500}).json()
    by_ext = {r["external_id"]: r["person_name"] for r in rows}

    assert by_ext[holder["ids"]["en"]] == "Taro Yamada"
    assert by_ext[holder["ids"]["none"]] == "山田太郎"
```

- [ ] **Step 2: Write the performance guard**

Create `tests/test_query_counts.py`:

```python
"""Performance guard: the card list must not issue a query per card.

Without this, an N+1 can creep back silently — it stays fast on a small database
and only hurts once a real collection grows.
"""
import asyncio
from datetime import date, datetime

from sqlalchemy import event

from app.db.session import get_db
from app.main import app


def _seed_many(n):
    async def _run():
        from app.db.models import Card, Person, PersonName

        async for db in app.dependency_overrides[get_db]():
            for i in range(n):
                person = Person(external_id=f"p-{i}")
                db.add(person)
                await db.flush()
                db.add(PersonName(person_id=person.id, language="en", name_type="legal",
                                  full_name=f"Person {i}", is_current=True))
                db.add(Card(external_id=f"c-{i}", person_id=person.id,
                            received_date=date(2026, 9, 1),
                            created_at=datetime(2026, 9, 1, 0, i % 60)))
            await db.commit()
            break

    asyncio.run(_run())


def test_card_list_query_count_does_not_scale_with_rows(client_with_test_db):
    _seed_many(60)

    engine = client_with_test_db.session_maker.kw["bind"]
    counter = {"n": 0}

    def _count(conn, cursor, statement, parameters, context, executemany):
        counter["n"] += 1

    event.listen(engine.sync_engine, "before_cursor_execute", _count)
    try:
        rows = client_with_test_db.get("/api/v2/cards", params={"limit": 500}).json()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _count)

    assert len(rows) == 60
    # Cards + sides + sync history + names ≈ a handful. Anything near 60 is an N+1.
    assert counter["n"] < 15, f"{counter['n']} queries for 60 cards — N+1 regression"
```

- [ ] **Step 3: Run both to verify the guard fails**

Run: `cd nxt-a1-meishi && venv/bin/python3 -m pytest tests/test_query_counts.py -v`
Expected: FAIL — roughly 60-70 queries for 60 cards, well over the threshold.

`test_name_batch_honours_display_language` should already PASS; it pins the behaviour the
refactor must preserve.

- [ ] **Step 4: Replace the per-card lookup with one batched query**

In `app/routers/v2/cards.py`, delete the `async def _get_name(...)` helper (lines ~182-198)
and replace the item-building loop:

```python
    # One query for every name we might need, then resolve preference in memory.
    # Was 1-2 queries per card inside the loop below.
    person_ids = {c.person_id for c in rows}
    names_by_person: dict[int, list[tuple[str, Optional[str]]]] = {}
    if person_ids:
        name_rows = (await db.execute(
            select(PersonName.person_id, PersonName.language, PersonName.full_name)
            .where(
                PersonName.person_id.in_(person_ids),
                PersonName.is_current == True,  # noqa: E712
            )
            .order_by(PersonName.person_id, PersonName.id)
        )).all()
        for pid, lang, full in name_rows:
            names_by_person.setdefault(pid, []).append((lang, full))

    def _pick_name(pid: int, lang: Optional[str]) -> Optional[str]:
        """First name whose language starts with `lang`, else the lowest-id current name.
        Mirrors the old _get_name exactly — rows are already ordered by PersonName.id."""
        candidates = names_by_person.get(pid, [])
        if lang:
            for cand_lang, full in candidates:
                if cand_lang and cand_lang.startswith(lang) and full:
                    return full
        for _, full in candidates:
            if full:
                return full
        return None

    items = []
    for card in rows:
        name = _pick_name(card.person_id, card.display_name_language)
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

- [ ] **Step 5: Run both tests**

Run: `cd nxt-a1-meishi && venv/bin/python3 -m pytest tests/test_query_counts.py tests/test_collection_scalability.py -v`
Expected: all pass; the query counter now reports a single-digit number.

- [ ] **Step 6: Run the whole suite and re-check live data**

```bash
cd nxt-a1-meishi && venv/bin/python3 -m pytest tests/ -q
launchctl unload ~/Library/LaunchAgents/co.nxta.nxt-a1-backend.plist
launchctl load  ~/Library/LaunchAgents/co.nxta.nxt-a1-backend.plist
sleep 3
curl -s -o /dev/null -w "%{time_total}s\n" -G localhost:8000/api/v2/cards --data-urlencode "limit=500"
```
Expected: all tests pass; the request is no slower than the ~60ms baseline.

Spot-check that names still render: open the Collection page and confirm cards still show
person names (the page still caps at 200 — that is fixed in Task 6).

- [ ] **Step 7: Commit**

```bash
git add app/routers/v2/cards.py tests/test_collection_scalability.py tests/test_query_counts.py
git commit -m "perf: batch the card-name lookup instead of one query per card"
```

---

## Task 4: Fix and bound the persons endpoint

Three problems in `list_persons`: the `if q:` branch has **no limit, offset or order_by**
(`persons.py:135-150`), so search returns everything in nondeterministic order; the item loop
runs **two queries per person**; and the browse cap of 50 silently truncates against 202 persons.

**Files:**
- Modify: `app/routers/v2/persons.py:128-190`
- Test: `tests/test_collection_scalability.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_collection_scalability.py`:

```python
def _seed_persons(n, name_prefix="Tester"):
    async def _run():
        from app.db.models import Person, PersonName

        async for db in app.dependency_overrides[get_db]():
            for i in range(n):
                person = Person(external_id=f"pp-{i}")
                db.add(person)
                await db.flush()
                db.add(PersonName(person_id=person.id, language="en", name_type="legal",
                                  full_name=f"{name_prefix} {i}", is_current=True))
            await db.commit()
            break

    asyncio.run(_run())


def test_persons_search_is_bounded(client_with_test_db):
    """A query matching more than `limit` persons returns `limit`, not everything."""
    _seed_persons(60)

    rows = client_with_test_db.get("/api/v2/persons", params={"q": "Tester", "limit": 10}).json()
    assert len(rows) == 10


def test_persons_search_is_ordered(client_with_test_db):
    """Two identical requests return the same rows in the same order."""
    _seed_persons(30)

    a = client_with_test_db.get("/api/v2/persons", params={"q": "Tester", "limit": 10}).json()
    b = client_with_test_db.get("/api/v2/persons", params={"q": "Tester", "limit": 10}).json()
    assert [r["id"] for r in a] == [r["id"] for r in b]


def test_persons_search_paginates(client_with_test_db):
    _seed_persons(30)

    p1 = client_with_test_db.get("/api/v2/persons", params={"q": "Tester", "limit": 10, "offset": 0}).json()
    p2 = client_with_test_db.get("/api/v2/persons", params={"q": "Tester", "limit": 10, "offset": 10}).json()
    assert not ({r["id"] for r in p1} & {r["id"] for r in p2})


def test_persons_limit_accepts_500(client_with_test_db):
    """The cap was 200; the Collection page needs to match cards at 500."""
    _seed_persons(5)

    resp = client_with_test_db.get("/api/v2/persons", params={"limit": 500})
    assert resp.status_code == 200


def test_persons_count_matches_search(client_with_test_db):
    _seed_persons(12)

    total = client_with_test_db.get("/api/v2/persons/count", params={"q": "Tester"}).json()["total"]
    rows = client_with_test_db.get("/api/v2/persons", params={"q": "Tester", "limit": 500}).json()
    assert total == len(rows) == 12
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd nxt-a1-meishi && venv/bin/python3 -m pytest tests/test_collection_scalability.py -k persons -v`

Expected: `test_persons_search_is_bounded` FAILS (returns 60, not 10);
`test_persons_limit_accepts_500` FAILS with 422 (`le=200`);
`test_persons_count_matches_search` FAILS with 422/404 (no `/count` route yet).
The ordering and pagination tests may pass by luck — keep them, they pin the guarantee.

- [ ] **Step 3: Extract the person-id search into a helper**

In `app/routers/v2/persons.py`, above `list_persons`:

```python
async def _person_ids_matching(db: AsyncSession, q: str) -> set[int]:
    """Person ids whose current name or current organisation name matches q.

    Shared by list_persons and count_persons so the two cannot disagree.
    """
    like = f"%{q}%"
    matched_name_ids = (await db.execute(
        select(PersonName.person_id)
        .where(PersonName.is_current == True, PersonName.full_name.ilike(like))  # noqa: E712
    )).scalars().all()
    matched_org_ids = (await db.execute(
        select(Position.person_id)
        .join(OrganizationName, OrganizationName.org_id == Position.org_id)
        .where(OrganizationName.is_current == True, OrganizationName.name.ilike(like))  # noqa: E712
    )).scalars().all()
    return set(matched_name_ids) | set(matched_org_ids)
```

- [ ] **Step 4: Rewrite `list_persons`**

Replace the whole function body (`persons.py:128-190`):

```python
@router.get("", response_model=List[PersonListItem])
async def list_persons(
    q: Optional[str] = Query(None, description="Search by name or organisation"),
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Person)
    if q:
        stmt = stmt.where(Person.id.in_(await _person_ids_matching(db, q)))
    # Both branches order and paginate identically — the search branch previously did
    # neither, so it returned every match in nondeterministic order.
    stmt = stmt.order_by(Person.created_at.desc(), Person.id.desc()).limit(limit).offset(offset)

    persons = (await db.execute(stmt)).scalars().all()
    person_ids = [p.id for p in persons]
    if not person_ids:
        return []

    # One query for names, one for countries — was two queries per person.
    name_rows = (await db.execute(
        select(PersonName.person_id, PersonName.full_name, PersonName.family_name)
        .where(PersonName.person_id.in_(person_ids), PersonName.is_current == True)  # noqa: E712
        .order_by(PersonName.person_id, PersonName.id)
    )).all()
    names_by_person: dict[int, tuple[Optional[str], Optional[str]]] = {}
    for pid, full, family in name_rows:
        names_by_person.setdefault(pid, (full, family))

    country_rows = (await db.execute(
        select(ContactDetail.person_id, ContactDetail.country_code)
        .where(
            ContactDetail.person_id.in_(person_ids),
            ContactDetail.detail_type.in_(["address_home", "address_work"]),
            ContactDetail.country_code.isnot(None),
        )
        # address_home sorts before address_work, preserving the old preference
        .order_by(ContactDetail.person_id, ContactDetail.detail_type.asc(), ContactDetail.id.asc())
    )).all()
    country_by_person: dict[int, str] = {}
    for pid, code in country_rows:
        country_by_person.setdefault(pid, code)

    return [
        PersonListItem(
            id=p.id,
            external_id=p.external_id,
            primary_name=names_by_person.get(p.id, (None, None))[0],
            family_name=names_by_person.get(p.id, (None, None))[1],
            country_code=country_by_person.get(p.id),
            created_at=p.created_at,
        )
        for p in persons
    ]
```

- [ ] **Step 5: Add `/persons/count` before the catch-all**

Insert **immediately after `list_persons` and before `@router.get("/{person_ext_id}")`
(line ~195)** — same declaration-order rule as cards:

```python
# MUST precede GET /{person_ext_id}, or "count" is matched as an external ID.
@router.get("/count", response_model=CountOut)
async def count_persons(
    q: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(func.count(Person.id))
    if q:
        stmt = stmt.where(Person.id.in_(await _person_ids_matching(db, q)))
    return CountOut(total=await db.scalar(stmt) or 0)
```

Add `CountOut` to the `app.schemas.api` import block at the top of `persons.py`.

- [ ] **Step 6: Run the tests**

Run: `cd nxt-a1-meishi && venv/bin/python3 -m pytest tests/test_collection_scalability.py -v`
Expected: all pass.

- [ ] **Step 7: Run the whole suite**

Run: `cd nxt-a1-meishi && venv/bin/python3 -m pytest tests/ -q`
Expected: all pass. `test_person_merge.py` exercises this router — if it fails, the
`PersonListItem` field mapping was changed rather than preserved.

- [ ] **Step 8: Verify against live data**

```bash
launchctl unload ~/Library/LaunchAgents/co.nxta.nxt-a1-backend.plist
launchctl load  ~/Library/LaunchAgents/co.nxta.nxt-a1-backend.plist
sleep 3
curl -s localhost:8000/api/v2/persons/count
curl -s -G localhost:8000/api/v2/persons --data-urlencode "limit=500" | venv/bin/python3 -c "import json,sys; print(len(json.load(sys.stdin)),'persons')"
```
Expected: `{"total":202}` and `202 persons` — previously capped at 50.

- [ ] **Step 9: Commit**

```bash
git add app/routers/v2/persons.py tests/test_collection_scalability.py
git commit -m "fix: bound and order the persons search, batch its N+1 lookups, add count"
```

---

## Task 5: Add the indexes

**Files:**
- Create: `migrations/versions/<rev>_add_collection_indexes.py`

- [ ] **Step 1: Find the current Alembic head**

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

Use whatever it prints as `down_revision` below. It will be `e1f2a3b4c5d6` on a clean `main`,
or `f2a3b4c5d6e7` if the scan/occasion plan landed first.

- [ ] **Step 2: Write the migration**

Create `migrations/versions/a3b4c5d6e7f8_add_collection_indexes.py`:

```python
"""add_collection_indexes

The first indexes on these tables. SQLite does not index foreign keys automatically,
so every filter and join above was a full scan. Immaterial at a few hundred rows; the
point is that removing the 200-card ceiling does not just replace it with a slow one.

Revision ID: a3b4c5d6e7f8
Revises: <PASTE THE HEAD FROM STEP 1>
Create Date: 2026-09-10

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = 'a3b4c5d6e7f8'
down_revision: Union[str, None] = '<PASTE THE HEAD FROM STEP 1>'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEXES = [
    ("ix_cards_person_id", "cards", ["person_id"]),
    ("ix_cards_occasion_id", "cards", ["occasion_id"]),
    # NOT single-column indexes on created_at / received_date. Every date path — the
    # list ORDER BY, the ?year=/?month=/?date= filters, and the facets GROUP BY — is
    # built from coalesce(received_date, created_at), and no single-column index can
    # serve an expression. This one must also LEAD with deleted_at: see the note below.
    ("ix_cards_filing_date", "cards",
     [sa.text("deleted_at"), sa.text("coalesce(received_date, created_at) DESC"), sa.text("id DESC")]),
    ("ix_cards_deleted_at", "cards", ["deleted_at"]),
    # Composite, not single-column: the batched lookups in Tasks 3 and 4 filter on
    # person_id AND is_current / detail_type together. Verified on live data that the
    # name query currently does "SCAN person_names" + "USE TEMP B-TREE FOR ORDER BY".
    ("ix_person_names_person_current", "person_names", ["person_id", "is_current"]),
    ("ix_contact_details_person_type", "contact_details", ["person_id", "detail_type"]),
    ("ix_positions_person_id", "positions", ["person_id"]),
    ("ix_card_sync_history_card_id", "card_sync_history", ["card_id"]),
]


def upgrade() -> None:
    for name, table, cols in _INDEXES:
        op.create_index(name, table, cols)


def downgrade() -> None:
    for name, table, _ in reversed(_INDEXES):
        op.drop_index(name, table_name=table)
```

**Why the filing-date index leads with `deleted_at`, and the verification trap behind it.**

`_apply_card_filters` adds `deleted_at IS NULL` to every list, count and facets query, and
`ix_cards_deleted_at` exists. Nothing in the app ever runs `ANALYZE`, so there is no
`sqlite_stat1` and SQLite falls back to a **default selectivity estimate** for `deleted_at = ?`
— which makes that narrow index look cheap. The planner takes it and then sorts in a temp
B-tree. A bare `coalesce(...) DESC, id DESC` index is *matchable* but never *chosen*: measured
plan-for-plan and microsecond-for-microsecond identical to having no index at all, at 208 rows,
at 5,200, and at 100,000.

(An earlier draft of this note had the cause backwards — it blamed statistics being present and
averaged. It is their **absence**. After an `ANALYZE` the planner does start choosing a bare
`coalesce` index for the default list; the `deleted_at`-leading index still wins, because
leading with `deleted_at` prunes rather than scanning the whole index — 1.088 ms vs 2.013 ms on
`?month=`. The decision holds in both states, but for the opposite reason to the one first
written down.)

**The index is declared only in the migration, never on the model** — so
`alembic revision --autogenerate` would propose dropping all seven *reflectable* indexes while
silently keeping the expression one, which SQLAlchemy cannot reflect. An `include_object()` hook
in `migrations/env.py`, keyed on a `_MIGRATION_ONLY_INDEXES` frozenset, suppresses exactly these
eight names and nothing else. Verified with a negative control: an index outside the set is
still detected and proposed for drop.

An `EXPLAIN QUERY PLAN` probe on a hand-built table suggested the bare index would win, because
that table had no `ix_cards_deleted_at`. **Probe the real schema, using the SQL the endpoint
actually emits** — compile the statement rather than hand-writing an approximation.

`ix_cards_deleted_at` becomes a strict prefix of this index and is redundant for everything
except the bare `/cards/count`, where it is still measurably better as the narrower covering
index. Keep it; drop it if `/cards/count` ever gains filters.

- [ ] **Step 3: Confirm every table and column named actually exists**

Run:
```bash
cd nxt-a1-meishi && PYTHONPATH=. venv/bin/python3 -c "
from app.db.models import Base
want = [('cards',['person_id','occasion_id','created_at','received_date','deleted_at']),
        ('person_names',['person_id','is_current']),
        ('contact_details',['person_id','detail_type']),
        ('positions',['person_id']),('card_sync_history',['card_id'])]
for t, cols in want:
    tbl = Base.metadata.tables[t]
    for c in cols:
        assert c in tbl.columns, f'{t}.{c} MISSING'
print('all index targets exist')
"
```
Expected: `all index targets exist`. A `KeyError` means a table name is wrong — fix it before
running the migration, or startup will crash-loop.

- [ ] **Step 4: Apply it by restarting the backend**

The app runs Alembic at startup. Do **not** run `alembic upgrade` by hand against
`~/.nxt-a1/meishi.db`.

```bash
launchctl unload ~/Library/LaunchAgents/co.nxta.nxt-a1-backend.plist
launchctl load  ~/Library/LaunchAgents/co.nxta.nxt-a1-backend.plist
sleep 4
curl -s localhost:8000/api/v1/health
tail -20 /tmp/nxt-a1-backend.log
```
Expected: health OK, and no traceback in the log.

- [ ] **Step 4b: Confirm the indexes are actually used**

An index that the planner ignores is worse than none — it costs writes and buys nothing.
Check the two that back the batched lookups:

```bash
sqlite3 ~/.nxt-a1/meishi.db "EXPLAIN QUERY PLAN SELECT person_id, language, full_name FROM person_names WHERE person_id IN (1,2,3) AND is_current = 1 ORDER BY person_id, id;"
```
Expected: a `SEARCH person_names USING INDEX ix_person_names_person_current` line. Before the
migration this reads `SCAN person_names` plus `USE TEMP B-TREE FOR ORDER BY`.

- [ ] **Step 5: Confirm the indexes exist**

Run:
```bash
sqlite3 ~/.nxt-a1/meishi.db "select name from sqlite_master where type='index' and name like 'ix_%' order by name;"
```
Expected: all nine `ix_` names listed.

- [ ] **Step 6: Commit**

```bash
git add migrations/versions/a3b4c5d6e7f8_add_collection_indexes.py
git commit -m "perf: add the first indexes on cards, names, contacts and sync history"
```

---

## Task 6: Browse mode — facets tree with lazy months

> **Implemented.** The committed code (`31b000c`, `dfdfb39`, `bd71101`; guard spelling tidied in
> `9610765`) supersedes the snippets below — especially their pagination (a page count in the
> query key, not `useInfiniteQuery`) and loading-state code (`= []` defaults, `isLoading` /
> `isFetching` guards). Copy patterns from `MonthSection.tsx` and `CollectionPage.tsx` as
> committed, not from this section.

This is the task that makes the five hidden cards reappear.

**Files:**
- Create: `frontend/src/components/LoadMore.tsx`, `frontend/src/components/MonthSection.tsx`
- Modify: `frontend/src/types/index.ts`, `frontend/src/api/index.ts`, `frontend/src/pages/CollectionPage.tsx`, `frontend/src/i18n.ts`

- [ ] **Step 1: Add the type and API functions**

In `frontend/src/types/index.ts`:

```ts
/** One year/month bucket from GET /api/v2/cards/facets. */
export interface CardFacet {
  year: number
  month: number
  count: number
}
```

In `frontend/src/api/index.ts`, after `listCards`:

```ts
type CardFilters = Parameters<typeof listCards>[0]

const cardFilterQS = (params?: CardFilters) => {
  const qs = new URLSearchParams()
  if (params?.person_id) qs.set('person_id', String(params.person_id))
  if (params?.occasion_id) qs.set('occasion_id', String(params.occasion_id))
  if (params?.my_company_id) qs.set('my_company_id', String(params.my_company_id))
  if (params?.q) qs.set('q', params.q)
  if (params?.year) qs.set('year', String(params.year))
  if (params?.month) qs.set('month', params.month)
  if (params?.date) qs.set('date', params.date)
  if (params?.not_exported) qs.set('not_exported', 'true')
  return qs
}

export const listCardFacets = (params?: CardFilters) =>
  get<import('../types').CardFacet[]>(`/api/v2/cards/facets?${cardFilterQS(params)}`)

export const countCards = (params?: CardFilters) =>
  get<{ total: number }>(`/api/v2/cards/count?${cardFilterQS(params)}`)

export const countPersons = (q?: string) =>
  get<{ total: number }>(`/api/v2/persons/count${q ? `?q=${encodeURIComponent(q)}` : ''}`)
```

Also give `listPersons` pagination:

```ts
export const listPersons = (q?: string, limit = 50, offset = 0) => {
  const qs = new URLSearchParams()
  if (q) qs.set('q', q)
  qs.set('limit', String(limit))
  qs.set('offset', String(offset))
  return get<PersonListItem[]>(`/api/v2/persons?${qs}`)
}
```

- [ ] **Step 2: Add the i18n keys**

In each of the three blocks of `frontend/src/i18n.ts`:

```ts
    // ja
    resultsN: (n: number) => `${n}件`,
    loadMore: 'さらに読み込む',
    showingNofM: (n: number, m: number) => `${m}件中 ${n}件を表示`,
    noResults: (q: string) => `「${q}」に一致する名刺はありません`,
```
```ts
    // en
    resultsN: (n: number) => `${n} result${n === 1 ? '' : 's'}`,
    loadMore: 'Load more',
    showingNofM: (n: number, m: number) => `Showing ${n} of ${m}`,
    noResults: (q: string) => `No cards match "${q}"`,
```
```ts
    // zh-TW
    resultsN: (n: number) => `${n} 筆`,
    loadMore: '載入更多',
    showingNofM: (n: number, m: number) => `顯示 ${m} 筆中的 ${n} 筆`,
    noResults: (q: string) => `沒有符合「${q}」的名片`,
```

- [ ] **Step 3: Write the shared LoadMore component**

Create `frontend/src/components/LoadMore.tsx`:

```tsx
import { useLang } from '../LangContext'

interface Props {
  loaded: number
  total: number
  onLoadMore: () => void
  isLoading?: boolean
}

/** "Showing n of m" plus a button, hidden once everything is loaded.
 *  Used by search results, each month section, and the Persons tab. */
export function LoadMore({ loaded, total, onLoadMore, isLoading }: Props) {
  const { t } = useLang()
  if (loaded >= total) return null

  return (
    <div className="flex flex-col items-center gap-1 py-3">
      <p className="text-xs text-gray-400">{t.showingNofM(loaded, total)}</p>
      <button
        onClick={onLoadMore}
        disabled={isLoading}
        className="btn-secondary text-sm disabled:opacity-50"
      >
        {isLoading ? t.loading : t.loadMore}
      </button>
    </div>
  )
}
```

- [ ] **Step 4: Write the MonthSection component**

Create `frontend/src/components/MonthSection.tsx`:

```tsx
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { listCards } from '../api'
import type { CardListItem } from '../types'
import { LoadMore } from './LoadMore'
import { useLang } from '../LangContext'

const PAGE = 500  // the API maximum

interface Props {
  year: number
  month: number
  /** Authoritative count from the facets endpoint — correct even before any fetch. */
  count: number
  defaultExpanded: boolean
  renderCard: (card: CardListItem) => React.ReactNode
}

/**
 * One month in the browse tree. Fetches its cards only once expanded, and paginates
 * within the month so a month larger than PAGE is still fully reachable — the facet
 * count is the authority on whether more remain.
 */
export function MonthSection({ year, month, count, defaultExpanded, renderCard }: Props) {
  const { t } = useLang()
  const [expanded, setExpanded] = useState(defaultExpanded)
  const [pages, setPages] = useState(1)
  const monthKey = `${year}-${String(month).padStart(2, '0')}`

  const { data: cards = [], isFetching } = useQuery<CardListItem[]>({
    queryKey: ['cards', 'month', monthKey, pages],
    queryFn: async () => {
      const batches = await Promise.all(
        Array.from({ length: pages }, (_, i) =>
          listCards({ month: monthKey, limit: PAGE, offset: i * PAGE }),
        ),
      )
      return batches.flat()
    },
    enabled: expanded,
  })

  return (
    <div className="ml-4 mb-2">
      <button
        className="flex items-center gap-2 text-xs font-medium text-gray-500 py-0.5 hover:text-blue-500"
        onClick={() => setExpanded(v => !v)}
      >
        <span className="text-gray-400">{expanded ? '▼' : '▶'}</span>
        <span>{monthKey}</span>
        <span className="text-gray-400">({count})</span>
      </button>

      {expanded && (
        <>
          {isFetching && cards.length === 0 ? (
            <p className="text-xs text-gray-400 py-2">{t.loading}</p>
          ) : (
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3 mt-2">
              {cards.map(renderCard)}
            </div>
          )}
          <LoadMore
            loaded={cards.length}
            total={count}
            isLoading={isFetching}
            onLoadMore={() => setPages(p => p + 1)}
          />
        </>
      )}
    </div>
  )
}
```

- [ ] **Step 5: Rewrite the browse branch of CollectionPage**

In `CollectionPage.tsx`, replace the `cards` query and the `cardsByYearMonth` memo with a
facets query, and the cards-tab JSX with the tree. Delete `collapsedMonths` state — months own
their own expansion now. Keep `collapsedYears`.

```tsx
  const { data: facets = [], isLoading: facetsLoading } = useQuery<CardFacet[]>({
    queryKey: ['cards', 'facets'],
    queryFn: () => listCardFacets(),
    enabled: view === 'cards' && !debouncedQ,
  })

  // Facets are newest-first, so the first three are the most recent months.
  const eagerMonths = useMemo(
    () => new Set(facets.slice(0, 3).map(f => `${f.year}-${f.month}`)),
    [facets],
  )

  const facetsByYear = useMemo(() => {
    const years = new Map<number, CardFacet[]>()
    for (const f of facets) {
      if (!years.has(f.year)) years.set(f.year, [])
      years.get(f.year)!.push(f)
    }
    return [...years.entries()].sort((a, b) => b[0] - a[0])
  }, [facets])
```

and the browse JSX:

```tsx
          <div className="space-y-2">
            {facetsByYear.map(([year, months]) => {
              const yearCollapsed = collapsedYears.has(year)
              const yearCount = months.reduce((n, f) => n + f.count, 0)
              return (
                <div key={year}>
                  <button
                    className="w-full flex items-center gap-2 text-sm font-semibold text-gray-700 py-1 hover:text-blue-600 text-left"
                    onClick={() => toggleYear(year)}
                  >
                    <span className="text-xs text-gray-400">{yearCollapsed ? '▶' : '▼'}</span>
                    <span>{year}</span>
                    <span className="text-xs text-gray-400">({yearCount})</span>
                  </button>
                  {!yearCollapsed && months.map(f => (
                    <MonthSection
                      key={`${f.year}-${f.month}`}
                      year={f.year}
                      month={f.month}
                      count={f.count}
                      defaultExpanded={eagerMonths.has(`${f.year}-${f.month}`)}
                      renderCard={card => <CardThumbnail key={card.id} card={card} />}
                    />
                  ))}
                </div>
              )
            })}
          </div>
```

Use `facetsLoading` where `cardsLoading` was used, and show `<EmptyState />` when
`facets.length === 0`.

`debouncedQ` arrives in Task 7; until then define `const debouncedQ = q` at the top of the
component so this task compiles and can be tested on its own.

- [ ] **Step 6: Build**

Run: `cd nxt-a1-meishi/frontend && npm run build`
Expected: no errors. Unused-import errors for `listCards` in `CollectionPage` are expected —
remove it there; `MonthSection` imports it now.

- [ ] **Step 7: Verify in the browser — the headline check**

Hard-refresh, then:
1. The Collection page shows a year/month tree. The three most recent months are expanded with
   thumbnails; older months are collapsed with counts.
2. **Expand `2026-03` and `2026-04`.** 徐子恆, 張雅純, 解沛誼, 楊嘉明 and 何鈞軒 appear.
   **These five are invisible before this task** — this is the fix.
3. Every month header count matches the number of thumbnails inside when expanded.
4. Sum the year headers: **205**.
5. Collapse and re-expand a month — it does not refetch (TanStack cache).

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/LoadMore.tsx frontend/src/components/MonthSection.tsx frontend/src/pages/CollectionPage.tsx frontend/src/api/index.ts frontend/src/types/index.ts frontend/src/i18n.ts
git commit -m "feat: browse the collection via a facets tree with lazily loaded months"
```

---

## Task 7: Search mode — server-side, debounced, paginated

> **Render loading / error / empty from data presence and `isLoadingError` — never from `isLoading` or `isSuccess`.**
> In TanStack Query v5 (installed: 5.95.2) `isLoading` is `isPending && isFetching`. A query can
> sit at `status: 'pending'` with `fetchStatus: 'paused'` — when a retry comes due while the tab
> is **hidden** (tab visibility, not window focus), or after the connection drops once the page
> has loaded (TanStack's `onlineManager` starts online and changes only on the browser's
> `online`/`offline` events). Then `isLoading` and `isError` are both false and `data` is
> undefined, so `isLoading ? … : isError ? … : data.length === 0 ? <Empty/>` shows the empty
> state — "Scan your first card" to a user with 205 cards. Separately, `status` stays `'error'`
> even when earlier data is cached, so gating on `!isSuccess` would hide loaded data behind a
> spinner after a failed background refetch. Order the branches:
> 1. `isLoadingError` (error **and** no data) → error UI with retry
> 2. `data === undefined` (pending — fetching or paused) → loading
> 3. data empty → empty state
> 4. otherwise the data (a failed background refetch keeps it on screen)
> 5. under the rows: a failed **later** page (`isFetchNextPageError`) →
>    `<LoadError onRetry={() => fetchNextPage()} isRetrying={isFetchingNextPage} />` in place of
>    the pager; otherwise `<LoadMore … isLoading={isFetchingNextPage} />` — and render either one
>    only while `hasNextPage`, because `LoadMore` keeps its button whenever the total is unknown. Without this a failed
>    page 2 shows nothing at all. Use `isFetchingNextPage`, not `isFetching`, which is also true
>    while page one refetches in the background.
>
> Take rows as `data?.pages.flat()` with **no `?? []`**, so step 2 can test `=== undefined`.
> Where several queries feed one view, the view has no data until **all** of them do. (The
> client-side search this task replaces needed the persons cross-search too. The server-side
> search below has a single rows query; its `/count` total only sizes the pager, so it gates
> nothing — `LoadMore` treats an `undefined` total as "more may exist".)
> Caught in the browser during Task 6 verification: the code passed build, grep and review.
> `MonthSection.tsx` as committed is the reference implementation of all five steps.

**Files:**
- Create: `frontend/src/hooks/useDebounced.ts`
- Modify: `frontend/src/pages/CollectionPage.tsx`, `frontend/src/i18n.ts`

- [ ] **Step 1: Write the debounce hook**

Create `frontend/src/hooks/useDebounced.ts`:

```ts
import { useEffect, useState } from 'react'

/** Returns `value` after it has stopped changing for `delay` ms.
 *  Keeps a fast typist from firing a request per keystroke. */
export function useDebounced<T>(value: T, delay = 300): T {
  const [debounced, setDebounced] = useState(value)

  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(id)
  }, [value, delay])

  return debounced
}
```

- [ ] **Step 2: Replace the client-side filter with a server query**

In `CollectionPage.tsx`, replace the placeholder `const debouncedQ = q` from Task 6 with:

```tsx
  const debouncedQ = useDebounced(q, 300)

  const SEARCH_PAGE = 50

  // The total is declared FIRST, on purpose. getNextPageParam below reads `searchTotal`,
  // and TanStack calls getNextPageParam synchronously inside useInfiniteQuery (to compute
  // `hasNextPage`) on every render once a page is cached. Declared after the infinite
  // query, `searchTotal` would still be in its temporal dead zone at that moment and the
  // render would throw a ReferenceError as soon as the first page arrived.
  const { data: searchTotal } = useQuery<{ total: number }>({
    queryKey: ['cards', 'search-count', debouncedQ],
    // countCardsTotal, NOT countCards: `countCards` from '../api' is the Claude Vision
    // card-count call re-exported from ./sessions — see the note on countCardsTotal.
    queryFn: () => countCardsTotal({ q: debouncedQ }),
    enabled: view === 'cards' && debouncedQ.length > 0,
  })

  // useInfiniteQuery, NOT a useQuery whose key contains the page count. Keying on the
  // page count makes every "Load more" a fresh cache entry: it re-requests every earlier
  // page (n(n+1)/2 requests to reach page n), keeps overlapping copies, and blanks the
  // grid while the new entry is empty. The page count belongs in the cache, not in state.
  // `debouncedQ` is in the key, so each search term owns its pages: page 2 of an old term
  // cannot leak into a new one, and there is no paging state to reset.
  const {
    data: searchData,
    isLoadingError: searchLoadError,
    isFetching: searchFetching,
    isFetchingNextPage: searchFetchingMore,
    isFetchNextPageError: searchMoreError,
    hasNextPage: searchHasMore,
    fetchNextPage: fetchMoreResults,
    refetch: refetchSearch,
  } = useInfiniteQuery({
    queryKey: ['cards', 'search', debouncedQ],
    queryFn: ({ pageParam }) =>
      listCards({ q: debouncedQ, limit: SEARCH_PAGE, offset: pageParam }),
    initialPageParam: 0,
    // The /count total stays the authority on whether more remain.
    getNextPageParam: (last, all) => {
      const loaded = all.reduce((n, page) => n + page.length, 0)
      // Known total: stop exactly at it. Unknown total (the count query is still in
      // flight, or failed): a full last page means more may exist, so keep offering the
      // next offset. Defaulting the total to 0 here would disable Load more entirely.
      const total = searchTotal?.total
      if (total !== undefined) return loaded < total ? loaded : undefined
      return last.length === SEARCH_PAGE ? loaded : undefined
    },
    enabled: view === 'cards' && debouncedQ.length > 0,
  })
  // No `?? []`: `undefined` means "no page yet" (in flight or paused), which the render
  // must keep distinct from "zero results". Typed `CardListItem[] | undefined`.
  const searchResults = searchData?.pages.flat()
```

New imports: `useInfiniteQuery` (beside `useQuery`), `countCardsTotal` from `../api`, `LoadMore`
(beside the existing `LoadError` from `../components/LoadMore`), and `useDebounced`.

**Delete** these, which the server now replaces (find them by name — line numbers drift):
- the temporary search-mode `cards` query (`queryKey: ['cards']`, `listCards({ limit: 200 })`)
- the `searchPersons` cross-search query (`queryKey: ['persons-search', debouncedQ]`)
- `searchLoadError` and `retrySearch`, which combine those two queries' errors — the new
  `searchLoadError` above is the infinite query's own `isLoadingError`
- the `matchingPersonIds` memo
- the `filteredCards` memo

- [ ] **Step 3: Render search results as a flat list**

In the cards-tab JSX, branch on `debouncedQ`:

```tsx
      {view === 'cards' && (debouncedQ ? (
        // Status order — see the rule at the top of this task.
        searchLoadError ? (
          // The first page failed and nothing is cached: an error, never "no results".
          // Retrying re-runs the whole query.
          <LoadError onRetry={() => refetchSearch()} isRetrying={searchFetching} />
        ) : searchResults === undefined ? (
          // No page has arrived yet — whether the request is in flight or paused.
          <div className="text-center text-gray-400 py-12">{t.loading}</div>
        ) : searchResults.length === 0 ? (
          <p className="text-center text-sm text-gray-400 py-12">{t.noResults(debouncedQ)}</p>
        ) : (
          <div className="space-y-2">
            {/* Only a known total is a result count. Falling back to the loaded length would
                read "50 results" while more exist, whenever /count is slow or failed. */}
            {searchTotal?.total !== undefined && (
              <p className="text-xs text-gray-500">{t.resultsN(searchTotal.total)}</p>
            )}
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
              {searchResults.map(card => <CardThumbnail key={card.id} card={card} />)}
            </div>
            {/* A later page failed: keep the results already shown, and swap the pager for
                an explicit error whose retry fetches just the missing page. */}
            {/* Only while another page may exist. LoadMore keeps its button whenever the total
                is unknown, but getNextPageParam already knows (known total, or a full last page),
                so without this gate a short last page leaves a button that does nothing.
                A failed next page leaves searchHasMore true, so its error stays reachable. */}
            {searchHasMore && (searchMoreError ? (
              <LoadError onRetry={() => fetchMoreResults()} isRetrying={searchFetchingMore} />
            ) : (
              <LoadMore
                loaded={searchResults.length}
                total={searchTotal?.total}
                // Only a next-page fetch makes the button busy. `isFetching` is also true
                // during a background refetch of page one, which is not "loading more".
                isLoading={searchFetchingMore}
                onLoadMore={() => fetchMoreResults()}
              />
            ))}
          </div>
        )
      ) : (
        /* the browse tree from Task 6 */
      ))}
```

- [ ] **Step 4: Build**

Run: `cd nxt-a1-meishi/frontend && npm run build`
Expected: no errors.

- [ ] **Step 5: Verify in the browser**

Hard-refresh, then:
1. Type `Rotary` → results appear as a flat grid with a count, **no year/month tree**.
2. Type quickly — the network tab shows one request after you stop, not one per keystroke.
3. **Search by a company name.** Results appear. This is impossible before this task.
4. **Search by a phone number fragment.** Results appear.
5. Search a term matching more than 50 cards → **Load more** appends the next 50 and disappears
   at the total.
6. Search `徐子恆` → his card appears, even though he is outside the newest 200.
7. Clear the box → the browse tree returns about 300 ms later. Year collapse is kept and the three
   newest months are open again, but months opened or closed **by hand** reset: the search branch
   unmounts the tree. That predates Task 7 (verified in the browser: a hand-opened May came back
   collapsed, a hand-closed September came back open), so it is not a regression.
8. If the scan/occasion plan has landed: search an occasion name → its cards appear.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/hooks/useDebounced.ts frontend/src/pages/CollectionPage.tsx frontend/src/i18n.ts
git commit -m "feat: server-side debounced paginated card search"
```

---

## Task 8: Persons tab pagination

> **Render loading / error / empty from data presence and `isLoadingError` — never from `isLoading` or `isSuccess`.**
> In TanStack Query v5 (installed: 5.95.2) `isLoading` is `isPending && isFetching`. A query can
> sit at `status: 'pending'` with `fetchStatus: 'paused'` — when a retry comes due while the tab
> is **hidden** (tab visibility, not window focus), or after the connection drops once the page
> has loaded (TanStack's `onlineManager` starts online and changes only on the browser's
> `online`/`offline` events). Then `isLoading` and `isError` are both false and `data` is
> undefined, so `isLoading ? … : isError ? … : data.length === 0 ? <Empty/>` shows the empty
> state — "Scan your first card" to a user with 205 cards. Separately, `status` stays `'error'`
> even when earlier data is cached, so gating on `!isSuccess` would hide loaded data behind a
> spinner after a failed background refetch. Order the branches:
> 1. `isLoadingError` (error **and** no data) → error UI with retry
> 2. `data === undefined` (pending — fetching or paused) → loading
> 3. data empty → empty state
> 4. otherwise the data (a failed background refetch keeps it on screen)
> 5. under the rows: a failed **later** page (`isFetchNextPageError`) →
>    `<LoadError onRetry={() => fetchNextPage()} isRetrying={isFetchingNextPage} />` in place of
>    the pager; otherwise `<LoadMore … isLoading={isFetchingNextPage} />` — and render either one
>    only while `hasNextPage`, because `LoadMore` keeps its button whenever the total is unknown. Without this a failed
>    page 2 shows nothing at all. Use `isFetchingNextPage`, not `isFetching`, which is also true
>    while page one refetches in the background.
>
> Take rows as `data?.pages.flat()` with **no `?? []`**, so step 2 can test `=== undefined`.
> Where several queries feed one view, the view has no data until **all** of them do. (The
> persons list has a single rows query; its `/count` total only sizes the pager, so it gates
> nothing — `LoadMore` treats an `undefined` total as "more may exist".)
> Caught in the browser during Task 6 verification: the code passed build, grep and review.
> `MonthSection.tsx` as committed is the reference implementation of all five steps.

**Files:**
- Modify: `frontend/src/pages/CollectionPage.tsx`

- [ ] **Step 1: Paginate the persons query**

Replace the `persons` query:

```tsx
  const PERSON_PAGE = 50

  // Declared BEFORE the infinite query, for the same reason as `searchTotal` in Task 7:
  // getNextPageParam reads it during render, so a later `const` would be in its TDZ.
  const { data: personTotal } = useQuery<{ total: number }>({
    queryKey: ['persons-count', debouncedQ],
    queryFn: () => countPersons(debouncedQ || undefined),
    enabled: view === 'persons',
  })

  // useInfiniteQuery for the same reason as the card search above.
  // The load-error / fetching / refetch names match the committed `persons` query, so the
  // persons-tab status ternary (`personsLoadError` → `persons === undefined` →
  // `persons.length === 0` → groups) keeps working unchanged.
  const {
    data: personsData,
    isLoadingError: personsLoadError,
    isFetching: personsFetching,
    isFetchingNextPage: personsFetchingMore,
    isFetchNextPageError: personsMoreError,
    hasNextPage: personsHasMore,
    fetchNextPage: fetchMorePersons,
    refetch: refetchPersons,
  } = useInfiniteQuery({
    queryKey: ['persons', debouncedQ],
    queryFn: ({ pageParam }) =>
      listPersons(debouncedQ || undefined, PERSON_PAGE, pageParam),
    initialPageParam: 0,
    getNextPageParam: (last, all) => {
      const loaded = all.reduce((n, page) => n + page.length, 0)
      // Same unknown-total rule as the card search above.
      const total = personTotal?.total
      if (total !== undefined) return loaded < total ? loaded : undefined
      return last.length === PERSON_PAGE ? loaded : undefined
    },
    enabled: view === 'persons',
  })
  // No `?? []`: `undefined` is "no page yet", which the status ternary tests for.
  // (The `persons ?? []` inside the personsByCountry and selectedPersons memos stays — those
  // only derive from whatever is loaded, they don't decide what state to render.)
  const persons = personsData?.pages.flat()
```

New import: `countPersons` from `../api` (`useInfiniteQuery` and `LoadMore` arrive in Task 7).

- [ ] **Step 2: Render the pager — or a failed-page error — under the persons list**

Inside the **rows branch** of the persons-tab status ternary — the `<div className="space-y-2">`
wrapping `personsByCountry.map(…)` — after the country groups. Not after the ternary: there the
pager would also render under the loading and error states.

```tsx
            {/* A later page failed: keep the persons already shown, and swap the pager for
                an explicit error whose retry fetches just the missing page. */}
            {/* Only while another page may exist. LoadMore keeps its button whenever the total
                is unknown, but getNextPageParam already knows (known total, or a full last page),
                so without this gate a short last page leaves a button that does nothing.
                A failed next page leaves personsHasMore true, so its error stays reachable. */}
            {personsHasMore && (personsMoreError ? (
              <LoadError onRetry={() => fetchMorePersons()} isRetrying={personsFetchingMore} />
            ) : (
              <LoadMore
                loaded={persons.length}
                total={personTotal?.total}
                // isFetchingNextPage, not isFetching — see the card search pager in Task 7.
                isLoading={personsFetchingMore}
                onLoadMore={() => fetchMorePersons()}
              />
            ))}
```

- [ ] **Step 3: Build**

Run: `cd nxt-a1-meishi/frontend && npm run build`
Expected: no errors.

- [ ] **Step 4: Verify in the browser**

Hard-refresh, switch to the **Persons** tab:
1. "Showing 50 of 202" appears with a Load more button. Before this task the tab stopped at 50
   with no indication.
2. Click Load more twice → all 202 persons are reachable and the button disappears.
3. Search a name → results are bounded and the count reflects the filtered total.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/CollectionPage.tsx
git commit -m "feat: paginate the persons tab instead of silently stopping at 50"
```

---

## Final verification

- [ ] **Full backend suite**

Run: `cd nxt-a1-meishi && venv/bin/python3 -m pytest tests/ -q`
Expected: all pass, including the `test_query_counts.py` N+1 guard.

- [ ] **Clean build**

Run: `cd nxt-a1-meishi/frontend && npm run build && npm run lint`
Expected: no errors.

- [ ] **The acceptance check**

Redeploy the backend, hard-refresh, and confirm on real data:

1. **All 205 cards are reachable** — year headers sum to 205, and the five 2026-03/04 cards
   (徐子恆, 張雅純, 解沛誼, 楊嘉明, 何鈞軒) are visible.
2. Searching a **company name** returns cards. Searching a **phone fragment** returns cards.
3. The Persons tab reaches all 202 people.
4. `curl -s localhost:8000/api/v2/cards/facets | python3 -c "import json,sys;print(sum(f['count'] for f in json.load(sys.stdin)))"` → `205`.

- [ ] **Update project memory**

Record: the browse/search mode split; that facets and the `?month=` filter must both bucket on
`received_date ?? created_at`; that `/facets` and `/count` must be declared before the
`/{ext_id}` catch-all in both routers; and that `test_query_counts.py` exists to catch N+1
regressions.

---

## Self-review notes

**Spec coverage** — §4.1→Task 1, §4.2→Task 2, §4.3→Task 2, §4.4→Task 3, §4.5→Task 4,
§4.6→Task 5, §5.1→Task 6, §5.2→Task 7, §5.3→Task 8, §5.4→Task 6 Step 2, §6 testing→each
task's test and browser steps, §7 amendment→**already applied** to the scan/occasion spec in
commit `18adc13`, so no task is needed.

**Verified rather than assumed** — two claims this plan rests on were checked against the real
models before writing: bucketing on `func.coalesce(received_date, created_at)` groups and orders
correctly and yields Python `int`. (A claim in an earlier draft — that `extract` returns a float
on SQLite — was disproved during Task 2 and corrected;
and `session_maker.kw["bind"]` returns the `AsyncEngine`, so the query-counter test can reach
`.sync_engine`.

**Naming consistency** — `_apply_card_filters` (Task 1) is used by name in Tasks 2 and 4;
`CardFacet`/`CountOut` (Task 2) are the schema names used in Task 6's TS type and API helpers;
`_person_ids_matching` (Task 4) is shared by `list_persons` and `count_persons`;
`LoadMore` takes `{loaded, total, onLoadMore, isLoading}` at all three call sites (Tasks 6, 7, 8);
`useDebounced` is introduced in Task 7 but referenced by Task 6, which is why Task 6 Step 5
defines `const debouncedQ = q` as a temporary stand-in so it compiles standalone.

**Deliberate ordering** — Tasks 1-5 ship no UI change and are independently verifiable against
live data. Task 6 is the one that makes the five hidden cards reappear.
