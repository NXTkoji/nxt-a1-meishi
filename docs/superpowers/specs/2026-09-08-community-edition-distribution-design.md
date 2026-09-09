# Community Edition — Distributing NXT名刺整理器 to Non-Technical Friends

**Date:** 2026-09-08
**Status:** Design approved, pending implementation plan

## 1. Context

The app today is a single-user, local-first tool that only Koji can run. The
launcher hardcodes an absolute path into Koji's Google Drive folder and calls
`/usr/local/bin/python3`; `dist/` is gitignored so there is no shippable
frontend build; and every outbound integration authenticates as Koji.

The goal is to hand a working copy to friends in the Taipei community — Rotary,
日本人会, 沖縄県人会, WUB — who complain about their business card piles. This
is a goodwill project. There is no revenue, so the design is constrained by two
things above all else: **near-zero ongoing cost** and **near-zero support
burden**.

**The primary value of this app is retrieval, not capture.** Scanning is the
cost of entry; being able to find the right contact later is the reason to keep
using it. That judgement drives several decisions below that would otherwise
look like scope creep — search breadth, pagination, and backup in particular.

A second motive shapes the design: Koji is introducing these friends to Claude.
Sitting with someone to create their Anthropic account is therefore part of the
point rather than an onboarding tax, which is what makes bring-your-own-key
viable for an audience that could not complete that signup alone.

## 2. Decisions

Each of these was a real fork in the road. Recording the reasoning so a future
reader does not relitigate them.

| Decision | Choice | Why |
|---|---|---|
| What "sharing" means | Distribute the app itself | Not contact-sharing, not team collaboration, not a digital business card |
| Audience | Community friends | Non-technical, mixed platform, no shared infrastructure, no support appetite |
| Payload | The whole collection app | Not just photo→vCard. Scan, browse, search, dedupe, organize |
| Where data lives | Entirely on the friend's own machine | No hosting cost, no custody of other people's contacts, no Taiwan PDPA exposure |
| Hosting | None | Golden-Zeus is a laptop that sleeps, so self-hosting was never viable |
| Platform for v1 | macOS only | Halves the work. The launcher, `lsof`, `open`, and paths are all POSIX-shell today |
| Claude API access | Each friend uses their own Anthropic account; Koji sets it up with them in person | Koji is also promoting Claude adoption in the community, so the account signup is a feature of the visit, not a cost of it. A Claude Pro/Max subscription grants **no** API access — the friend needs a console.anthropic.com organisation with its own billing. The objection to this (a non-technical person cannot self-serve that flow) is answered by Koji being present |
| Scan model | Default `claude-sonnet-5`, changeable via a picker populated live from the Models API | Current generation, and ~26% cheaper per card than the `claude-sonnet-4-6` pinned in `app/config.py:11` today ($2/$10 versus $3/$15 per MTok). A **hardcoded** model string is a time bomb: with no auto-update, the day that model retires every install breaks at once and the only repair is shipping a new `.dmg` to everybody. The picker exists for survivability, not for choice |
| Google Contacts sync | Deferred, but the button stays visible and explains itself | Auth is a refresh token baked into `.env` ([google_contacts.py:93](../../../app/services/google_contacts.py)). No in-app OAuth flow exists, and the OAuth client is unverified against a sensitive scope — every friend would need manual test-user allowlisting and would still see Google's warning screen. Keeping the button advertises the capability and creates a natural moment for the friend to contact Koji, rather than hiding a feature that does exist |
| Backup | Local zip of `~/.nxt-a1/`, with restore | Local-only storage plus "retrieval is the primary value" means one dead laptop loses the entire collection. A non-technical friend will not have Time Machine configured |
| Gatekeeper | Apple Developer Program, US$99/yr | The only option compatible with zero support. Unsigned distribution means walking non-technical friends through Terminal or System Settings, in three languages, with behaviour that varies by macOS version |

### Multi-tenancy was considered and rejected

A hosted multi-tenant service would have required an `owner_id` on persons,
organizations, cards, contact_details, scan_sessions, occasions, and
my_companies, with every query filtered. One missed `WHERE` clause is a privacy
incident among friends. Local-only sidesteps the entire class of bug.

## 3. Non-goals for v1

- Windows or Linux
- Odoo, OneDrive, and Google Drive sync (absent)
- Google Contacts OAuth and app verification (deferred — the button ships,
  explaining itself; see W5)
- Auto-update
- Multi-user, accounts, or any server component
- Sharing contacts *between* friends' installs

## 4. What already works in our favour

