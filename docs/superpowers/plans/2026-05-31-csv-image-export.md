# CSV + Image Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two download-based export destinations (Odoo CSV + images, Google Contacts CSV) alongside the existing direct-push Odoo and Google Contacts exports.

**Architecture:** New pure-function service `csv_export.py` generates CSV text from `LegacyCard` objects; two new GET endpoints in the existing export router serve CSV and individual card images as file downloads; the frontend handles download destinations entirely client-side without calling `runExport`.

**Tech Stack:** Python stdlib `csv`, FastAPI `StreamingResponse`/`FileResponse`, React 19 + `fetch` + blob URL download pattern.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `app/services/csv_export.py` | `format_odoo_csv()` and `format_google_csv()` — pure functions, no I/O |
| Modify | `app/routers/v2/export.py` | Add `GET /api/v2/export/csv` and `GET /api/v2/export/image/{card_id}/{side}` |
| Modify | `frontend/src/components/ExportDestinationSelector.tsx` | Add download destinations + client-side download logic |

---

## Task 1: CSV export service

**Files:**
- Create: `app/services/csv_export.py`

- [ ] **Step 1: Create `app/services/csv_export.py`**

```python
"""
CSV export service — pure functions with no I/O.

Takes a list of LegacyCard (app.models.card.Card) objects and returns a
UTF-8 CSV string formatted for the target system's import.
"""
from __future__ import annotations

import csv
import io

from app.models.card import Card


def _primary_name(card: Card) -> str:
    """Return the primary display name for the card owner."""
    names = card.person.names
    # Prefer name_type == "primary", fall back to first name
    for n in names:
        if n.type == "primary":
            return n.value
    return names[0].value if names else ""


def _work_phone(card: Card) -> str:
    for p in card.person.phones:
        if p.type in ("work", ""):
            return p.value
    return ""


def _mobile_phone(card: Card) -> str:
    for p in card.person.phones:
        if p.type == "mobile":
            return p.value
    return ""


def _first_email(card: Card) -> str:
    return card.person.emails[0].value if card.person.emails else ""


def _first_address(card: Card):
    return card.person.addresses[0] if card.person.addresses else None


def _notes(card: Card) -> str:
    """Plain-text notes (no HTML, unlike odoo_sync._build_notes)."""
    parts = []
    if card.received_date:
        parts.append(f"Card received: {card.received_date}")
    if card.notes:
        parts.append(card.notes)
    soc = card.person.social
    if soc.linkedin:
        parts.append(f"LinkedIn: {soc.linkedin}")
    if soc.wechat:
        parts.append(f"WeChat: {soc.wechat}")
    if soc.line:
        parts.append(f"LINE: {soc.line}")
    return "\n".join(parts)


def format_odoo_csv(cards: list[Card]) -> str:
    """
    Return a UTF-8 CSV string ready for Odoo's standard contact import.

    Columns: Name, Company Name, Job Position, Department, Phone, Mobile,
             Email, Website, Street, City, Zip, Country, Notes
    """
    FIELDNAMES = [
        "Name", "Company Name", "Job Position", "Department",
        "Phone", "Mobile", "Email", "Website",
        "Street", "City", "Zip", "Country", "Notes",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=FIELDNAMES, lineterminator="\r\n")
    writer.writeheader()

    for card in cards:
        pos = card.person.positions[0] if card.person.positions else None
        addr = _first_address(card)
        writer.writerow({
            "Name": _primary_name(card),
            "Company Name": pos.company if pos else "",
            "Job Position": pos.title if pos else "",
            "Department": pos.department if pos else "",
            "Phone": _work_phone(card),
            "Mobile": _mobile_phone(card),
            "Email": _first_email(card),
            "Website": card.person.website,
            "Street": addr.street if addr else "",
            "City": addr.city if addr else "",
            "Zip": addr.postal_code if addr else "",
            "Country": addr.country if addr else "",
            "Notes": _notes(card),
        })

    return buf.getvalue()


def format_google_csv(cards: list[Card]) -> str:
    """
    Return a UTF-8 CSV string ready for Google Contacts import.

    Columns: Name, Given Name, Family Name, Organization Name,
             Organization Title, Phone 1 - Value, Phone 1 - Type,
             E-mail 1 - Value, Address 1 - Street, Address 1 - City,
             Address 1 - Postal Code, Address 1 - Country, Notes
    """
    FIELDNAMES = [
        "Name", "Given Name", "Family Name",
        "Organization Name", "Organization Title",
        "Phone 1 - Value", "Phone 1 - Type",
        "E-mail 1 - Value",
        "Address 1 - Street", "Address 1 - City",
        "Address 1 - Postal Code", "Address 1 - Country",
        "Notes",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=FIELDNAMES, lineterminator="\r\n")
    writer.writeheader()

    for card in cards:
        pos = card.person.positions[0] if card.person.positions else None
        addr = _first_address(card)

        # Split display name into given/family heuristically.
        # For CJK names the full name goes into "Name" and both given/family
        # remain empty — Google handles it fine.
        display = _primary_name(card)
        parts = display.split()
        given = parts[0] if len(parts) >= 2 else ""
        family = " ".join(parts[1:]) if len(parts) >= 2 else ""

        # First non-fax phone
        phone = next(
            (p for p in card.person.phones if p.type != "fax"), None
        )

        writer.writerow({
            "Name": display,
            "Given Name": given,
            "Family Name": family,
            "Organization Name": pos.company if pos else "",
            "Organization Title": pos.title if pos else "",
            "Phone 1 - Value": phone.value if phone else "",
            "Phone 1 - Type": phone.type.capitalize() if phone else "",
            "E-mail 1 - Value": _first_email(card),
            "Address 1 - Street": addr.street if addr else "",
            "Address 1 - City": addr.city if addr else "",
            "Address 1 - Postal Code": addr.postal_code if addr else "",
            "Address 1 - Country": addr.country if addr else "",
            "Notes": _notes(card),
        })

    return buf.getvalue()
```

