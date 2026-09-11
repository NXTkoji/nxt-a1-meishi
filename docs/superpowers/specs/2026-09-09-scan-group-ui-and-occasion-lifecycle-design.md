# Scan → Group UI overhaul, and Occasion lifecycle

**Date:** 2026-09-09
**Status:** Approved for planning
**Scope:** `frontend/src/pages/ScanPage.tsx`, `frontend/src/components/{DropZone,CardOutlineSelector}.tsx`,
`frontend/src/pages/SettingsPage.tsx`, `frontend/src/i18n.ts`, `app/routers/v2/{cards,occasions}.py`,
`app/schemas/api.py`, `app/db/models.py`, `app/services/legacy_card.py`, one Alembic migration.

---

## 1. Why

Two unrelated problems, batched because both surfaced in the same review.

**The Group stage is unguided.** A new user (and the Community Edition will have them) is shown
three grouping buttons with no way to know which applies, icon buttons too small to hit reliably,
and a `Start analysis` button that silently discards any image left ungrouped. Rotation appears
not to work. The outline screen gives no instructions at all.

**Occasions accumulate and deleting one loses data.** The live database
(`~/.nxt-a1/meishi.db`) holds 21 occasions created between March and July 2026 — roughly
50 per year — presented in a flat `<select>`. 20 of the 21 have cards attached; `RI Convention`
alone has 48. Deleting an occasion silently nulls `cards.occasion_id`, and the occasion name is
not recoverable or searchable afterwards.

## 2. Non-goals

- No occasion **filter UI** on the Collection page; a clickable occasion chip is a separate
  feature. (An earlier draft justified this with "text search covers the need". That was
  wrong: the Collection page filters client-side over the newest 200 cards and never sends
  `?q=` at all, so §6.3 alone does not make occasions findable there. Closing that is the
  job of `2026-09-09-collection-scalability-design.md` §5.2. The conclusion — no occasion
  filter UI — is unchanged.)
- No change to the corner-detection CV pipeline, to analysis, review, or export.
- No change to how `event_date` is captured. It stays optional and mostly unset; grouping
  works around that (§6.2).
- No new frontend test runner. `frontend/` has no test harness and this spec does not add one.

---

## 3. Upload — drop target

**Problem.** `DropZone` is `min-h-[360px]` ([DropZone.tsx:33]). Dragging files from another
window onto a 360px strip is fiddly.

**Change.** `DropZone` gains a `compact?: boolean` prop.

| State | Height | Rationale |
|---|---|---|
| No images uploaded yet | `min-h-[720px]` | The whole job at this stage is hitting the target. Double, as requested. |
| One or more images uploaded (`compact`) | `min-h-[200px]` | Still larger than today's 360px is *useful*, but does not push card groups off-screen. |

`ScanPage` renders `<DropZone compact={ungrouped.length > 0 || groups.length > 0} />`.
(Note `separated` is a *subset* of `ungrouped` per §4.2 — it must not be added to it.)

**Acceptance:** with an empty session the drop zone fills the viewport; after the first upload it
shrinks and the Ungrouped row is visible without scrolling.

---

## 4. Group stage — layout and copy

### 4.1 Instruction panel

New collapsible panel, rendered above the Ungrouped row whenever `stage === 'grouping'`.
Open by default; collapsed state persisted in `localStorage` under `scan.help.collapsed` so a
returning user is not re-lectured.

Content (i18n, all three languages):

> **How this works**
> ✂️ **Split** — for a photo holding several cards, cut it into one image per card.
> Every multi-card photo must be split before analysis.
> ↺ ↻ **Rotate** — turn an image upright so the text reads left to right.

### 4.2 Two rows

`ungrouped` is partitioned for display only, by filename. No new state.

```ts
// Images produced by the scissors carry a _cardN suffix (see getCardPos).
const separated = ungrouped.filter(i => getCardPos(i.image_filename) !== null)
const unsplit   = ungrouped.filter(i => getCardPos(i.image_filename) === null)
```

