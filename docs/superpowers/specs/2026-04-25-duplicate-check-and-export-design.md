# Duplicate Check & Manual Export — Design Spec

**Date:** 2026-04-25
**Status:** Approved for implementation planning

---

## Overview

Two independent features added to NXT-A1:

1. **Duplicate Check** — when a scanned card matches an existing person, show a side-by-side field editor inline in the review step so the user can merge, discard, or treat as a new person.
2. **Manual Export** — replace automatic sync-on-confirm with a user-initiated export flow: filter and select cards, choose destination(s), export.

---

## 1. Duplicate Check

### Where it appears

Inline within the existing **Review step** of the scan flow (`ScanPage.tsx`), immediately below the parsed card fields. Only shown when `match_confidence >= 0.55` (existing threshold). No new screen or modal is added.

### UI: Two-column field editor

The duplicate panel expands as a two-column editor using the same drag-and-drop interaction style as the existing post-analysis field editor (`ParsedCardEditor`).

| Column | Content |
|---|---|
| Left — **Existing record** | Current state of the matched person from the local DB |
| Right — **New card** | Fields parsed from the scanned card |

**Interactions:**
- **Drag handle (⠿) on right-column fields** — user drags a field from the new card into the existing record on the left. A drop zone appears in the appropriate section as the user drags.
- **✕ on either column** — permanently deletes that field from the record being built.
- Fields on the right that are new or different from the existing record are **highlighted in green** so the user can spot differences at a glance. Same-value fields display in normal style.
- Both columns support **"+ Add field"** for manual entry.

**Actions (footer of the panel):**

| Action | Behaviour |
|---|---|
| **Not a duplicate →** (top-right) | Dismisses the duplicate panel. New card is saved as a fresh person. Scan continues normally. |
| **Discard new card** | Removes the pending scan from the session (no DB record created). Existing person record is untouched. |
| **Confirm merge** | Saves the left column as the updated existing person. Any fields remaining in the right column that were not dragged across are discarded. The new card's image is always linked to that person as an additional card record, regardless of how many fields were moved. |

### Data changes on merge

- `PersonName`, `ContactDetail`, `Position`, `Organization` records on the existing person are updated to reflect the final left-column state (append-only name history is preserved — new values appended, old marked `is_current=false`).
- A new `Card` + `CardSide` record is created for the scanned image and linked to the existing `Person`.
- No automatic export is triggered.

---

## 2. Manual Export

### Trigger

Export is **never triggered automatically** on confirm. The existing concurrent sync calls in `routers/confirm.py` are removed. Users initiate export explicitly from the card list.

### Flow

```
Card List → tap "Export" → Export Selection screen → Choose Destinations screen → Export runs → Result summary
```

### Export Selection Screen

A dedicated screen (new route/page). Not a modal, not inline.

**Filter bar:**
- **Text search** — full-text across all fields: all name languages, company, title, department, email, phone, address, notes.
- **Filter chips** — dismissible chips for active filters. Available filters:
  - Year (picker)
  - Month (year + month picker)
  - Date (single date picker)
  - Occasion (dropdown of existing occasions)
  - "Not yet exported" — shows only cards with no sync history to any destination

**Results list:**
- Shows cards matching **all** active filters simultaneously (AND logic — text search + chips all apply together).
- Each row has a **checkbox** for selection.
- **Sync history badges** shown per row (e.g. `Odoo`, `Google`) — informational only, does not block selection or re-export.
- **"Select all N"** — selects all cards in the current filtered results (not the whole database).
- **"Deselect all"** — clears selection within filtered results.
- Individual rows can be toggled independently after a "select all".

**Footer:** "Next: Choose destinations (N cards) →" — disabled until at least one card is selected.

### Choose Destinations Screen

Lists all configured destination integrations with checkboxes (multi-select). Unconfigured destinations (missing credentials) are shown grayed out with a "Set up →" link.

For each selected card, if that card has prior sync history to a chosen destination, a subtle informational note is shown ("already synced") — not a warning, not blocking.

The export button label updates dynamically: "Export N cards to Odoo" / "Export N cards to Odoo + Google Contacts".

### Export Result

Shown inline on the destination screen after the export runs — no new screen. Each card shows: ✓ Created / ✓ Updated / ✗ Error per destination. A "← Back to card list" button closes the flow.

### Sync History Tracking

A new `CardSyncHistory` table tracks each export event:

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `card_id` | FK → Card | |
| `destination` | enum | `odoo`, `google_contacts`, `onedrive` (OneDrive tracked but not shown in export UI — file upload only) |
| `synced_at` | datetime | |
| `result` | enum | `created`, `updated`, `error` |
| `error_message` | text nullable | |

Sync badges on card rows and selection screens are derived from the most recent `CardSyncHistory` record per card per destination.

---

## 3. What is NOT in scope

- Duplicate check against external destination databases (Odoo, Google Contacts) — left to those apps' own deduplication features.
- Field-level export selection (choosing which fields to send per card) — whole-card export only.
- Automatic re-export on card edit.
- OneDrive as a selectable export destination in the UI (it is a file upload, not a contact database — handle separately if needed).

---

## Affected files (expected)

| Area | Files |
|---|---|
| Backend — remove auto-sync | `app/routers/confirm.py` |
| Backend — new sync history | `app/models/db/models.py`, new migration |
| Backend — export endpoint | new `app/routers/v2/export.py` |
| Backend — card search/filter | `app/routers/v2/cards.py` (add filter params) |
| Frontend — duplicate panel | `frontend/src/components/DuplicateFieldEditor.tsx` (new) |
| Frontend — export selection | `frontend/src/pages/ExportPage.tsx` (new) |
| Frontend — destination chooser | `frontend/src/components/ExportDestinationSelector.tsx` (new) |
| Frontend — card list | `frontend/src/pages/CardsPage.tsx` (add Export button) |