- `app/config.py:36` already resolves the database, images, and temp directory
  under `~/.nxt-a1/`. Per-user local storage is largely solved.
- Odoo, Google, and OneDrive services already no-op when their credentials are
  blank (e.g. `google_contacts.py:230`).
- The UI already ships ja / en / zh-TW (`frontend/src/i18n.ts:3`), which is
  exactly the language mix of the target audience.
- `my_companies` has no seeded rows in migrations, so a fresh
  `~/.nxt-a1/meishi.db` starts empty rather than pre-loaded with NXT / 正康 /
  智原.

## 5. Architecture

A `.app` bundle containing a PyInstaller-frozen backend (uvicorn, FastAPI,
opencv, alembic), the built `frontend/dist`, and `migrations/`. Double-clicking
it starts the backend on a free port and opens the default browser at the
collection page. The `.app` owns the process and appears in the Dock; quitting
it stops the server.

Expect roughly 150 MB from opencv and numpy. Acceptable for a Drive link.

This is **not a fork**. It is the same repository with a new packaging target
and a first-run path. Distribution-specific behaviour is achieved by runtime
detection of absent credentials, never by a build-time `COMMUNITY_EDITION` flag.

## 6. Phasing

The work splits along a line that matters: most of it makes the app usable by
somebody who is not Koji, and only a minority is macOS packaging.

**Phase A — usable by someone else.** W3, W4, W5, W6, W7, W10, W11, W12.
Every one is verifiable in the existing development tree with the app running as
it does today. No bundle, no signing, no Apple Developer membership required.

**Phase B — ship it.** W1, W2, W8, W9, W13. Bundling, Gatekeeper, notarization,
the install guide, and clean-room verification.

Three reasons for this order:

1. **Phase A is dogfoodable.** Better search, backup, and a model picker improve
   Koji's own daily use immediately. If the audience-validation risk in §10
   bites and no friend ever installs this, Phase A was still worth building.
2. **It defers the money.** The US$99 Apple Developer fee is only needed for
   Phase B, so it is spent after the app has proven itself rather than before.
3. **It separates the unfamiliar risk.** Hardened Runtime and PyInstaller
   failures are the least predictable part of this project. Isolating them keeps
   them from blocking work that carries no such uncertainty.

Phase B depends on Phase A only in that W13's checklist verifies Phase A
features. Nothing in Phase A depends on Phase B.

## 7. Work items

### W1 · Phase B — Bundle-safe path resolution

Three paths are resolved relative to the source tree or the working directory
and will break inside a frozen bundle:

- `app/main.py:117` — `_FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"`
- `app/main.py:33` — `AlembicConfig("alembic.ini")`
- the Alembic `script_location` pointing at `migrations/`

Introduce one `resource_path()` helper that is aware of `sys._MEIPASS` when
frozen and falls back to the source tree otherwise. Apply it to all three.

### W2 · Phase B — Delete the Docker launcher, choose a free port

`frontend/src/App.tsx:12` defines `LAUNCHER = 'http://127.0.0.1:8001'`, posts
to it to "start Docker," sends a `/stop` beacon on `beforeunload`, and shows the
user "Check that Docker is running" on timeout. None of this applies to a
distributed bundle. Remove the launcher client, the beacon, and the Docker
copy — do not adapt them.

Port 8000 may be occupied on a friend's Mac. Bind an ephemeral free port at
launch, pass it to uvicorn, and open the browser at whatever was obtained.
Remove the fixed-port assumption from the launcher and the frontend.

Note that Koji's own machine runs two supervisors for this app (the
`co.nxta.nxt-a1-backend` LaunchAgent and `~/.nxt-a1/launcher.py`). Neither ships.
The distributed bundle is self-supervising.

### W3 · Phase A — Config store with source precedence

Add `~/.nxt-a1/config.json`, written by the first-run wizard and readable by
`app/config.py`. Precedence, highest first:

1. environment variables / `.env` — Koji's development machine
2. `~/.nxt-a1/config.json` — a friend's install
3. defaults

Keys a distributed install needs: `anthropic_api_key` and `claude_model`. Odoo,
Google, and Microsoft keys stay absent, which is what drives the runtime gating
in W5.

`claude_model` defaults to `claude-sonnet-5` and is changeable from Settings via
the live picker described in W10. It is not offered during the wizard — a friend
setting up for the first time has no basis to choose, and the default is right.

Changing the default from the `claude-sonnet-4-6` currently pinned at
`app/config.py:11` also applies to Koji's own install: it is the current
generation and cheaper per card. Note that `claude_parser.py:284` only sends
`thinking: {"type": "adaptive"}` when the model name contains `opus`, so the
Sonnet path is unaffected by the switch.

