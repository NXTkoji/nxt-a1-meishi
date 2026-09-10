# Collection scalability

**Date:** 2026-09-09
**Status:** Approved for planning
**Scope:** `app/routers/v2/cards.py`, `app/routers/v2/persons.py`, `app/schemas/api.py`,
one Alembic migration, `frontend/src/pages/CollectionPage.tsx`, `frontend/src/api/index.ts`,
`frontend/src/i18n.ts`.

**Related:** `2026-09-09-scan-group-ui-and-occasion-lifecycle-design.md` — that spec's §6.3
adds occasion matching to the server-side `?q=`. This spec is what makes it reachable from
the UI. The two are independent; either can ship first.

---

## 1. Why

The Collection page fetches a fixed **200** cards and does everything else in the browser.
The database holds **205 live cards**. Five are already invisible, silently — no error, no
"showing 200 of 205", they are simply absent:

```
id 5  2026-04-04  何鈞軒
id 4  2026-03-31  楊嘉明
id 3  2026-03-31  解沛誼
id 2  2026-03-30  張雅純
id 1  2026-03-30  徐子恆
```

Verified against the live API: `limit=200` returns 200 rows, `limit=500` returns 205.

Four separate problems sit behind that:

**The page never searches the server.** It fetches 200 cards with no `q`, then filters them
in the browser on `person_name` alone (`CollectionPage.tsx:58-66`), plus a workaround that
cross-references persons matching `q`. So you cannot find a card by company, job title or
phone number from the Collection page even though the API supports all three — and you cannot
find anything outside the newest 200 at all.

**Card listing has an N+1.** `_get_name` runs one or two queries per card inside the result
loop (`cards.py:182-198`). 60ms at 205 cards; 10,000–20,000 sequential queries at 10k.

**Persons search is unbounded and unordered.** `list_persons` applies `.limit(50)` when
browsing, but the `if q:` branch (`persons.py:135-150`) builds
`select(Person).where(Person.id.in_(all_ids))` with **no limit, offset or order_by**. It also
runs two queries per person (name, country). Searching a common letter walks the whole table.
Browsing, meanwhile, silently truncates at 50 — against 202 persons.

**No indexes exist.** Not one user-created index on any table.

## 2. Non-goals

- No full-text search engine (FTS5, external index). `ilike` with a batched query plan and
  the indexes below is sufficient at the scale this app will realistically reach.
- No change to what `?q=` matches. That set is defined by the other spec's §6.3.
- No redesign of the card thumbnail, the Persons tab's country grouping, or the Export page
  (which already passes `limit: 500` and a server-side `q`, and is correct).
- No infinite scroll. Explicit "Load more" — simpler, no scroll-restoration bugs.

## 3. The grouping rule

Both the client (`CollectionPage.tsx:70`) and the server's `year`/`month` filters
(`cards.py:78-99`) bucket a card by **`received_date`, falling back to `created_at` when it
is null**.

Every piece of this spec must use exactly that rule. If the facets endpoint and the month
filter disagree, a month's header count will not match the number of cards that appear when
you expand it — the most visible possible bug in this design.

---

## 4. Backend

### 4.1 One shared filter builder

`list_cards` currently builds its `where` clauses inline. Extract them so list, count and
facets cannot drift apart:

```python
def _apply_card_filters(stmt, *, person_id, occasion_id, my_company_id, q,
                        year, month, date, not_exported):
    """Apply the shared Collection/Export filter set to a Card select.

    The single owner of the received_date-else-created_at bucketing rule (§3) and of
    what ?q= matches. list_cards, count_cards and card_facets all route through here.
    """
```

Behaviour is unchanged — this is a pure extraction, verified by the existing filter tests
continuing to pass.

### 4.2 `GET /api/v2/cards/facets`

```json
[ { "year": 2026, "month": 9, "count": 12 },
  { "year": 2026, "month": 8, "count": 31 } ]
```

Newest first. One `GROUP BY` over the §3 expression, with the same filters applied. This is
what makes the tree complete at any collection size: the response is a few hundred bytes
whether you hold 200 cards or 200,000.

**Route ordering matters.** `cards.py` already declares `GET /{card_ext_id}`, and
`persons.py` declares `GET /{person_ext_id}`. FastAPI matches in declaration order, so
`/facets` and `/count` must be declared **before** those catch-alls in both routers, or they
will be captured as an external ID. A 404 or a validation error on `/api/v2/cards/facets` is
this mistake.