Render order: **Ungrouped** (whole photos), then **Separated cards**, then **Card groups**.
A row with no images is not rendered.

### 4.3 Grouping buttons move to the row they apply to

This is the streamlining. The user is never asked to choose between two strategies whose
preconditions they cannot see.

| Row | Buttons | Note |
|---|---|---|
| Ungrouped | `1 per card (single-sided)`, `Pairs of 2 (double-sided)` | `Pair by position` is impossible here — these files have no `_cardN`. |
| Separated cards | **`Pair by position`** (primary), `1 per card (single-sided)` | Matches the confirmed workflow: shoot all fronts in one photo, flip in place, shoot all backs. |

`Pair by position` is the primary (blue) button on the Separated row, with a one-line subtitle:
*"Card #1 of each photo pairs with card #1 of the next photo, as front and back."*

`autoGroup1`, `autoGroup2` and `autoPairByPosition` are parameterised to take an
`images: SessionImage[]` argument instead of closing over the whole `ungrouped` array.
Behaviour is otherwise unchanged, including `autoPairByPosition`'s handling of a lone trailing
prefix (each of its positions becomes a single-sided card) — which is what makes
`Pair by position` correct for a fronts-only batch too.

`hasMixedCropState` keeps its warning but is reworded to point at the row:
*"Some photos in Ungrouped have not been split yet. Split them before pairing by position,
or the pairing will be wrong."*

### 4.4 Readiness gate

**Problem.** Two behaviours silently lose work:

1. `Start analysis` is enabled when `groups.some(g => g.images.length > 0)`. Any image still
   sitting in Ungrouped or Separated is dropped from the analysis without warning.
2. The `Start analysis` shortcut in the Ungrouped header ([ScanPage.tsx:713]) runs
   `autoGroup1()` first, silently making the whole batch single-sided.

**Change.**

- **Remove** the shortcut button at [ScanPage.tsx:713]. Grouping becomes an explicit act.
- `Start analysis` is **disabled** while `ungrouped.length > 0`, with the reason rendered
  beside it rather than hidden in a tooltip:

  > ⚠ **2 images are not in a card group yet.** Split any photo holding more than one card,
  > then use the grouping buttons above.

- It stays disabled if every group is empty, as today.

Both `Start analysis` buttons (header and footer of the Card groups section) share one
`analysisBlockedReason` value so they cannot disagree.

### 4.5 Tips

The existing tip line ([ScanPage.tsx:807]) is moved into the Card groups section header area and
extended to three lines, shown whenever `stage === 'grouping'` and `groups.length > 0`:

- Drag an image from one card into another to pair them as front and back.
- **The side showing the name and contact details should be the Front.** Use ⇅ Swap to change it.
- **When every card is positioned correctly, press Start analysis.**

### 4.6 Icon size and layout stability

**Problem A — icons too small.** Split/rotate/crop buttons are `text-xs px-1.5 py-0.5`,
roughly 18px. Below any reasonable target size.

**Problem B — the rotate button moves.** Images render `h-24 w-auto` (Ungrouped) and
`h-28 w-auto` (group). Rotating 90° swaps the aspect ratio, so the tile's width changes, the
button row below it re-lays out, and every sibling after it shifts. Clicking ↻ twice in a row
requires re-aiming.

**Change.** Both rows use a **fixed `w-32 h-32` tile** with `object-contain`. A landscape card
fills the width, a portrait card fills the height; the tile's footprint never changes, so nothing
below or after it moves. Icon buttons become `h-9 min-w-9 text-lg` (~36px).

**Acceptance:** clicking ↻ four times in succession without moving the mouse rotates the image
four times.

---

## 5. Card outline screen

`CardOutlineSelector` is currently hardcoded English while the rest of the app is ja/en/zh-TW.
Since this section adds user-facing copy, the whole component is moved onto `useLang()` —
roughly a dozen strings × 3 languages, including the existing `Cancel`, `Undo`, `Crop N cards`,
`Detecting corners…`, `That card is already outlined`, `Corner detection failed — try again`.

New copy:

| Position | Text |
|---|---|
| Sub-header, tap mode | Click the centre of a card once — its 4 corners are found automatically. Then drag any corner to adjust. |
| `Undo` button `title` | Removes the last card you outlined. |
| Footer, before any outline | Click the centre of each business card. |
| Footer, once ≥1 outlined | Above the green button: *When every card is outlined, press the green button below.* |

An amber (`confidence === 0`) outline already renders dashed with a ⚠ marker; the sub-header
gains a conditional line when any amber polygon exists: *"A dashed amber outline means the
corners are a guess — drag them onto the card edges."*

No behavioural change. The `cornerDragRef` fix and touch handling are untouched.

---

## 6. Occasions

### 6.1 Preserve the label on delete

**Current behaviour (verified empirically, not inferred):** `db.delete(occ)` causes SQLAlchemy
to load `Occasion.cards` and set each `occasion_id` to `NULL`. Cards survive; the occasion name
is gone.

**Change.** New nullable column:

```python
# app/db/models.py — Card
# Plain-text occasion name, stamped in when the linked Occasion is deleted so the
# card keeps a human-readable record of where it came from. Only consulted when
# occasion_id is NULL — a live link always wins, so renames still propagate.
occasion_label: Mapped[Optional[str]] = mapped_column(String(256))
```

`DELETE /api/v2/occasions/{id}` stamps before deleting:

```python
await db.execute(
    update(Card)
    .where(Card.occasion_id == occasion_id)
    .values(occasion_label=occ.name)
)
await db.delete(occ)
```

Deleting an occasion **always** overwrites `occasion_label`, even if the card already
carried a label from an earlier deletion — the label should track the most recently
deleted occasion, not the first one. A card's label is also cleared whenever its
`occasion_id` changes (re-linked to another occasion, or explicitly unlinked via
`PATCH .../cards/{id}` with `occasion_id` set to a new id or to `null`) — see
`app/routers/v2/cards.py`'s `update_card`. Leaving `occasion_id` out of a PATCH body
entirely leaves the label untouched.

**Resolution rule, applied everywhere an occasion name is displayed or exported:**

1. `occasion_id` set → the live `Occasion.name` (renames propagate)
2. else `occasion_label` set → that text
3. else empty

[legacy_card.py:110] already derives an `occasion_name` field for the Google Contacts sync;
it becomes:

```python
occasion_name = db_card.occasion.name if db_card.occasion else (db_card.occasion_label or "")
```

so exported contacts keep their occasion after a delete. `occasion_location` has no snapshot
and correctly becomes empty — location is a property of the occasion, not of the card.

**Migration** `f2a3b4c5d6e7_add_occasion_label_to_cards`, `down_revision = 'e1f2a3b4c5d6'`.
Adds one nullable column. No backfill: existing cards all have live links.

**Confirm dialog** gains the count and states the consequence plainly:

> Delete "RI Convention"?
> **48 cards use this occasion.** They will keep "RI Convention" as a plain text label,
> but will no longer be linked to an occasion you can rename or filter by.

`OccasionOut` gains `card_count: int`, computed in `list_occasions` with a correlated scalar
subquery over `cards` where `deleted_at IS NULL`.

### 6.2 Group the occasion lists by year and month

**Constraint discovered in the data:** only 1 of 21 occasions has `event_date` set, because both
creation paths call `createOccasion({ name })` with no date
([ScanPage.tsx:1004], [SettingsPage.tsx:284]). Grouping on `event_date` alone would put 20 of 21
into an "undated" bucket.

**Grouping key is therefore `event_date ?? created_at`.** In the live data `created_at` tracks
the real event well (`3481地區年會` → 2026-04-20, `RI Convention` → 2026-06-14). A shared helper:

```ts
// frontend/src/lib/occasionGrouping.ts
// event_date is optional and rarely set in practice, so fall back to created_at,
// which in practice tracks when the event was scanned.
export function occasionPeriod(o: Occasion): { year: number; month: number }
export function groupOccasionsByMonth(os: Occasion[]): { year, month, occasions }[]  // newest first
```

