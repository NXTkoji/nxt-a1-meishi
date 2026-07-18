# Google Contacts Auto-Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Set up a dedicated worktree/branch (e.g. `.worktrees/google-contacts-auto-sync` on `feat/google-contacts-auto-sync`) before starting — see superpowers:using-git-worktrees.

**Goal:** Automatically push every scanned or edited business card to Google Contacts (replacing the current manual-only "Export" button trigger), with a new "Met As" custom field capturing which of Koji's businesses the contact was met through — so the native iOS/macOS Contacts app can serve as a free, zero-maintenance, read-mostly viewer on the phone. This replaces the custom SwiftUI app plan (`docs/superpowers/plans/2026-07-17-iphone-viewer-app.md`, now superseded).

**Architecture:** The Google Contacts push logic already exists in `app/services/google_contacts.py` but has never actually run — `GOOGLE_REFRESH_TOKEN` was empty in `.env` until this session, and the only call site is a manual `/api/v2/export` endpoint. This plan does three things: (1) fixes a real bug where the existing "legacy card" bridge silently drops `received_date`, `notes`, and never carried "which company" data at all — despite `MyCompany.google_label` already existing in the schema for exactly this purpose; (2) extracts the Google-Contacts-sync-and-record logic into a small reusable service (`app/services/contact_sync.py`) so both the manual export endpoint and new automatic triggers share one code path; (3) wires that service into FastAPI `BackgroundTasks` on card creation (scan confirm) and card update, so every save pushes to Google Contacts without blocking the request — no periodic loop needed, since this is one-way (push only), not the bidirectional eventually-consistent design the superseded iPhone-app plan required.

**Tech Stack:** Existing FastAPI/SQLAlchemy backend, existing `httpx`-based People API client (`google_contacts.py`), FastAPI `BackgroundTasks` (already used in `sessions.py` for temp-file cleanup — same pattern, new use), pytest with the existing `client_with_test_db` fixture.

**Known limitation (out of scope for this plan):** Editing a *person's* fields directly (name, title, phone, etc. via the `persons.py` PATCH endpoints) does not itself trigger a re-sync — only touching a *card* does (create or update). Since `build_legacy_card` always reads live data at sync time, the data won't be wrong, just possibly stale until the next time any of that person's cards is touched. Only 3 of the 174 people currently in the database have more than one card, so this is a narrow edge case. If it becomes annoying in practice, a follow-up plan can add sync triggers to the person-editing endpoints too.

---

## File Structure

**New files:**
- `app/services/legacy_card.py` — extracted + fixed `build_legacy_card()` (was a private function inside `export.py`)
- `app/services/contact_sync.py` — `sync_card_to_google_contacts()` (shared push+record logic) and `auto_sync_card()` (background-task entry point with its own DB session)
- `tests/test_legacy_card.py`
- `tests/test_google_contacts.py`
- `tests/test_contact_sync.py`
- `tests/test_cards_auto_sync.py`

**Modified files:**
- `app/models/card.py` — add `my_company_labels` field to the legacy `Card` model
- `app/services/google_contacts.py` — add "Met As" custom field to the People API request body
- `app/routers/v2/export.py` — remove local `_build_legacy_card`, import the extracted version; refactor the `google_contacts` branch of `_export_one` to call the shared service
- `app/routers/v2/sessions.py` — schedule `auto_sync_card` after each card is confirmed
- `app/routers/v2/cards.py` — schedule `auto_sync_card` after a card is updated
- `tests/conftest.py` — expose the test DB's session maker on `client_with_test_db`, so tests can point `contact_sync.AsyncSessionLocal` at the isolated test database instead of the real one

---

## Task 1: Fix and extract the legacy Card builder

**Files:**
- Modify: `app/models/card.py`
- Create: `app/services/legacy_card.py`
- Modify: `app/routers/v2/export.py`
- Create: `tests/test_legacy_card.py`

