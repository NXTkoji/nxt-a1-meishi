# Manual Person Entry — Design Spec
_Date: 2026-04-25_

## Overview

Allow users to create a person record without scanning a business card. The entry collects the same fields as the post-scan review stage (names in multiple languages, company, title, contact details, Met As, Occasion, date, notes) and stores the result as a Person + Card with no card images.

---

## Entry Point

**CollectionPage header** — always shows two buttons regardless of active tab:

| Button | Style | Destination |
|---|---|---|
| `+ Enter Manually` | Secondary (gray border) | `/scan?manual=1` |
| `+ New Scan` | Primary (blue, existing) | `/scan` |

Both buttons are always visible. No tab-switching logic required.

---

## Frontend Flow

### 1. Navigation
Clicking `+ Enter Manually` navigates to `/scan?manual=1`.

### 2. ScanPage — manual mode detection
On mount, `ScanPage` reads the `manual` query param. When present:

- Create a scan session immediately on mount (`POST /api/v2/sessions`), same as normal flow.
- Set `stage = 'review'` immediately (skip upload / group / analyze).
- Pre-populate `cardGroups` with one blank group:
  ```ts
  {
    tempCardId: crypto.randomUUID(),
    images: [],           // no images
    myCompanyIds: [],
    occasionId: undefined,
    receivedDate: new Date().toISOString().slice(0, 10),
    notes: undefined,
    status: 'done',       // no analysis needed
    parsed: {
      names: [],
      positions: [],
      contact_details: [],
      languages: [],
    },
  }
  ```
- Show a simplified stage bar: **Review → Done** (no Upload / Group / Analyze steps).

### 3. Review UI
Identical to the existing post-scan review stage with one difference:

- **No image thumbnails** — the image area on the left of the card group is hidden.
- All fields behave exactly the same: ParsedCardEditor (names multi-lang, company, title, dept, contacts), add/remove fields, Met As chips, Occasion picker, date input, notes textarea.
- Duplicate detection is skipped (no Claude analysis was run).

### 4. Save
On save:

- Call `POST /api/v2/sessions/{sid}/confirm` with the card group data and an empty `grouped_images` list (session was already created on mount).
- Backend creates Person + Card with zero CardSide records (see Backend section).
- On success: navigate to the done stage and show the same "View Collection" / "New Scan" / "Enter Manually" options.

---

## Backend Change

**File:** `app/routers/v2/sessions.py` — `_confirm_one_card()`

Currently the function iterates over `grouped_images` to move temp files and create `CardSide` rows. When called with no images, it must skip that loop without error.

**Change:** Guard the CardSide creation block with `if grouped_images:`. No other logic changes — Person, Card, Position, ContactDetail, and CardMyCompany records are created normally.

**Result in DB:** A valid `Card` row with `my_company_links`, `occasion`, and `received_date` set, but zero `CardSide` children. This is intentional and consistent — the card represents the meeting record, not a physical card scan.

---

## i18n Additions

| Key | EN | JA |
|---|---|---|
| `enterManuallyBtn` | `+ Enter Manually` | `＋ 手動入力` |
| `manualEntryTitle` | `Enter Person Details` | `人物情報を入力` |

The stage bar in manual mode reuses existing `stageReview` and `stageDone` keys — no new keys needed for those.

---

## Out of Scope

- Adding images to a manually-entered record later (can be done via existing card-side upload if needed in future).
- Duplicate detection for manual entry (can be added later; not needed for initial save).
- Editing a manually-entered record is already supported via PersonDetailPage.