Used in both places:

- **Scan picker** (`OccasionPicker`) — one `<optgroup>` per month, replacing today's
  "Recent (3) / All" split. Label format `2026年7月` / `2026-07` / `2026年7月`
  per language. `なし` stays first.
- **Settings** — collapsible year → month tree matching the idiom already in
  [CollectionPage.tsx:223], with each occasion's `card_count` shown. **The current year is
  expanded; earlier years are collapsed by default**, so the list stays roughly one screen
  regardless of how many years accumulate.

Rows within a month keep their existing ordering (`event_date desc, created_at desc`).

### 6.3 Make occasions searchable

**Current behaviour:** `q` searches person names, contact values, position titles/departments
and organisation names ([cards.py:112]). Occasion names are searched **nowhere** — an occasion is
reachable only via the exact `occasion_id` filter, which the Collection page never sends. So
even a live occasion is not findable by typing its name.

Without this piece, §6.1's stamped label would be visible but unfindable.

**Change.** Add a fourth branch to the `q` `or_()`:

```python
# Occasion — matches the linked occasion's name, or (only when the card has no
# live occasion) the label stamped on it by an earlier deletion. Once a card is
# re-linked to a live occasion, its stale label must stop matching — otherwise a
# search could surface a card under a name it no longer carries anywhere visible.
occasion_clause = or_(
    exists(
        select(Occasion.id).where(
            Occasion.id == Card.occasion_id,
            Occasion.name.ilike(like),
        )
    ),
    and_(Card.occasion_id.is_(None), Card.occasion_label.ilike(like)),
)
```

The label is a snapshot, not a live value. Through the API a card never holds a live
`occasion_id` and a label at once — a PATCH that sets `occasion_id` clears the label
(see §6.1). The `occasion_id IS NULL` guard protects rows written before that rule.

**Placeholder copy.** `searchPlaceholder` ([i18n.ts:72]) already understates search — it says
"by name" while covering company and phone number. Widened:

| Lang | New value |
|---|---|
| ja | `名前・会社・場面で検索…` |
| en | `Search name, company, occasion…` |
| zh-TW | `搜尋姓名、公司、場合…` |

---

## 7. i18n

Every new string goes in all three blocks of `i18n.ts`. `tsc -b` enforces key parity across
`ja` / `en` / `zh-TW` — a missing key is a compile error, so parity does not need a separate check.

New key groups: `scanHelp*` (§4.1), `pairByPosHint` (§4.3), `analysisBlocked*` (§4.4),
`tipFrontSide` / `tipStartAnalysis` (§4.5), `outline*` (§5, including the twelve strings being
migrated off hardcoded English), `occasionDeleteWarn(name, n)` (§6.1), `monthLabel(y, m)` (§6.2).

---

## 8. Testing

**Backend — real data-backed tests** using the existing `client_with_test_db` fixture, which
runs against a throwaway SQLite file (`tests/conftest.py`). Not the signature-only smoke style
of `test_cards_filter.py`; the delete path has 48 real cards riding on it.

`tests/test_occasion_lifecycle.py`:

| Test | Asserts |
|---|---|
| `test_delete_stamps_label_onto_cards` | after `DELETE`, each card has `occasion_id IS NULL` and `occasion_label == "<name>"` |
| `test_delete_does_not_delete_cards` | card count unchanged, cards still readable |
| `test_delete_overwrites_older_label` | a card already carrying an older label ends up with the *just-deleted* occasion's name, not the older one |
| `test_delete_unknown_occasion_returns_404` | deleting a nonexistent occasion id returns 404 |
| `test_list_returns_card_count` | `card_count` correct, and excludes soft-deleted cards |
| `test_card_count_zero_for_unused_occasion` | a brand-new occasion reports `card_count == 0` from both create and list |
| `test_patch_rename_occasion_reports_correct_card_count` | renaming an occasion with cards returns the real `card_count`, not 0 |
| `test_search_matches_linked_occasion` | `?q=Convention` returns cards whose live occasion matches |
| `test_search_matches_orphaned_label` | same query returns the same cards after the occasion is deleted |
| `test_search_ignores_stale_label_on_relinked_card` | a card's stale label does not match `?q=` once the card has a live occasion again |
| `test_patch_occasion_id_clears_stale_label` | PATCH with a new `occasion_id` clears the card's old label |
| `test_patch_occasion_id_null_clears_stale_label` | PATCH with `occasion_id: null` also clears the label |
| `test_patch_without_occasion_id_keeps_label` | a PATCH that omits `occasion_id` leaves the label untouched |
| `test_card_detail_exposes_label_after_delete` | `GET /cards/{ext_id}` has `occasion_label` null while linked, and the occasion's name after it is deleted |
| `test_legacy_card_falls_back_to_label` | `occasion_name` in the DTO survives deletion |