**Context:** `app/routers/v2/export.py` currently has a private `_build_legacy_card()` function that converts v2 SQLAlchemy objects into the older `app/models/card.py` Pydantic shape that both `odoo_sync.py` and `google_contacts.py` consume. It has a real bug: the final line is `return LegacyCard(person=legacy_person)` — it never sets `received_date`, `notes`, or any "which company" data on the returned object, even though `google_contacts.py`'s `_build_person_body()` reads `card.received_date` and `card.notes` to build the Google Contact's notes field. That data has silently never made it into Google Contacts. Separately, `MyCompany` already has a `google_label` column (seeded with values like `"NXT"`, `"Personal"` in `scripts/seed_my_companies.py`) that has never been consumed anywhere — it exists for exactly the "Met As" use case this plan needs.

- [x] **Step 1: Add `my_company_labels` to the legacy Card model**

In `app/models/card.py`, add a field to the `Card` class (it currently has `id`, `scanned_at`, `received_date`, `notes`, `images`, `person`, `match`):

```python
class Card(BaseModel):
    id: str = ""
    scanned_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    received_date: str = ""  # date card was received (user can override)
    notes: str = ""
    my_company_labels: list[str] = Field(default_factory=list)
    images: CardImages = Field(default_factory=CardImages)
    person: Person = Field(default_factory=Person)
    match: MatchResult = Field(default_factory=MatchResult)
```

- [x] **Step 2: Write the failing test**

Create `tests/test_legacy_card.py`:

```python
import asyncio
from datetime import date

from app.db.session import get_db
from app.main import app


def test_build_legacy_card_carries_notes_date_and_met_as(client_with_test_db):
    from app.db.models import Card, CardMyCompany, MyCompany, Person
    from app.services.legacy_card import build_legacy_card

    async def _run():
        async for db in app.dependency_overrides[get_db]():
            person = Person(external_id="p1")
            db.add(person)
            await db.flush()

            nxt = MyCompany(name="NXT株式会社", google_label="NXT")
            unlabeled = MyCompany(name="正康有限公司")
            db.add_all([nxt, unlabeled])
            await db.flush()

            card = Card(
                external_id="c1",
                person_id=person.id,
                received_date=date(2026, 7, 18),
                notes="Met at trade show",
            )
            db.add(card)
            await db.flush()
            db.add(CardMyCompany(card_id=card.id, my_company_id=nxt.id))
            db.add(CardMyCompany(card_id=card.id, my_company_id=unlabeled.id))
            await db.flush()
            await db.refresh(card, attribute_names=["my_company_links"])
            for link in card.my_company_links:
                await db.refresh(link, attribute_names=["my_company"])

            legacy = build_legacy_card(card, person, [], [])

            assert legacy.received_date == "2026-07-18"
            assert legacy.notes == "Met at trade show"
            assert sorted(legacy.my_company_labels) == ["NXT", "正康有限公司"]
            break

    asyncio.run(_run())
```

- [x] **Step 3: Run test to verify it fails**

Run: `venv/bin/python3 -m pytest tests/test_legacy_card.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.legacy_card'`

- [x] **Step 4: Extract and fix `build_legacy_card`**

Create `app/services/legacy_card.py`:

