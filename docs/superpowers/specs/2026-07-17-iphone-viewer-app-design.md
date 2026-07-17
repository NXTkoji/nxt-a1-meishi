# iPhone App — View & Edit Name Cards (v1)

**Status:** Approved for planning
**Date:** 2026-07-17

## Goal

A native iPhone app for browsing, searching, viewing, and editing the contacts already captured by NXT-A1 (via the Shortcut scan flow), for quick lookup away from the Mac/web app. Adding/scanning new cards is explicitly out of scope for v1 — that's already handled by the iPhone Shortcut.

## Architecture

A native SwiftUI app talks directly to the existing FastAPI backend (`nxt-a1-meishi`) over REST. No new backend endpoints are required — the current v2 API (`app/routers/v2/persons.py` and related) already supports everything this app needs: list, search, detail, edit, delete.

```
iPhone app (SwiftUI)
  → HTTPS → Cloudflare Tunnel → localhost:8000 → FastAPI backend (existing)
```

### Backend exposure

The backend currently only listens on `localhost:8000`, managed by the `co.nxta.nxt-a1-backend.plist` LaunchAgent, with no public HTTPS endpoint configured (the Shortcut docs have a placeholder domain).

- Run **Cloudflare Tunnel** (`cloudflared`) as a second LaunchAgent on the Mac, pointing a subdomain (e.g. `meishi-api.nxta.co`) at `localhost:8000`.
- No port forwarding, no static IP, no exposed router ports — the tunnel makes an outbound connection to Cloudflare.
- Requires `nxta.co` DNS to be managed by Cloudflare (free plan is sufficient). This is a prerequisite to confirm during implementation planning.
- Auth is unchanged: the existing `api_key` Bearer-token check in `app/auth.py` (`verify_api_key`) is reused as-is — same mechanism the Shortcut already uses.

**Rejected alternatives:**
- A separate mobile-specific backend (BFF) — unnecessary duplication at this scale; the existing v2 API is already a clean fit.
- Tailscale/VPN-only access — would require both Mac and phone to run Tailscale continuously, which doesn't fit the "quick lookup away from home/office" use case.

## Screens & Navigation

Single-stack navigation (no tab bar) — List → Detail → Edit sheets.

### A. Contact List (home screen)
- Search bar, live-filters by name, company, phone, or email
- Row: name, company/title, avatar (person photo if available, else initials)
- Pull-to-refresh
- Tap row → Person Detail

### B. Person Detail
- Header: person photo, primary name, primary position/company
- Grouped sections (mirrors the web app's data model):
  - Names (multilingual — e.g. native + romanized)
  - Position(s) / Organization(s)
  - Contact details (phones, emails, addresses, each with a label)
  - Notes
  - Card images (front/back) — tap for full-screen, pinch to zoom
- Each section has an Edit affordance
- Delete person (with confirmation)

### C. Settings
- API base URL (default `https://meishi-api.nxta.co`, editable)
- API key, stored in iOS Keychain (not UserDefaults)
- "Test Connection" button — calls `/api/v1/health` with the configured key

## Editing

Each section on Person Detail opens a small edit sheet scoped to just that section (mirrors the web app's `PersonEditor.tsx` pattern of focused per-field-group edits rather than one large form). Saving a section only affects that section — a failed save doesn't lose edits elsewhere on the screen.

Maps directly onto existing endpoints in `app/routers/v2/persons.py`:

| iPhone edit action | Backend call |
|---|---|
| Edit a name (native/romanized) | `PATCH /api/v2/persons/{id}/names/{name_id}` |
| Edit company/title | `PATCH /api/v2/persons/{id}/positions/{position_id}` (+ related org endpoint for org name) |
| Add/edit/delete phone, email, address | `POST` / `PATCH` / `DELETE` on `.../contact-details/{id}` |
| Edit notes | `PATCH /api/v2/persons/{id}` |
| Delete person | `DELETE /api/v2/persons/{id}` |

**Networking layer:** a thin `APIClient` (URLSession + Swift `async/await`) with `Codable` structs mirroring the backend's `PersonOut`, `ContactDetailOut`, `PositionOut`, etc. schemas (`app/schemas/api.py`), kept as a 1:1 mirror to avoid drift from the backend contract.

**Validation:** minimal client-side checks (e.g. non-empty required fields). The backend remains the source of truth; 400/422 responses surface as inline errors on the edit sheet.

## Auth & Error Handling

- Every request sends `Authorization: Bearer <key>` using the key stored in Settings/Keychain.
- **401** anywhere in the app → redirect to Settings with an explanation ("Invalid API key — check Settings").
- **Network unreachable** → banner with retry; doesn't crash the list.
- **404** (e.g. person deleted elsewhere) → "This contact no longer exists," back action.
- **400/422 on save** → inline field error on the edit sheet; sheet stays open so nothing is lost.

## Out of Scope for v1

- Scanning/adding new cards (handled by the existing iPhone Shortcut)
- Duplicate detection / merge
- Export/sync to Odoo, Google Contacts, OneDrive
- Face ID / app-open lock (declined for v1 — relies on the phone's own lock screen)
- Offline caching (app assumes network reachability, consistent with the public-HTTPS backend)

Each of these can become its own future spec.

## Testing

Manual QA against the real backend (once tunneled): browse list, search, open detail, edit each field type, delete a person, and cross-check against the web app / Odoo that nothing regresses. Given the small, well-isolated networking layer, lightweight unit tests on `APIClient` request building and response decoding are worth adding; full UI test automation isn't warranted for a single-user internal tool.
