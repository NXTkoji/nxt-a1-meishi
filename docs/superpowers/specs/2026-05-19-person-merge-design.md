# Person Merge Design

**Date:** 2026-05-19  
**Status:** Approved

## Problem

Scanning the same person's JP and TW business cards creates two separate Person records. Users need a way to consolidate N Person records into one, preserving all card images and contact data from every source.

## Decisions

- **Entry point:** CollectionPage persons view, multi-select → action bar
- **Conflict resolution:** Additive merge (union all data); count but do not block on duplicates
- **Primary record:** User picks explicitly before confirming
- **Supported N:** Any number ≥ 2

---

## Backend

### New endpoint

```
POST /api/v2/persons/{primary_ext_id}/merge
```

**Request body:**
```json
{ "source_ids": ["<ext_id>", "<ext_id>", ...] }
```
`source_ids` must contain ≥ 1 entry. If `primary_ext_id` appears in `source_ids`, it is silently ignored.

**What it does (single transaction):**

1. Resolve all source Person rows; 404 if any are missing.
2. For each source person:
   - `UPDATE cards SET person_id = primary_id WHERE person_id = source_id`
   - `UPDATE person_names SET person_id = primary_id WHERE person_id = source_id`
   - `UPDATE contact_details SET person_id = primary_id WHERE person_id = source_id`
   - `UPDATE positions SET person_id = primary_id WHERE person_id = source_id`
   - Append `source.notes` to `primary.notes` (newline-separated) if source notes are non-empty
3. Count duplicate contact details on the merged primary: rows sharing the same `(detail_type, lower(trim(value)))`.
4. DELETE each source Person (cascade handles `PersonRelationship`; cards/names/etc. are already reassigned).
5. Return `MergeResult { person: PersonOut, duplicate_contact_count: int }`.

**Location:** New handler added to `app/routers/v2/persons.py`.

**Schema additions** (`app/schemas/api.py`):
```python
class MergeRequest(BaseModel):
    source_ids: List[str]  # external_ids

class MergeResult(BaseModel):
    person: PersonOut
    duplicate_contact_count: int
```

---

## Frontend

### 1. CollectionPage — select mode

- Add a "Select" button to the persons view header (hidden in cards view).
- Toggling it enters select mode: checkboxes appear on each person row/card.
- A floating action bar slides up from the bottom when ≥ 2 are selected:
  - Label: "X selected"
  - Button: "Merge" (disabled if < 2)
  - Button: "Cancel" (exits select mode)

### 2. MergeModal component

New component `frontend/src/components/MergeModal.tsx`. Two steps:

**Step 1 — Pick primary**

- Title: "Which record is primary?"
- List all selected persons, each showing: name + card thumbnail (first card's `front_image_path` if available, else initials avatar)
- Radio button per person; first in list pre-selected
- "Next →" button advances to Step 2

**Step 2 — Confirm**

- Title: "Merge into [primary name]?"
- Body: "All cards, names, and contact details from the other [N-1] record(s) will be moved here. Those records will be permanently deleted."
- "Confirm Merge" button (primary/destructive style) + "Back" link
- Loading state on confirm

**On success:**
- Navigate to `/persons/<primary_ext_id>`
- Show toast:
  - If `duplicate_contact_count > 0`: "Merged. {N} duplicate contact details found — review below."
  - Otherwise: "Merged successfully."

**On error:** Show toast error, close modal, stay on CollectionPage.

### 3. API client

Add to `frontend/src/api.ts`:
```ts
mergePersons(primaryExtId: string, sourceIds: string[]): Promise<MergeResult>
```

### 4. i18n

Add keys to `frontend/src/i18n.ts` for both `en` and `ja`:
- `selectPersons`, `mergeSelected`, `whichIsPrimary`, `mergeInto`, `confirmMerge`, `mergeSucceeded`, `mergeDuplicatesFound`

---

## Data integrity notes

- `PersonRelationship` rows referencing deleted persons are cascade-deleted (existing FK constraint). Relationships pointing to the primary survive.
- `google_resource` on source persons is discarded (primary's value kept). No sync conflict because Odoo/Google export runs after merge.
- `Card.person_id` reassignment is what preserves the physical card photos — they appear in PersonDetailPage's existing card grid immediately.

---

## Out of scope

- Undo / unmerge
- Automatic duplicate detection / merge suggestions
- Merging from PersonDetailPage (can add later)