```python
"""Build the legacy app.models.card.Card Pydantic model from v2 database
objects, for handing off to the Odoo and Google Contacts sync services
(both still speak the pre-v2 Card/Person shape).
"""
from __future__ import annotations

from app.db.models import Card as DBCard, ContactDetail, Person, Position


def build_legacy_card(
    db_card: DBCard,
    person: Person,
    contact_details: list[ContactDetail],
    positions: list[Position],
):
    from app.models.card import (
        Address,
        Card as LegacyCard,
        Email,
        Person as LegacyPerson,
        PersonName as LegacyName,
        Phone,
        Position as LegacyPosition,
        Social,
    )

    names = [
        LegacyName(value=n.full_name, language=n.language, type=n.name_type)
        for n in person.names
        if n.is_current
    ]

    legacy_positions = []
    for pos in positions:
        org_name_ja = next(
            (on.name for on in pos.organization.names if on.language == "ja" and on.is_current),
            next((on.name for on in pos.organization.names if on.is_current), ""),
        )
        org_name_en = next(
            (on.name for on in pos.organization.names if on.language == "en" and on.is_current),
            "",
        )
        title_ja = next((pd.title or "" for pd in pos.details if pd.language == "ja"), "")
        title_en = next((pd.title or "" for pd in pos.details if pd.language == "en"), "")
        dept = next(
            (pd.department or "" for pd in pos.details if pd.language == "ja"),
            next((pd.department or "" for pd in pos.details), ""),
        )
        legacy_positions.append(LegacyPosition(
            company=org_name_ja,
            company_english=org_name_en,
            title=title_ja,
            title_english=title_en,
            department=dept,
        ))

    phones, emails, addresses = [], [], []
    website = ""
    social = Social()
    for cd in contact_details:
        t = cd.detail_type
        if t in ("phone_work", "phone_mobile", "phone_fax"):
            kind = t.replace("phone_", "")
            phones.append(Phone(value=cd.value, type=kind, label=cd.label or ""))
        elif t in ("email_work", "email_personal"):
            kind = t.replace("email_", "")
            emails.append(Email(value=cd.value, type=kind))
        elif t in ("address_work", "address_home"):
            kind = t.replace("address_", "")
            addresses.append(Address(type=kind, full=cd.value))
        elif t == "url_website":
            website = cd.value
        elif t == "social_wechat":
            social.wechat = cd.value
        elif t == "social_line":
            social.line = cd.value
        elif t == "social_linkedin":
            social.linkedin = cd.value

    legacy_person = LegacyPerson(
        names=names,
        positions=legacy_positions,
        phones=phones,
        emails=emails,
        addresses=addresses,
        website=website,
        social=social,
    )

    my_company_labels = [
        link.my_company.google_label or link.my_company.name
        for link in db_card.my_company_links
    ]

    return LegacyCard(
        person=legacy_person,
        received_date=str(db_card.received_date) if db_card.received_date else "",
        notes=db_card.notes or "",
        my_company_labels=my_company_labels,
    )
```

- [x] **Step 5: Update `export.py` to use the extracted function**

In `app/routers/v2/export.py`:

1. Delete the entire `_build_legacy_card` function definition (currently spans from `def _build_legacy_card(` down to its `return LegacyCard(person=legacy_person)`).
2. Add near the top imports: `from app.services.legacy_card import build_legacy_card`
3. Add `CardMyCompany` to the existing `from app.db.models import (...)` block.
4. Update the call site inside `_export_one`: change `legacy = _build_legacy_card(card, person, contact_details, positions)` to `legacy = build_legacy_card(card, person, contact_details, positions)`.
5. In `_load_full_card`, add eager loading for the my-company links (needed because `build_legacy_card` now reads `db_card.my_company_links`), so the `.options(...)` block becomes:

```python
    return await db.scalar(
        select(Card)
        .where(Card.external_id == card_ext_id, Card.deleted_at.is_(None))
        .options(
            selectinload(Card.sides),
            selectinload(Card.person).selectinload(Person.names),
            selectinload(Card.person).selectinload(Person.contact_details),
            selectinload(Card.person).selectinload(Person.positions)
                .selectinload(Position.details),
            selectinload(Card.person).selectinload(Person.positions)
                .selectinload(Position.organization)
                .selectinload(Organization.names),
            selectinload(Card.my_company_links).selectinload(CardMyCompany.my_company),
        )
    )
```

- [x] **Step 6: Run test to verify it passes**

Run: `venv/bin/python3 -m pytest tests/test_legacy_card.py -v`
Expected: PASS (1 test)

- [x] **Step 7: Run the full suite to check for regressions**

Run: `venv/bin/python3 -m pytest -q`
Expected: all existing tests still pass (no test currently covers `export.py` directly, so this just confirms nothing else broke on import).

- [x] **Step 8: Commit**

```bash
git add app/models/card.py app/services/legacy_card.py app/routers/v2/export.py tests/test_legacy_card.py
git commit -m "fix: carry received_date/notes/met-as into legacy card, extract builder"
```

