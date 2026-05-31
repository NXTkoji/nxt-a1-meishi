# CSV + Image Export — Design Spec
Date: 2026-05-31

## Overview

Add two new download-based export destinations alongside the existing direct-push
destinations (Odoo XML-RPC, Google Contacts API). Users can select any combination
of destinations in the existing ExportDestinationSelector UI.

## Destinations

| Key | Label | Behavior |
|-----|-------|----------|
| `odoo` | Odoo | Direct XML-RPC push (existing) |
| `google_contacts` | Google Contacts | Direct API push (existing) |
| `odoo_export` | Odoo Export (CSV + Images) | Downloads CSV + individual card images |
| `google_csv` | Google Contacts CSV | Downloads CSV |

## Backend

### New endpoints

#### `GET /api/v2/export/csv`

Query params:
- `card_ids` — comma-separated list of card external IDs
- `format` — `odoo` or `google_contacts`

Returns `text/csv` with `Content-Disposition: attachment; filename=contacts_odoo.csv`
(or `contacts_google.csv`).

No images included. Missing or empty fields produce empty CSV cells.

#### `GET /api/v2/export/image/{card_id}/{side}`

- `card_id` — card external ID
- `side` — `front` or `back`

Returns the image file with:
- `Content-Type: image/jpeg` (or detected MIME type)
- `Content-Disposition: attachment; filename=<primary_name>_<side>.jpg`

Where `<primary_name>` is the contact's primary name (e.g. `山田太郎`).

Returns 404 if the card doesn't exist or that side has no image.

### New file: `app/services/csv_export.py`

Two pure functions operating on a list of `LegacyCard` objects (same type already
used by `odoo_sync.py` and `google_contacts.py`):

**`format_odoo_csv(cards: list[Card]) -> str`**

Columns (matching Odoo's standard contact import format):
`Name`, `Company Name`, `Job Position`, `Department`, `Phone`, `Mobile`, `Email`,
`Website`, `Street`, `City`, `Zip`, `Country`, `Notes`

**`format_google_csv(cards: list[Card]) -> str`**

Columns (matching Google Contacts import format):
`Name`, `Given Name`, `Family Name`, `Organization Name`, `Organization Title`,
`Phone 1 - Value`, `Phone 1 - Type`, `E-mail 1 - Value`,
`Address 1 - Street`, `Address 1 - City`, `Address 1 - Postal Code`,
`Address 1 - Country`, `Notes`

Both functions use Python's stdlib `csv` module. No images included.

### Router: `app/routers/v2/export.py`

Add the two new GET endpoints to the existing export router. Card loading reuses
`_load_full_card()` and `_build_legacy_card()` already defined in that file.

## Frontend

### `ExportDestinationSelector.tsx`

Add `download: boolean` flag to the `Destination` interface. Updated `DESTINATIONS`:

```ts
const DESTINATIONS: Destination[] = [
  { key: 'odoo',           label: 'Odoo',                        configured: true, download: false },
  { key: 'google_contacts',label: 'Google Contacts',             configured: true, download: false },
  { key: 'odoo_export',    label: 'Odoo Export (CSV + Images)',  configured: true, download: true  },
  { key: 'google_csv',     label: 'Google Contacts CSV',         configured: true, download: true  },
]
```

**Export button behavior:**

When the user clicks Export:
1. Direct-push destinations (`download: false`) — call existing `runExport` API, show result badges as today.
2. Download destinations (`download: true`) — handled client-side, no `runExport` call:
   - `odoo_export`: fetch `/api/v2/export/csv?card_ids=...&format=odoo` → trigger download, then fetch `/api/v2/export/image/{id}/front` and `/api/v2/export/image/{id}/back` for each card sequentially → trigger download for each that returns 200.
   - `google_csv`: fetch `/api/v2/export/csv?card_ids=...&format=google_contacts` → trigger download.

Downloads are triggered via a temporary `<a href=blob download=filename>` click pattern.
Sequential image downloads (one at a time) avoid the browser's multi-download permission prompt.

**Result display:**

- Direct-push results: existing result badge UI (created / updated / error).
- Download destinations: simple inline status line — "✓ Downloaded" or "✗ Failed" per destination.
- Both can appear together if the user selected a mix.

## What's not included

- Card images are not embedded in CSV files (for either format).
- Google Contacts CSV has no image column (Google doesn't support image import via CSV).
- No ZIP packaging — individual files only.
- The existing direct-push Odoo and Google Contacts destinations are unchanged.