### 4.3 `GET /api/v2/cards/count`

```json
{ "total": 205 }
```

Same filters. Drives the "N results" label and tells Load-more when to stop.

A dedicated endpoint rather than an `X-Total-Count` header because `client.ts:23` returns
`res.json()` and discards headers; adding header plumbing would touch every call site.

### 4.4 Batch the card-name lookup

Replace the per-card `_get_name` with one query for every returned `person_id`, resolving the
`display_name_language` preference in Python:

```python
# One query for every name we might need, then resolve preference in memory.
# Was 1-2 queries per card inside the result loop.
name_rows = (await db.execute(
    select(PersonName.person_id, PersonName.language, PersonName.full_name)
    .where(PersonName.person_id.in_(person_ids), PersonName.is_current == True)
    .order_by(PersonName.person_id, PersonName.id)
)).all()
```

Preference rule, unchanged from `_get_name`: the first name whose `language` starts with the
card's `display_name_language`, else the lowest-`id` current name.

### 4.5 Fix `list_persons`

- Batch the name and country lookups the same way (one query each, not 2×N).
- Give the `if q:` branch the `order_by` / `limit` / `offset` the browse branch already has,
  so search is bounded and deterministic. Order both branches by `Person.created_at.desc()`.
- Raise the cap: `limit: int = Query(50, le=500)`.
- Add `GET /api/v2/persons/count` with the same `q`, for the Load-more stop condition.

### 4.6 Indexes

Migration `a3b4c5d6e7f8_add_collection_indexes`, chaining onto whatever is head at
implementation time.

| Table | Column | Why |
|---|---|---|
| `cards` | `person_id` | every list row joins to a person |
| `cards` | `occasion_id` | occasion filter + the other spec's search branch |
| `cards` | `deleted_at` | on every query as `IS NULL` |
| `cards` | `(deleted_at, coalesce(received_date, created_at) DESC, id DESC)` | the hot path: default list ordering and facets bucketing. Single-column indexes on `created_at` or `received_date` serve nothing — every date path is the `coalesce` expression — and the index must lead with `deleted_at` or the planner never picks it. |
| `person_names` | `person_id` | name batch lookup and `q` |
| `contact_details` | `person_id` | country lookup and `q` |
| `positions` | `person_id` | title/org search |
| `card_sync_history` | `card_id` | `not_exported` and the synced-destinations map |

SQLite does not index foreign keys automatically. At 205 rows none of this is measurable;
the point is that the ceiling this spec removes is not immediately replaced by a slower one.

---

## 5. Frontend — Collection page

The page gains an explicit mode. `q` empty → **browse**; `q` present → **search**.

### 5.1 Browse mode

The facets query builds the year → month tree. The **3 most recent months** are expanded and
fetched on load; every older month is collapsed and fetches only when clicked.

```
▼ 2026
  ▼ 2026/09 (12)   ← fetched on load
  ▼ 2026/08 (31)   ← fetched on load
  ▼ 2026/07 (24)   ← fetched on load
  ▶ 2026/06 (48)   ← fetches on click
  ▶ 2026/05 (19)
▶ 2025 (163)
```

Each month is its own query key — `['cards', 'month', '2026-09']` — so months cache, refetch
and invalidate independently. A month fetch is
`GET /api/v2/cards?month=YYYY-MM&limit=500&offset=N`.

**A month is paginated too.** 500 is the API maximum, so a month holding more would truncate —
reintroducing the exact silent-data-loss bug this spec exists to remove. Because the facet
already tells us the month's true count, the month renders **Load more** whenever
`loaded < count`, reusing the same pagination the search mode uses. Rare today; correct at any
scale, and it costs one shared component.

Counts in the headers come from facets, so they are correct even for months never expanded —
and they are the authority that tells a month whether it is fully loaded.

### 5.2 Search mode

- 300ms debounce on the input.
- `GET /api/v2/cards?q=…&limit=50&offset=N` — a flat list, newest first.
- Header shows the total from `/cards/count`; **Load more** appends the next 50 and is hidden
  once `loaded >= total`.
- **No year/month tree.** Grouping a filtered set produces many one-card months and makes
  Load-more awkward. A flat ranked list is the right shape for results.