---

## Task 2: Add "Met As" custom field to the Google Contacts push

**Files:**
- Modify: `app/services/google_contacts.py`
- Create: `tests/test_google_contacts.py`

**Context:** `_build_person_body()` in `google_contacts.py` builds the People API request body from a legacy `Card`. It already builds a `biographies` note from `card.received_date`/`card.notes`. This task adds a `userDefined` (custom field) entry from the new `card.my_company_labels`, which Google Contacts displays as a labeled custom field.

- [x] **Step 1: Write the failing test**

Create `tests/test_google_contacts.py`:

```python
from app.models.card import Card as LegacyCard, Person as LegacyPerson
from app.services.google_contacts import _build_person_body


def test_build_person_body_adds_met_as_custom_field():
    card = LegacyCard(
        person=LegacyPerson(),
        my_company_labels=["NXT", "正康有限公司"],
    )

    body = _build_person_body(card)

    assert body["userDefined"] == [{"key": "Met As", "value": "NXT, 正康有限公司"}]


def test_build_person_body_omits_met_as_when_empty():
    card = LegacyCard(person=LegacyPerson())

    body = _build_person_body(card)

    assert "userDefined" not in body


def test_build_person_body_dedupes_and_sorts_met_as():
    card = LegacyCard(
        person=LegacyPerson(),
        my_company_labels=["NXT", "NXT", "Personal"],
    )

    body = _build_person_body(card)

    assert body["userDefined"] == [{"key": "Met As", "value": "NXT, Personal"}]
```

- [x] **Step 2: Run test to verify it fails**