- [ ] **Step 2: Smoke-test the functions manually in Python REPL**

```bash
cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi"
python3 - <<'EOF'
from app.models.card import Card, Person, PersonName, Position, Phone, Email, Address, Social, CardImages
from app.services.csv_export import format_odoo_csv, format_google_csv

card = Card(
    notes="test",
    person=Person(
        names=[PersonName(value="山田太郎", language="ja", type="primary"),
               PersonName(value="Yamada Taro", language="en", type="romanized")],
        positions=[Position(company="株式会社テスト", title="部長", department="営業")],
        phones=[Phone(value="03-1234-5678", type="work"),
                Phone(value="090-1234-5678", type="mobile")],
        emails=[Email(value="taro@example.com")],
        addresses=[Address(street="1-2-3 Shibuya", city="Tokyo", postal_code="150-0001", country="Japan")],
        website="https://example.com",
        social=Social(linkedin="yamada-taro"),
    ),
)

print("=== Odoo CSV ===")
print(format_odoo_csv([card]))
print("=== Google CSV ===")
print(format_google_csv([card]))
EOF
```

Expected output: two CSV blocks with headers and one data row each, all fields populated correctly.

- [ ] **Step 3: Commit**

```bash
cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi"
git add app/services/csv_export.py
git commit -m "feat: add csv_export service (format_odoo_csv, format_google_csv)"
```

---

## Task 2: Backend — CSV download endpoint

**Files:**
- Modify: `app/routers/v2/export.py`

The existing router already has `_load_full_card()` and `_build_legacy_card()`. Add a new GET endpoint after the existing `run_export` POST.

- [ ] **Step 1: Add imports at the top of `app/routers/v2/export.py`**

After the existing imports, add:

```python
from fastapi import Query
from fastapi.responses import StreamingResponse
```

- [ ] **Step 2: Add `GET /api/v2/export/csv` endpoint**

Append to `app/routers/v2/export.py` (after the `run_export` function):