UI language is deliberately **not** stored here. It already persists in
`localStorage` via `frontend/src/LangContext.tsx:30`, and the wizard simply uses
that existing setter.

### W4 · Phase A — First-run setup wizard

Shown when `config.json` is missing. Steps:

1. Choose language (ja / en / zh-TW), through the existing `LangContext` setter
2. **Prerequisites checklist.** Before asking for anything, show what the whole
   setup will require, so nobody starts and stalls halfway:
   - an email address
   - a credit card (for the Anthropic account)
   - an internet connection
   - roughly 20 minutes
   - and the cost, stated plainly: *about US$5 covers a 300-card shoebox*

   This screen exists specifically to defuse the "friend stalls at the billing
   step" risk in §10. It is the first thing they see, not a footnote.
3. Paste the Claude API key. **Validate it with one cheap API call before
   accepting** so a typo or a bad paste fails during setup, not during the
   friend's first scan.
4. Add the first `my_company` entry
5. Done — land on the collection page

### W5 · Phase A — Three states for integration destinations

`frontend/src/components/ExportDestinationSelector.tsx:26-29` hardcodes four
destinations with `configured: true`. Drive that flag from a backend capability
response derived from which credentials are actually present, and give each
destination one of **three** states rather than two:

| State | Destinations | Behaviour |
|---|---|---|
| Configured | Everything on Koji's install | Works as today |
| Deferred | Google Contacts, on a distributed install | Button stays **visible**. Pressing it opens a dialog in the user's language: this feature needs additional setup, please contact Koji. Styled as information, **not** as an error — nothing has gone wrong |
| Not applicable | Odoo, on a distributed install | Hidden entirely. Odoo is Koji's ERP and is meaningless to a friend |

The same three-state treatment applies to the Odoo and Google sections of
`SettingsPage`, and to the Google Contacts auto-sync that fires on card
confirm — auto-sync is silently inactive when unconfigured, since a dialog on
every card confirm would be intolerable.

Keeping the Google button visible is deliberate: it advertises a capability that
genuinely exists and gives the friend a reason to get back in touch, which is
also when Koji learns whether demand justifies the OAuth verification project.

### W6 · Phase A — vCard export (new feature)

An interoperability escape hatch, not the product's payoff — retrieval inside
the app is the payoff (§1). vCard matters because it gets contacts onto a phone
and because it means the friend is never locked in. It needs no OAuth.

Add `GET /api/v2/export/vcard` returning a `.vcf` for a card selection or the
whole collection. Double-clicking a `.vcf` on macOS imports directly into
Contacts, which syncs onward to the friend's iPhone.

Note this is **not** the backup mechanism — vCard loses card images, occasions,
relationships, and merge history. Backup is W11.

Requirements:

- **vCard 3.0**, which macOS Contacts handles most reliably
- UTF-8 throughout — CJK names are the common case, not the edge case
- Multiple `TEL` and `EMAIL` entries per person
- `ORG`, `TITLE`, `ADR`, `BDAY`
- Optionally embed the front-side card image as `PHOTO`

### W7 · Phase A — Comprehensible quota and key errors

A friend's prepaid credit balance will eventually run out, and a key can be
revoked or mistyped. When the Anthropic call fails on authentication or on an
exhausted balance, the scan path must surface a plain-language message in the
user's chosen language — "your Claude credit has run out; top it up at
console.anthropic.com" — not a raw API error.

Because the friend owns the account, this error is **self-serve**: the message
should tell them exactly what to do rather than tell them to contact Koji. Every
case where it fails to do so becomes a support call, which is what the whole
design is trying to avoid. Distinguish at minimum: invalid key, exhausted
credit, and rate limit.

### W8 · Phase B — Build, sign, notarize

`scripts/build-app.sh`, one command on Koji's Mac:

1. `npm run build` in `frontend/`
2. PyInstaller freeze of the backend
3. Assemble the `.app`
4. **Sign every nested binary first.** PyInstaller bundles dozens of `.so` and
   `.dylib` files from numpy, opencv, and Pillow. Inner binaries are signed
   before the outer bundle.
5. `codesign --options runtime --timestamp` with the Developer ID Application
   identity
6. `xcrun notarytool submit --wait`
7. `xcrun stapler staple`
8. Package as a `.dmg`

**Entitlements.** Notarization requires the Hardened Runtime, which breaks
CPython bundles by default. The app needs:

- `com.apple.security.cs.allow-unsigned-executable-memory`
- `com.apple.security.cs.disable-library-validation` — opencv and numpy load
  unsigned shared objects at import time