Run: `venv/bin/python3 -m pytest tests/test_google_contacts.py -v`
Expected: FAIL — `KeyError: 'userDefined'` on the first test (field doesn't exist yet).

- [x] **Step 3: Add the "Met As" field**

In `app/services/google_contacts.py`, in `_build_person_body`, right before `return body`:

```python
    # Met As (which of the user's businesses this contact was met through)
    if card.my_company_labels:
        labels = sorted({label for label in card.my_company_labels if label})
        if labels:
            body["userDefined"] = [{"key": "Met As", "value": ", ".join(labels)}]

    return body
```

- [x] **Step 4: Run test to verify it passes**

Run: `venv/bin/python3 -m pytest tests/test_google_contacts.py -v`
Expected: PASS (3 tests)

- [x] **Step 5: Commit**

```bash
git add app/services/google_contacts.py tests/test_google_contacts.py
git commit -m "feat: add Met As custom field to Google Contacts sync"
```

---

## Task 3: Shared sync-and-record service + background-task entry point

**Files:**
- Create: `app/services/contact_sync.py`
- Modify: `app/routers/v2/export.py`
- Modify: `tests/conftest.py`
- Create: `tests/test_contact_sync.py`

**Context:** Today, `_export_one` in `export.py` has the Google-Contacts-push-and-record logic inline inside an `elif destination == "google_contacts":` branch. This task extracts that into `sync_card_to_google_contacts()` (pure logic, takes an already-open `db` session) so it can be reused by a new `auto_sync_card(card_id)` background-task entry point, which opens its **own** DB session — necessary because background tasks run after the triggering HTTP request has already returned its response, by which point the request's `db` session may already be closed.

- [x] **Step 1: Expose the test DB's session maker for background-task testing**

In `tests/conftest.py`, inside `client_with_test_db`, right after `test_client = TestClient(app)`, add one line so tests can point a service's module-level `AsyncSessionLocal` at the isolated test database:

```python
    app.dependency_overrides[get_db] = _override_get_db
    test_client = TestClient(app)
    test_client.session_maker = session_maker
```

- [x] **Step 2: Write the failing test for `sync_card_to_google_contacts`**

Create `tests/test_contact_sync.py`:

```python
import asyncio

import httpx

from app.db.session import get_db
from app.main import app


def _mock_google(monkeypatch, resource_name="people/c123"):
    async def fake_post(self, url, **kwargs):
        if "oauth2.googleapis.com" in url:
            return httpx.Response(200, json={"access_token": "atoken"}, request=httpx.Request("POST", url))
        return httpx.Response(200, json={"resourceName": resource_name}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    from app.config import settings
    monkeypatch.setattr(settings, "google_client_id", "cid")
    monkeypatch.setattr(settings, "google_client_secret", "csecret")
    monkeypatch.setattr(settings, "google_refresh_token", "rtoken")


def test_sync_card_to_google_contacts_creates_and_updates_person(client_with_test_db, monkeypatch):
    _mock_google(monkeypatch)

    from app.db.models import Card, Person
    from app.models.card import Card as LegacyCard, Person as LegacyPerson
    from app.services.contact_sync import sync_card_to_google_contacts

    async def _run():
        async for db in app.dependency_overrides[get_db]():
            person = Person(external_id="p1")
            db.add(person)
            await db.flush()
            card = Card(external_id="c1", person_id=person.id, sync_status="pending")
            db.add(card)
            await db.flush()
            card.person = person

            legacy = LegacyCard(person=LegacyPerson())
            result, error = await sync_card_to_google_contacts(db, card, legacy)

            assert result == "created"
            assert error is None
            assert person.google_resource == "people/c123"
            assert card.google_sync_at is not None
            break

    asyncio.run(_run())


def test_sync_card_to_google_contacts_reports_error(client_with_test_db, monkeypatch):
    async def fake_post(self, url, **kwargs):
        return httpx.Response(500, text="boom", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    from app.config import settings
    monkeypatch.setattr(settings, "google_client_id", "cid")
    monkeypatch.setattr(settings, "google_client_secret", "csecret")
    monkeypatch.setattr(settings, "google_refresh_token", "rtoken")

    from app.db.models import Card, Person
    from app.models.card import Card as LegacyCard, Person as LegacyPerson
    from app.services.contact_sync import sync_card_to_google_contacts

    async def _run():
        async for db in app.dependency_overrides[get_db]():
            person = Person(external_id="p2")
            db.add(person)
            await db.flush()
            card = Card(external_id="c2", person_id=person.id, sync_status="pending")
            db.add(card)
            await db.flush()
            card.person = person

            legacy = LegacyCard(person=LegacyPerson())
            result, error = await sync_card_to_google_contacts(db, card, legacy)

            assert result == "error"
            assert error is not None
            assert person.google_resource is None
            break

    asyncio.run(_run())


def test_auto_sync_card_records_history(client_with_test_db, monkeypatch):
    _mock_google(monkeypatch, resource_name="people/c999")

    from app.services import contact_sync
    monkeypatch.setattr(contact_sync, "AsyncSessionLocal", client_with_test_db.session_maker)

    from app.db.models import Card, CardSyncHistory, Person
    from sqlalchemy import select

    card_id_holder = {}

    async def _setup():
        async for db in app.dependency_overrides[get_db]():
            person = Person(external_id="p3")
            db.add(person)
            await db.flush()
            card = Card(external_id="c3", person_id=person.id, sync_status="pending")
            db.add(card)
            await db.flush()
            card_id_holder["id"] = card.id
            break

    asyncio.run(_setup())
    asyncio.run(contact_sync.auto_sync_card(card_id_holder["id"]))

    async def _check():
        async for db in app.dependency_overrides[get_db]():
            person = await db.scalar(select(Person).where(Person.external_id == "p3"))
            assert person.google_resource == "people/c999"
            history = (await db.scalars(select(CardSyncHistory))).all()
            assert len(history) == 1
            assert history[0].destination == "google_contacts"
            assert history[0].result == "created"
            break

    asyncio.run(_check())
```

- [x] **Step 3: Run tests to verify they fail**

Run: `venv/bin/python3 -m pytest tests/test_contact_sync.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.contact_sync'`

- [x] **Step 4: Implement `contact_sync.py`**

Create `app/services/contact_sync.py`:

```python
"""Orchestrates pushing one card to Google Contacts and recording the
result. Used both by the manual /api/v2/export endpoint and by the
automatic background-task triggers on card create/update.
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.engine import AsyncSessionLocal
from app.db.models import Card, CardMyCompany, CardSyncHistory, Organization, Person, Position
from app.services.google_contacts import sync_to_google
from app.services.legacy_card import build_legacy_card

logger = logging.getLogger(__name__)


async def sync_card_to_google_contacts(db: AsyncSession, card: Card, legacy) -> tuple[str, str | None]:
    """Push one card's data to Google Contacts. Returns (result, error_message).
    Does not commit — the caller is responsible for committing and recording
    CardSyncHistory, since callers differ in what else they commit alongside it.
    """
    person = card.person
    existing_resource = person.google_resource
    try:
        resource_name = await sync_to_google(legacy, existing_resource)
    except Exception as exc:
        logger.exception("Google Contacts sync failed for card %s", card.external_id)
        return "error", str(exc)
    if resource_name:
        result = "updated" if existing_resource else "created"
        person.google_resource = resource_name
        card.google_sync_at = datetime.utcnow()
        return result, None
    return "error", "sync_to_google returned None"


async def auto_sync_card(card_id: int) -> None:
    """Background-task entry point: push one card to Google Contacts.

    Opens its own DB session — this runs after the request that scheduled
    it has already returned its response, so the request's session may
    already be closed by then.
    """
    async with AsyncSessionLocal() as db:
        card = await db.scalar(
            select(Card)
            .where(Card.id == card_id)
            .options(
                selectinload(Card.person).selectinload(Person.names),
                selectinload(Card.person).selectinload(Person.contact_details),
                selectinload(Card.person).selectinload(Person.positions)
                    .selectinload(Position.details),
                selectinload(Card.person).selectinload(Person.positions)
                    .selectinload(Position.organization)
                    .selectinload(Organization.names),
                selectinload(Card.my_company_links).selectinload(CardMyCompany.my_company),
            )
        )
        if card is None or card.deleted_at is not None:
            logger.warning("auto_sync_card: card id=%s not found or deleted", card_id)
            return

        legacy = build_legacy_card(card, card.person, card.person.contact_details, card.person.positions)
        result, error_message = await sync_card_to_google_contacts(db, card, legacy)

        db.add(CardSyncHistory(
            card_id=card.id,
            destination="google_contacts",
            result=result,
            error_message=error_message,
        ))
        await db.commit()
```

- [x] **Step 5: Refactor `export.py` to use the shared function**

In `app/routers/v2/export.py`, replace the inline `elif destination == "google_contacts":` block inside `_export_one` (currently calls `sync_to_google` directly and updates `person.google_resource`/`card.google_sync_at` inline) with:

```python
        elif destination == "google_contacts":
            from app.services.contact_sync import sync_card_to_google_contacts
            result, error_message = await sync_card_to_google_contacts(db, card, legacy)
```

- [x] **Step 6: Run tests to verify they pass**

Run: `venv/bin/python3 -m pytest tests/test_contact_sync.py -v`
Expected: PASS (3 tests)

- [x] **Step 7: Run the full suite to check for regressions**

Run: `venv/bin/python3 -m pytest -q`
Expected: all tests pass.

- [x] **Step 8: Commit**

```bash
git add app/services/contact_sync.py app/routers/v2/export.py tests/conftest.py tests/test_contact_sync.py
git commit -m "refactor: extract shared Google Contacts sync-and-record service"
```

---

## Task 4: Auto-sync on card creation and card update

**Files:**
- Modify: `app/routers/v2/sessions.py`
- Modify: `app/routers/v2/cards.py`
- Create: `tests/test_cards_auto_sync.py`

**Context:** `sync_to_google` already no-ops gracefully (logs a warning and returns `None`) when `GOOGLE_REFRESH_TOKEN` isn't set, so scheduling this unconditionally is safe even before you've finished the OAuth setup from earlier this session. `confirm_session` (in `sessions.py`) already takes a `background_tasks: BackgroundTasks` parameter (used today for temp-file cleanup) — this task adds one more scheduled call alongside it. `update_card` (in `cards.py`, the `PATCH /{card_ext_id}` endpoint) doesn't currently take `BackgroundTasks` at all — this task adds it.

- [x] **Step 1: Schedule auto-sync after card confirmation**

In `app/routers/v2/sessions.py`, add near the top imports:

```python
from app.services.contact_sync import auto_sync_card
```

In `confirm_session`, change:

```python
    background_tasks.add_task(image_store.delete_temp_session, sid)
    return ConfirmResponse(confirmed=confirmed)
```

to:

```python
    background_tasks.add_task(image_store.delete_temp_session, sid)
    for result in confirmed:
        background_tasks.add_task(auto_sync_card, result.card_id)
    return ConfirmResponse(confirmed=confirmed)
```

- [x] **Step 2: Write the failing test for the update-card trigger**

Create `tests/test_cards_auto_sync.py`:

```python
import asyncio

import httpx

from app.db.session import get_db
from app.main import app


def test_update_card_triggers_google_contacts_sync(client_with_test_db, monkeypatch):
    async def fake_post(self, url, **kwargs):
        if "oauth2.googleapis.com" in url:
            return httpx.Response(200, json={"access_token": "atoken"}, request=httpx.Request("POST", url))
        return httpx.Response(200, json={"resourceName": "people/c777"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    from app.config import settings
    monkeypatch.setattr(settings, "google_client_id", "cid")
    monkeypatch.setattr(settings, "google_client_secret", "csecret")
    monkeypatch.setattr(settings, "google_refresh_token", "rtoken")

    from app.services import contact_sync
    monkeypatch.setattr(contact_sync, "AsyncSessionLocal", client_with_test_db.session_maker)

    from app.db.models import Card, Person
    from sqlalchemy import select

    card_ext_id_holder = {}

    async def _setup():
        async for db in app.dependency_overrides[get_db]():
            person = Person(external_id="p1")
            db.add(person)
            await db.flush()
            card = Card(external_id="c1", person_id=person.id, sync_status="pending")
            db.add(card)
            await db.flush()
            card_ext_id_holder["id"] = card.external_id
            break

    asyncio.run(_setup())

    resp = client_with_test_db.patch(
        f"/api/v2/cards/{card_ext_id_holder['id']}",
        json={"notes": "updated notes"},
    )
    assert resp.status_code == 200

    async def _check():
        async for db in app.dependency_overrides[get_db]():
            person = await db.scalar(select(Person).where(Person.external_id == "p1"))
            assert person.google_resource == "people/c777"
            break

    asyncio.run(_check())
```

- [x] **Step 3: Run test to verify it fails**

Run: `venv/bin/python3 -m pytest tests/test_cards_auto_sync.py -v`
Expected: FAIL — `person.google_resource` is `None` (nothing schedules the sync yet).

- [x] **Step 4: Schedule auto-sync after card update**

In `app/routers/v2/cards.py`:

1. Change the import line `from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile` to add `BackgroundTasks`:

```python
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
```

2. Add near the top: `from app.services.contact_sync import auto_sync_card`

3. Update `update_card`'s signature and add the scheduled call before its `return`:

```python
@router.patch("/{card_ext_id}", response_model=CardOut)
async def update_card(
    card_ext_id: str,
    body: dict,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    from datetime import date as date_type
    card = await _load_card(db, card_ext_id)
    if "received_date" in body:
        val = body["received_date"]
        card.received_date = date_type.fromisoformat(val) if val else None
    if "notes" in body:
        card.notes = body["notes"]
    if "display_name_language" in body:
        val = body["display_name_language"]
        card.display_name_language = val if val else None
    if "occasion_id" in body:
        card.occasion_id = body["occasion_id"] or None
    if "my_company_ids" in body:
        from sqlalchemy import delete as sa_delete
        await db.execute(sa_delete(CardMyCompany).where(CardMyCompany.card_id == card.id))
        for mc_id in body["my_company_ids"]:
            db.add(CardMyCompany(card_id=card.id, my_company_id=mc_id))
    await db.flush()
    person_ext_id = await db.scalar(select(Person.external_id).where(Person.id == card.person_id))
    mc_ids = (await db.execute(
        select(CardMyCompany.my_company_id).where(CardMyCompany.card_id == card.id)
    )).scalars().all()
    out = CardOut.model_validate(card)
    out.person_external_id = person_ext_id
    out.my_company_ids = list(mc_ids)
    background_tasks.add_task(auto_sync_card, card.id)
    return out
```

- [x] **Step 5: Run test to verify it passes**

Run: `venv/bin/python3 -m pytest tests/test_cards_auto_sync.py -v`
Expected: PASS (1 test)

- [x] **Step 6: Run the full suite to check for regressions**

Run: `venv/bin/python3 -m pytest -q`
Expected: all tests pass.

- [x] **Step 7: Commit**

```bash
git add app/routers/v2/sessions.py app/routers/v2/cards.py tests/test_cards_auto_sync.py
git commit -m "feat: auto-sync cards to Google Contacts on create and update"
```

**Note on `confirm_session` coverage:** this task does not add an automated test exercising the full scan-session → confirm flow (it requires a `ScanSession` + `CardDraft` + Claude-parsing setup that has no existing test fixture in this codebase, and building one is disproportionate to this plan). The `confirm_session` wiring is verified manually in Task 5 instead.

---

## Task 5: Manual QA against real Google Contacts

**Files:** none (verification only)

**Context:** This requires the Google OAuth setup from earlier this session to be finished — `GOOGLE_REFRESH_TOKEN` must be set in `.env` (see the "NXT-A1 Backend" OAuth client already created in the `nxt-a1-meishi` GCP project). Run `venv/bin/python3 scripts/get_drive_refresh_token.py` first if that's not done yet, and restart the backend (`./deploy.sh`) so it picks up the new `.env` value.

- [x] **Step 1: Verify a scanned card syncs automatically**

Scan a new business card through the web app as usual, tag it with a "Met As" company, and confirm it. Do **not** click the manual "Export" button. Within a few seconds, check [contacts.google.com](https://contacts.google.com) (or the iOS/macOS Contacts app, once your Google account is added there) for a new contact with the scanned name, org, phone/email, and a "Met As" custom field showing the company you tagged.

- [x] **Step 2: Verify editing a card re-syncs it**

Edit that card's notes or "Met As" company via the web app. Refresh Google Contacts and confirm the note/custom field updated on the same contact (not a duplicate).

**Found here:** the first edit-triggered re-sync failed with a 400 `INVALID_ARGUMENT` from Google's People API — `updateContact` requires the contact's current `etag` in the request body, which `sync_to_google` never fetched or sent. Contact creation worked fine (no prior etag needed); every subsequent edit failed. No mocked unit test caught this since they stub the HTTP layer. Fixed in `app/services/google_contacts.py` (commit `a97dcb5`) by fetching `personFields=metadata` before the update call; re-verified against the real API that the same request now succeeds and updates the same `resourceName`.

- [x] **Step 3: Verify the manual export path still works**

Use the existing "Export" button on a different card with `destination: google_contacts`. Confirm it still creates/updates a contact correctly (this exercises the Task 3 refactor of `_export_one`).

- [x] **Step 4: Check backend logs for sync errors**

```bash
tail -100 /tmp/nxt-a1-backend.log | grep -i "google\|sync"
```

Expected: no repeated errors. A single `Google Contacts not configured, skipping` line is fine if you haven't finished the OAuth setup yet — that's the graceful no-op, not a bug.

- [ ] **Step 5: Optionally backfill `google_label` for the remaining companies** (not done — optional)

Only "NXT株式会社" and "個人 (Koji)" currently have a `google_label` set — the other four (正康有限公司, Rotary, 康鑫建築集團, 台湾日本人会) will show their full name in the "Met As" field instead of a short label, which is a harmless fallback. If you'd like shorter labels, they can be set via the web app's company management UI (or a follow-up direct DB update) — not required for this plan to work.