```python
@router.get("/csv")
async def export_csv(
    card_ids: str = Query(..., description="Comma-separated card external IDs"),
    format: str = Query(..., description="odoo or google_contacts"),
    db: AsyncSession = Depends(get_db),
):
    """
    Download a CSV of the requested cards formatted for Odoo or Google Contacts.

    GET /api/v2/export/csv?card_ids=abc,def&format=odoo
    GET /api/v2/export/csv?card_ids=abc,def&format=google_contacts
    """
    from app.services.csv_export import format_google_csv, format_odoo_csv

    if format not in ("odoo", "google_contacts"):
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="format must be 'odoo' or 'google_contacts'")

    ext_ids = [cid.strip() for cid in card_ids.split(",") if cid.strip()]
    if not ext_ids:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="card_ids must not be empty")

    # Load cards
    legacy_cards = []
    for ext_id in ext_ids:
        db_card = await _load_full_card(db, ext_id)
        if db_card is None:
            continue  # silently skip missing cards
        legacy = _build_legacy_card(
            db_card,
            db_card.person,
            db_card.person.contact_details,
            db_card.person.positions,
        )
        legacy_cards.append(legacy)

    if format == "odoo":
        csv_text = format_odoo_csv(legacy_cards)
        filename = "contacts_odoo.csv"
    else:
        csv_text = format_google_csv(legacy_cards)
        filename = "contacts_google.csv"

    return StreamingResponse(
        iter([csv_text.encode("utf-8-sig")]),  # utf-8-sig adds BOM for Excel compat
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
```

- [ ] **Step 3: Deploy and test the CSV endpoint**

```bash
cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi"
./deploy.sh
```

Wait ~5 seconds for the process to restart, then pick a real card external_id from the DB:

```bash
sqlite3 ~/.nxt-a1/meishi.db "SELECT external_id FROM cards WHERE deleted_at IS NULL LIMIT 1;"
```

Then test (replace `<ID>` and `<API_KEY>` from `.env`):

```bash
curl -H "X-API-Key: <API_KEY>" \
  "http://localhost:8000/api/v2/export/csv?card_ids=<ID>&format=odoo" \
  --output /tmp/test_odoo.csv && cat /tmp/test_odoo.csv

curl -H "X-API-Key: <API_KEY>" \
  "http://localhost:8000/api/v2/export/csv?card_ids=<ID>&format=google_contacts" \
  --output /tmp/test_google.csv && cat /tmp/test_google.csv
```

Expected: CSV with header row and one data row per card.

- [ ] **Step 4: Commit**

```bash
git add app/routers/v2/export.py
git commit -m "feat: add GET /api/v2/export/csv endpoint"
```

---

## Task 3: Backend — image download endpoint

**Files:**
- Modify: `app/routers/v2/export.py`

Card images are stored on disk. `CardSide` rows have `image_path` (absolute path) and `side_order` (0 = front, 1 = back).

- [ ] **Step 1: Add `FileResponse` import at the top of `app/routers/v2/export.py`**

Add `FileResponse` to the existing FastAPI response import:

```python
from fastapi.responses import FileResponse, StreamingResponse
```

- [ ] **Step 2: Add `GET /api/v2/export/image/{card_id}/{side}` endpoint**

Append to `app/routers/v2/export.py`:

```python
@router.get("/image/{card_id}/{side}")
async def export_image(
    card_id: str,
    side: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Download a card image named <primary_name>_front.jpg or <primary_name>_back.jpg.

    side must be "front" or "back".
    Returns 404 if card not found or that side has no image.
    """
    import mimetypes
    import os
    from fastapi import HTTPException

    if side not in ("front", "back"):
        raise HTTPException(status_code=400, detail="side must be 'front' or 'back'")

    db_card = await _load_full_card(db, card_id)
    if db_card is None:
        raise HTTPException(status_code=404, detail="Card not found")

    # side_order: 0 = front, 1 = back
    side_order = 0 if side == "front" else 1
    card_side = next((s for s in db_card.sides if s.side_order == side_order), None)
    if card_side is None:
        raise HTTPException(status_code=404, detail=f"No {side} image for this card")

    image_path = card_side.image_path
    if not os.path.isfile(image_path):
        raise HTTPException(status_code=404, detail="Image file not found on disk")

    # Build download filename from primary name
    legacy = _build_legacy_card(
        db_card,
        db_card.person,
        db_card.person.contact_details,
        db_card.person.positions,
    )
    names = legacy.person.names
    primary = next((n.value for n in names if n.type == "primary"), names[0].value if names else "card")
    # Sanitize for use as filename (remove slashes, null bytes)
    safe_name = primary.replace("/", "_").replace("\x00", "")
    ext = os.path.splitext(image_path)[1] or ".jpg"
    download_filename = f"{safe_name}_{side}{ext}"

    mime_type, _ = mimetypes.guess_type(image_path)
    mime_type = mime_type or "image/jpeg"

    return FileResponse(
        path=image_path,
        media_type=mime_type,
        filename=download_filename,
    )
```