Getting this wrong produces a bundle that launches correctly on the build
machine and dies instantly on a friend's Mac, so it carries an explicit
verification step in W13.

**Credentials never enter the repository.** Team ID, signing identity, and the
notarytool app-specific password or API key live in Koji's Keychain and are read
by the script at build time.

Notarization must be re-run for every build that is handed out.

### W9 · Phase B — Install guide

One page in ja / zh-TW / en. With a notarized build this is roughly four lines:
download the `.dmg`, drag to Applications, double-click, paste the key you were
given.

### W10 · Phase A — Model selection that survives model retirement

The distributed build has no auto-update, so a hardcoded model string is a
latent outage: on the day that model is retired, every install fails at once and
the only repair is redistributing a `.dmg`. Three pieces:

1. **Live picker.** Settings shows a "Scan model" control populated by
   `client.models.list()` using the friend's own key, so the list is always
   whatever their account can actually reach. Default `claude-sonnet-5`.
2. **Estimated cost per card beside each option.** Computed from the token
   figures in §8 and the model's own pricing tier. Without this, the picker
   invites an expensive mistake — see the warning below.
3. **Graceful failure.** When a scan fails because the configured model is
   unavailable, catch it specifically and route the user to the picker with a
   plain-language explanation, rather than surfacing a raw API error.

**Cost warning to encode.** `app/services/claude_parser.py:284` turns on
adaptive thinking with `if "opus" in settings.claude_model`. Selecting any Opus
model therefore both moves to a higher price tier and adds thinking tokens — a
several-fold cost increase from one dropdown change. The string match is also
fragile against future model naming. Surface the estimated cost in the picker,
and treat that `in` test as something to make explicit rather than inherit.

### W11 · Phase A — Backup and restore

Local-only storage is the right privacy answer, but combined with "retrieval is
the primary value" it means a friend's entire collection sits on one laptop with
no copy, and a non-technical user will not have Time Machine configured.

- **Back up:** write a zip of `~/.nxt-a1/` — `meishi.db` plus the `images/`
  tree — to a folder the user chooses (Desktop or iCloud Drive being the
  obvious targets). Exclude `temp/`.
- **Restore:** read such a zip back. Restore **replaces** the current
  collection rather than merging, and must say so unambiguously before
  proceeding — merge semantics are a different and much larger feature.
- Respect the DB-commit-before-filesystem convention already used elsewhere in
  the project, and validate the archive before destroying anything.

No cloud service, no new dependency, no change to the privacy story.

### W12 · Phase A — Search breadth and pagination

Both halves of this are blockers under §1, not enhancements.

**Pagination.** `app/routers/v2/persons.py:133` caps results at 50 by default
and 200 maximum, and `CollectionPage` fetches `listCards({limit: 200})` then
filters client-side. A friend who scans a genuine shoebox of 300+ cards silently
cannot reach most of their own collection. Remove the ceiling for browsing —
paginate or virtualize the list — and make sure search is not bounded by a page
of results either.

**Breadth.** `persons.py:141-147` matches only current person names and current
organization names. Extend the same union-of-`person_id` approach to cover:

| Field | Path |
|---|---|
| Job title, department | `Position` → `PositionDetail.title` / `.department` |
| Email, phone, address, URL, social | `ContactDetail.value` and `.label` — one clause covers all of them, since every type shares the column |
| Free-text notes | `Person.notes` |
| Occasion | `Card.occasion_id` → `Occasion.name` / `.location` / `.notes`, joined back to the person via the card |

Occasion is the one to get right: *"the person from the Rotary dinner in March"*
is the query this audience actually has, the data already exists, and today it
is unreachable.

Start with `ILIKE` clauses matching the existing style. Only introduce SQLite
FTS if measurement on a realistic collection shows it is needed — a few hundred
rows on local SQLite will not need it, and FTS brings tokenizer problems with
CJK text that are not worth inheriting unprompted.

### W13 · Phase B — Clean-room verification

None of the above is provable in Koji's development tree, where Python,
dependencies, `.env`, and a populated `~/.nxt-a1/` all already exist.

The realistic clean room is **a fresh macOS user account** — new home directory,
no `~/.nxt-a1`, no development tools on `PATH`, and a `.dmg` downloaded through
a browser so that quarantine genuinely applies. It costs nothing and needs no VM.

Verification checklist:

- The `.dmg` opens and the app launches with no Gatekeeper prompt of any kind
- The Hardened Runtime does not kill the process at opencv or numpy import
- Migrations run and create a fresh `~/.nxt-a1/meishi.db`
- The setup wizard appears, rejects a deliberately malformed key, and accepts a
  valid one