This **deletes** the client-side `filteredCards` filter and the `searchPersons` /
`matchingPersonIds` cross-search workaround (`CollectionPage.tsx:47-66`). The server already
matches names, organisations, titles and contact values — and occasions, once the other
spec's §6.3 lands.

### 5.3 Persons tab

Same shape: server-side `q` (already the case), 50 per page, Load more against
`/api/v2/persons/count`. The existing country grouping is preserved and applies to whatever
is currently loaded.

### 5.4 New copy

| Key | en |
|---|---|
| `resultsN` | `${n} results` |
| `loadMore` | `Load more` |
| `showingNofM` | `Showing ${n} of ${m}` |
| `noResults` | `No cards match "${q}"` |

Japanese and Traditional Chinese equivalents in the same commit; `tsc -b` enforces key parity.

---

## 6. Testing

**Backend**, using the existing `client_with_test_db` fixture — data-backed, not signature
smoke tests. `tests/test_collection_scalability.py`:

| Test | Asserts |
|---|---|
| `test_facets_groups_by_received_date` | a card with `received_date` buckets by it, not `created_at` |
| `test_facets_falls_back_to_created_at` | a card with null `received_date` buckets by `created_at` |
| `test_facets_counts_match_month_query` | for every facet, `?month=` returns exactly `count` cards — the §3 consistency guarantee |
| `test_facets_excludes_soft_deleted` | soft-deleted cards are absent from counts |
| `test_facets_respects_q` | facets narrow when `q` is passed |
| `test_count_matches_list_length` | `/cards/count` equals an unpaginated list length |
| `test_list_pagination_is_stable` | offset 0+50 and offset 50+50 do not overlap or skip |
| `test_month_query_paginates` | a month with more rows than `limit` returns the rest via `offset`, with no overlap |
| `test_name_batch_matches_old_behaviour` | `display_name_language` preference still honoured, incl. the fallback |
| `test_persons_search_is_bounded` | a `q` matching >50 persons returns `limit`, not everything |
| `test_persons_search_is_ordered` | two identical requests return the same order |

**Performance guard.** One test seeds 500 cards and asserts the card list issues a bounded
number of queries (via a SQLAlchemy `before_cursor_execute` counter) rather than one per card.
This is the regression that would otherwise creep back silently.

**Frontend.** No test runner exists; this spec does not add one. `npm run build` (runs
`tsc -b`) plus browser verification:

1. Collection loads → 3 most recent months expanded with thumbnails, older months collapsed
   with correct counts.
2. **All 205 cards are reachable** — expand every month, including 2026/03, and confirm
   徐子恆 and 張雅純 appear. They are invisible today.
3. Expand a collapsed month → its cards load, and the count in the header matched already.
4. Search `Rotary` → flat results, count shown, no year/month tree.
5. Search a term matching >50 cards → Load more appends and then disappears at the total.
6. Search by **company name** → returns cards. Impossible today.
7. Persons tab → more than 50 persons reachable via Load more.

---

## 7. Amendment to the Scan/Occasion spec

`2026-09-09-scan-group-ui-and-occasion-lifecycle-design.md` §6.3 closes with:

> Since the Collection page has no occasion filter UI at all, clicking an occasion name on a
> card is the natural way to see its siblings. That's a genuine new feature rather than a fix,
> so I'm leaving it out unless you want it — text search now covers the need.

"Text search now covers the need" assumed Collection search reaches the server. It does not.
That paragraph is replaced with a pointer to this spec: occasion search becomes reachable from
the Collection page when §5.2 lands. The conclusion — no occasion filter UI — is unchanged.

---

## 8. Order of work

1. `_apply_card_filters` extraction (§4.1) — pure refactor, existing tests must still pass.
2. `/cards/facets` and `/cards/count` (§4.2, §4.3) + tests.
3. Batch the card-name lookup (§4.4) + the performance guard.
4. `list_persons` fixes and `/persons/count` (§4.5) + tests.
5. Indexes (§4.6).
6. Browse mode: facets tree with lazy months (§5.1).
7. Search mode: server-side, debounced, paginated (§5.2).
8. Persons tab pagination (§5.3).
9. Spec amendment (§7).

Steps 1–5 are independently verifiable and ship no UI change. Step 6 is the one that makes
the five missing cards reappear.