Re-linking a card to a different occasion, or explicitly unlinking it, clears its
`occasion_label` — see §6.1's PATCH rule. A stale label is dead weight once the card
has (or explicitly loses) a live link, so it is wiped rather than left to resurface later.

**Frontend.** No test runner exists and this spec does not add one. Verification is
`cd frontend && npm run build` (which runs `tsc -b`) plus browser verification of the
stateful paths, per the project's standing rule that a `tsc` pass is not evidence that
stateful UI works. Browser checks:

1. Rotate an image in the Ungrouped row → run `Pair by position` → **the grouped card shows
   the rotated image.** This is the §9 bug and cannot be caught by `tsc`.
2. Click ↻ four times without moving the mouse → four rotations.
3. Leave one image ungrouped → `Start analysis` disabled, reason shown.
4. Split a photo → its crops appear in **Separated cards**, not Ungrouped.
5. Delete an occasion with cards → dialog shows the count; afterwards the card still displays
   the name and is still returned by search.

Remember: after `npm run build`, hard-refresh (Cmd+Shift+R) — the bundle is content-hashed and
the browser will otherwise serve the old one. Backend changes need the LaunchAgent reloaded
(`launchctl unload` then `load`).

---

## 9. Bug: rotation not visible after grouping

**Reported as:** "even though I have rotated the card during scissoring, after `Pair by position`
the card image is not rotated — the app seems to know it is rotated but the display is not."

**Confirmed as a display-only bug.** The backend rotates the file **in place** on disk
([sessions.py:248]), so the stored image is genuinely rotated and analysis sees the rotated
version. The display is stale because there are **two independent cache-bust maps**:

| Owner | State | Bumped by |
|---|---|---|
| `ScanPage` | `imgCacheBust` ([ScanPage.tsx:467]) | rotations in the **Ungrouped** row |
| `CardGroupCard` | `localCacheBust` ([ScanPage.tsx:1107]), starts `{}` | rotations **inside that group** |

`CardGroupCard` is never given `imgCacheBust`. When an image that was rotated while ungrouped
moves into a group, its `<img src>` is emitted with **no `?t=` parameter**, so the browser serves
its cached pre-rotation copy.

**Fix.** Delete `localCacheBust`. Pass `ScanPage`'s `imgCacheBust` down as a prop and use it for
every image URL. `onRotateImage` already bumps it ([ScanPage.tsx:467]), so the group's own
rotate buttons keep working through the same single source of truth.
`LightboxImage` forwards `src` to both the thumbnail and the full-screen view, so the fix
propagates to the lightbox for free.

---

## 10. Order of work

1. Backend: migration, `occasion_label`, delete-stamps-label, `card_count`, search branch,
   `legacy_card` fallback + tests. Self-contained and independently verifiable.
2. §9 cache-bust fix. One-line-ish, unblocks confident manual testing of everything else.
3. §4.6 tile sizing and icon size.
4. §4.2/4.3 row split and per-row buttons.
5. §4.1/4.4/4.5 copy, readiness gate, tips.
6. §5 outline screen i18n + copy.
7. §3 drop zone.
8. §6.2 occasion grouping in both pickers.

Steps 1–2 are the correctness work; 3–8 are presentation and can be reviewed together.