- [ ] **Step 3: Deploy and test the image endpoint**

```bash
./deploy.sh
```

Wait ~5 seconds, then:

```bash
# Get a card ID that has sides
sqlite3 ~/.nxt-a1/meishi.db \
  "SELECT c.external_id FROM cards c JOIN card_sides s ON s.card_id=c.id WHERE c.deleted_at IS NULL LIMIT 1;"

curl -H "X-API-Key: <API_KEY>" \
  "http://localhost:8000/api/v2/export/image/<ID>/front" \
  --output /tmp/test_front.jpg -w "%{http_code}\n"
```

Expected: `200` and a valid JPEG file at `/tmp/test_front.jpg`. Test back with `/back` — expect 200 if back side exists, 404 if not.

- [ ] **Step 4: Commit**

```bash
git add app/routers/v2/export.py
git commit -m "feat: add GET /api/v2/export/image/{card_id}/{side} endpoint"
```

---

## Task 4: Frontend — download destinations

**Files:**
- Modify: `frontend/src/components/ExportDestinationSelector.tsx`

The component currently uses `runExport` for all destinations. We add two download-only destinations that bypass `runExport` and trigger browser downloads directly.

- [ ] **Step 1: Replace `ExportDestinationSelector.tsx` content**

Open `frontend/src/components/ExportDestinationSelector.tsx` and apply the full replacement below. Key changes:

1. `Destination` interface gets `download: boolean`
2. `DESTINATIONS` gains two new entries
3. New `downloadStatus` state tracks download results per destination
4. `handleExport` fans out: push destinations go to `runExport`; download destinations call `triggerDownloads`
5. `triggerDownloads` fetches CSV and images sequentially and fires blob downloads

Full replacement:

```tsx
/**
 * ExportDestinationSelector
 *
 * Second step of the export flow:
 *   - Multi-select destination checkboxes
 *   - "Export N cards to …" button
 *   - Inline result list after export runs (push) or downloads complete
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
  download: boolean
}

const DESTINATIONS: Destination[] = [
  { key: 'odoo',            label: 'Odoo',                       configured: true, download: false },
  { key: 'google_contacts', label: 'Google Contacts',            configured: true, download: false },
  { key: 'odoo_export',     label: 'Odoo Export (CSV + Images)', configured: true, download: true  },
  { key: 'google_csv',      label: 'Google Contacts CSV',        configured: true, download: true  },
]

/** Trigger a browser download from a Blob */
function triggerBlobDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

/** Fetch one URL and trigger download. Returns true on success. */
async function fetchAndDownload(
  url: string,
  filename: string,
  apiKey: string,
): Promise<boolean> {
  const res = await fetch(url, { headers: { 'X-API-Key': apiKey } })
  if (!res.ok) return false
  const blob = await res.blob()
  triggerBlobDownload(blob, filename)
  return true
}

/** Read the API key from localStorage (same key the api.ts module uses) */
function getApiKey(): string {
  return localStorage.getItem('apiKey') ?? ''
}

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
  // Results for direct-push destinations
  const [pushResults, setPushResults] = useState<ExportResultItem[] | null>(null)
  // Status for download destinations: key → 'downloading' | 'done' | 'error'
  const [downloadStatus, setDownloadStatus] = useState<Record<string, string>>({})
  const [hasRun, setHasRun] = useState(false)

  const pushMutation = useMutation({
    mutationFn: (pushDests: string[]) =>
      runExport({ card_external_ids: cardExternalIds, destinations: pushDests }),
    onSuccess: (data) => setPushResults(data.results),
  })

  const toggle = (key: string) =>
    setSelected(prev => {
      const next = new Set(prev)
      next.has(key) ? next.delete(key) : next.add(key)
      return next
    })

  /** Run all selected download destinations sequentially */
  async function triggerDownloads(dlDests: string[]) {
    const apiKey = getApiKey()
    const baseUrl = '/api/v2/export'
    const idsParam = cardExternalIds.join(',')

    for (const dest of dlDests) {
      setDownloadStatus(prev => ({ ...prev, [dest]: 'downloading' }))
      try {
        if (dest === 'google_csv') {
          const ok = await fetchAndDownload(
            `${baseUrl}/csv?card_ids=${idsParam}&format=google_contacts`,
            'contacts_google.csv',
            apiKey,
          )
          setDownloadStatus(prev => ({ ...prev, [dest]: ok ? 'done' : 'error' }))
        } else if (dest === 'odoo_export') {
          // 1. CSV first
          const csvOk = await fetchAndDownload(
            `${baseUrl}/csv?card_ids=${idsParam}&format=odoo`,
            'contacts_odoo.csv',
            apiKey,
          )
          if (!csvOk) {
            setDownloadStatus(prev => ({ ...prev, [dest]: 'error' }))
            continue
          }
          // 2. Images — front then back, for each card, sequentially
          for (const cardId of cardExternalIds) {
            for (const side of ['front', 'back'] as const) {
              // 404 = no image for that side → skip silently
              await fetchAndDownload(
                `${baseUrl}/image/${cardId}/${side}`,
                `${cardId}_${side}.jpg`,  // server will rename to <name>_front.jpg
                apiKey,
              )
            }
          }
          setDownloadStatus(prev => ({ ...prev, [dest]: 'done' }))
        }
      } catch {
        setDownloadStatus(prev => ({ ...prev, [dest]: 'error' }))
      }
    }
  }

  async function handleExport() {
    setHasRun(true)
    const pushDests = [...selected].filter(k => {
      const d = DESTINATIONS.find(x => x.key === k)
      return d && !d.download
    })
    const dlDests = [...selected].filter(k => {
      const d = DESTINATIONS.find(x => x.key === k)
      return d && d.download
    })

    // Run push and downloads in parallel
    const promises: Promise<unknown>[] = []
    if (pushDests.length > 0) promises.push(pushMutation.mutateAsync(pushDests))
    if (dlDests.length > 0) promises.push(triggerDownloads(dlDests))
    await Promise.allSettled(promises)
  }

  const destLabel = [...selected]
    .map(k => DESTINATIONS.find(d => d.key === k)?.label ?? k)
    .join(' + ')

  const isPending = pushMutation.isPending ||
    Object.values(downloadStatus).includes('downloading')

  // Show results view once export has been triggered and all downloads settled
  const allDownloadsDone = Object.values(downloadStatus).every(
    s => s === 'done' || s === 'error',
  )
  const showResults = hasRun && !pushMutation.isPending && allDownloadsDone

  if (showResults && (pushResults !== null || Object.keys(downloadStatus).length > 0)) {
    // Group push results by card
    const grouped: Record<string, ExportResultItem[]> = {}
    for (const r of pushResults ?? []) {
      grouped[r.card_external_id] = grouped[r.card_external_id] ?? []
      grouped[r.card_external_id].push(r)
    }

    return (
      <div className="max-w-2xl mx-auto py-6 px-4 space-y-4">
        <h2 className="text-base font-semibold">{t.exportDestTitle}</h2>

        {/* Download destination results */}
        {Object.entries(downloadStatus).length > 0 && (
          <div className="rounded-xl border border-gray-200 bg-white divide-y divide-gray-100 overflow-hidden">
            {Object.entries(downloadStatus).map(([dest, status]) => {
              const label = DESTINATIONS.find(d => d.key === dest)?.label ?? dest
              return (
                <div key={dest} className="px-4 py-3 flex items-center justify-between gap-4">
                  <span className="text-sm text-gray-700 font-medium">{label}</span>
                  <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                    status === 'done'  ? 'bg-green-100 text-green-700' :
                    status === 'error' ? 'bg-red-100 text-red-700' :
                    'bg-gray-100 text-gray-500'
                  }`}>
                    {status === 'done' ? '✓ Downloaded' :
                     status === 'error' ? '✗ Failed' : 'Downloading…'}
                  </span>
                </div>
              )
            })}
          </div>
        )}

        {/* Push destination results — per card */}
        {Object.keys(grouped).length > 0 && (
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
        )}

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
            {dest.download && (
              <span className="ml-auto text-xs text-gray-400">↓ Download</span>
            )}
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
        disabled={selected.size === 0 || isPending}
        onClick={handleExport}
        className="btn-primary w-full py-3 text-sm disabled:opacity-50"
      >
        {isPending
          ? 'Exporting…'
          : t.exportRunBtn(cardExternalIds.length, destLabel || '…')}
      </button>

      {pushMutation.isError && (
        <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded px-3 py-2">
          Export failed: {pushMutation.error instanceof Error ? pushMutation.error.message : 'Unknown error'}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Check where `apiKey` is stored in `api.ts`**

```bash
grep -n "apiKey\|X-API-Key\|localStorage" \
  "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi/frontend/src/api.ts" | head -20
```

If the key name in localStorage differs from `'apiKey'`, update the `getApiKey()` function in the component to match.

- [ ] **Step 3: Build the frontend**

```bash
cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi/frontend"
npm run build
```

Expected: build completes with no TypeScript errors.

- [ ] **Step 4: Hard-refresh and smoke test in the browser**

1. Open the app in Chrome
2. Press **Cmd+Shift+R** to hard-refresh (clears cached JS bundle)
3. Go to Export page, select some cards
4. In the destination selector, verify 4 destinations are visible with "↓ Download" tags on the two new ones
5. Select "Odoo Export (CSV + Images)" and click Export
6. Verify CSV downloads and image files download (one per side per card)
7. Select "Google Contacts CSV" and click Export
8. Verify `contacts_google.csv` downloads
9. Select "Odoo" (push) + "Google Contacts CSV" (download) together — verify both happen

- [ ] **Step 5: Commit**

```bash
cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi"
git add frontend/src/components/ExportDestinationSelector.tsx
git commit -m "feat: add download export destinations (Odoo CSV+images, Google CSV)"
```

---

## Self-Review

**Spec coverage:**

| Spec requirement | Task |
|---|---|
| `odoo_export` destination: CSV + images in one click | Task 4 (`triggerDownloads`) |
| `google_csv` destination: CSV only | Task 4 |
| Existing `odoo` and `google_contacts` unchanged | ✓ existing code untouched |
| `GET /api/v2/export/csv?card_ids=...&format=odoo\|google_contacts` | Task 2 |
| `GET /api/v2/export/image/{card_id}/{side}` | Task 3 |
| Filename `<name>_front.jpg` / `<name>_back.jpg` | Task 3 step 2 |
| No images in CSV | ✓ `csv_export.py` has no image fields |
| Sequential image downloads (avoid browser prompt) | Task 4 — nested `for` loops, `await` each |
| 404 for missing card or missing side | Task 3 step 2 |
| UTF-8 BOM for Excel compat | Task 2 step 2 (`utf-8-sig`) |
| `download: boolean` on Destination interface | Task 4 step 1 |
| "↓ Download" label hint in UI | Task 4 step 1 |
| "✓ Downloaded" / "✗ Failed" status display | Task 4 step 1 results view |

**No placeholders found.**

**Type consistency:** `LegacyCard` → `Card` (imported as `from app.models.card import Card`) used consistently across Tasks 1, 2, 3. `_build_legacy_card()` signature and call sites match the existing export.py pattern.