- A scan completes end to end using a key from a freshly created Anthropic
  account
- A revoked or exhausted key produces the plain-language message from W7, in
  the selected UI language — not a raw API error
- Odoo and Google Contacts are absent from the UI, not present-and-failing
- The scan runs on `claude-sonnet-5`, confirmed from `config.json` rather than
  assumed from the default
- vCard export produces a file that imports into macOS Contacts with CJK names
  intact
- Quitting the app stops the backend and frees the port
- A collection of 300+ cards browses fully — nothing is cut off at 50 or 200
  (W12)
- Search finds a person by job title, by email fragment, by phone fragment, by a
  word from their notes, and by occasion name (W12)
- The Google Contacts button is present and opens the informational dialog in
  the selected language; Odoo is absent entirely (W5)
- The Settings model picker lists models from the friend's own account, and a
  deliberately invalid `claude_model` in `config.json` routes to the picker with
  a readable message rather than an API error (W10)
- Backup produces a zip that, restored into a fresh install, reproduces the
  collection including card images (W11)

## 8. Operations — the setup session

Not app code. A procedure Koji runs with each friend, in person, once. It is
also the Claude introduction, so it is worth doing unhurried.

1. Create the friend's Anthropic account at console.anthropic.com
2. Create an organisation and add a payment method
3. Purchase an initial credit balance
4. Create an API key
5. Install the `.dmg`, run the setup wizard, paste the key
6. Scan two or three of their own cards together, and export a `.vcf` into
   their Contacts so they have seen the whole loop work

### What a scan actually costs

Measured against the app's real `SYSTEM_PROMPT`, `_RULES`, and `_SCHEMA` with an
image at the app's own `MAX_DIMENSION` of 1568px. Input tokens are measured via
`messages.count_tokens`; output is assumed at roughly 600 tokens, which is the
one figure not measured.

| Model | Input tokens, 1 side | Input tokens, 2 sides | Cost per 2-sided card | Cards per US$5 |
|---|---|---|---|---|
| `claude-sonnet-4-6` (today's pin) | 2,673 | 4,243 | ~2.2¢ | ~230 |
| **`claude-sonnet-5` (chosen)** | 3,210 | 5,013 | **~1.6¢** | **~310** |
| `claude-haiku-4-5` | 2,672 | 4,242 | ~0.7¢ | ~690 |

The sentence to say out loud during setup: **about US$5 covers a 300-card
shoebox.** Confirm Anthropic's current minimum credit purchase during the first
session; it is not recorded here because it was not verified.

A fresh install has no `FieldCorrection` rows, so the few-shot block is empty
and these are floor numbers. Prompt size grows as a friend corrects fields.

## 9. Costs

| Item | Who pays | Cost | Notes |
|---|---|---|---|
| Apple Developer Program | Koji | **US$99/year, recurring** | Required for Developer ID signing and notarization. Ties the build to Koji's Apple ID. The only recurring cost in the project |
| Claude API usage | Each friend | ~1.6–2.2¢ per card | Their own account, their own prepaid credit. Koji's exposure is zero |
| Hosting | — | $0 | There is none |
| Distribution | Koji | $0 | Google Drive link |

Koji's total ongoing cost is therefore US$99/year, independent of how many
friends install it or how many cards they scan.

## 10. Risks

- **Hardened Runtime versus PyInstaller** is the most likely source of a
  "works on my machine" failure. Mitigated by W13.
- **Bundle size** of roughly 150 MB may be awkward over a slow connection.
  Acceptable for now.
- **No auto-update.** A bug fix means Koji redistributes a `.dmg` and asks
  people to reinstall. Acceptable at community scale; revisit if the population
  grows. The sharpest consequence of this — a retired model bricking every
  install simultaneously — is addressed by W10; other bugs remain manual.
- **Restore is destructive.** W11 replaces rather than merges. A friend who
  restores an old backup over a newer collection loses the difference. The
  confirmation copy is the only guard, so it has to be unambiguous.
- **Friends may want Google Contacts sync** once they see vCard export is
  manual. The answer for v1 is no; the path forward would be OAuth plus Google
  verification, a project of its own.
- **The audience was never asked.** This design assumes Rotary friends want the
  whole collection app because Koji does. Worth validating with two or three
  people before building all of it.

## 11. Deferred

- Windows support
- Google Contacts OAuth and app verification
- Auto-update, e.g. Sparkle — notarized signing makes this cleanly possible later
- Any form of sharing contacts between installs
