# Community Edition Phase A2 — Collection Value Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the collection findable, portable, and recoverable — the three things that have to be true if retrieval is the app's primary value.

**Architecture:** Search grows from two matched fields to seven by extending the existing union-of-`person_id` query, and the browse ceiling that silently truncates large collections is removed. A vCard formatter joins the existing CSV formatters behind the same `build_legacy_card` converter, so all three exports share one normalisation path. Backup zips the whole `~/.nxt-a1/` data directory — database and card images together — because a vCard is not a backup.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 (async), React 19, TanStack Query v5, pytest.

**Spec:** `docs/superpowers/specs/2026-09-08-community-edition-distribution-design.md` — Phase A items W6, W11, W12.

**Depends on:** Nothing in A1. These two plans can be built in either order, though A1's `capabilities` endpoint is where a vCard destination would naturally register if both are done.

---

## Context for the implementer

You have not seen this codebase. Read these first:

- `app/services/legacy_card.py` — `build_legacy_card()` converts v2 database rows into a normalised Pydantic `Card` (defined in `app/models/card.py`) with `person.names`, `.positions`, `.phones`, `.emails`, `.addresses`, `.website`, `.social`, `.birthday`. Odoo sync, Google sync, and both CSV exports all consume this shape. **The vCard exporter must too** — do not query the ORM directly from the formatter.
- `app/routers/v2/export.py:159` — `export_csv` shows the exact loader pattern to copy: split `card_ids`, call `_load_full_card`, call `build_legacy_card`, hand the list to a formatter, return a `Response` with a `Content-Disposition` header.
- `app/routers/v2/persons.py:130-155` — `list_persons`. Two `ILIKE` queries collect `person_id` values into a set, then one `select(Person).where(Person.id.in_(all_ids))`. Extending search means adding more collectors to that union.
- `app/config.py` — `settings.data_dir` (`~/.nxt-a1`), `settings.db_path`, `settings.images_path`, `settings.temp_path`.
- `tests/conftest.py` — the `client_with_test_db` fixture. Read its docstring before using it.

### Two operational facts

1. **Backend changes need a LaunchAgent reload:**
   ```bash
   launchctl unload ~/Library/LaunchAgents/co.nxta.nxt-a1-backend.plist && launchctl load ~/Library/LaunchAgents/co.nxta.nxt-a1-backend.plist
   ```
2. **Frontend edits need `cd frontend && npm run build`** then Cmd+Shift+R.

### A convention this codebase follows

Database commits happen *before* filesystem mutations: prepare in memory, commit, then write or delete files. Task 6's restore must respect this — it is the most destructive operation in the app.

---

## File structure

| File | Responsibility |
|---|---|
| `app/services/vcard_export.py` | **Create.** Format a list of legacy `Card` objects as a vCard 3.0 document. Pure function, no I/O. |
| `app/services/backup.py` | **Create.** Zip and restore `~/.nxt-a1/`. Sole owner of the archive format. |
| `app/routers/v2/export.py` | **Modify.** Add the `.vcf` endpoint. |
| `app/routers/v2/backup.py` | **Create.** Backup/restore endpoints, separate because restore is destructive and deserves its own file. |
| `app/routers/v2/persons.py` | **Modify.** Widen search, remove the browse ceiling. |
| `app/main.py` | **Modify.** Register the backup router. |
| `frontend/src/api/index.ts` | **Modify.** Pagination params on `listPersons`. |
| `frontend/src/api/backup.ts` | **Create.** Backup/restore client. |
| `frontend/src/pages/CollectionPage.tsx` | **Modify.** Stop capping at 200. |
| `frontend/src/pages/SettingsPage.tsx` | **Modify.** Backup and restore controls. |
| `frontend/src/components/ExportDestinationSelector.tsx` | **Modify.** Add vCard as a download destination. |
| `frontend/src/i18n.ts` | **Modify.** New strings, all three languages. |

---

## Task 1: vCard formatter

**Files:**
- Create: `app/services/vcard_export.py`
- Test: `tests/test_vcard_export.py`

**Why vCard 3.0 and not 4.0:** macOS Contacts is the target, and it handles 3.0 most reliably. 4.0's cleaner `BDAY` syntax is not worth an import that silently drops fields.

- [ ] **Step 1: Write the failing test**

```python
"""vCard 3.0 output. CJK names are the common case here, not an edge case."""
from app.models.card import (
    Address, Card, Email, Person, PersonName, Phone, Position, Social,
)
from app.services.vcard_export import format_vcard


def _card(**person_kwargs) -> Card:
    return Card(id="c1", person=Person(**person_kwargs))


def test_emits_one_vcard_per_person():
    out = format_vcard([_card(names=[PersonName(value="Alice Smith", language="en")]),
                        _card(names=[PersonName(value="Bob Jones", language="en")])])
    assert out.count("BEGIN:VCARD") == 2
    assert out.count("END:VCARD") == 2


def test_version_is_3_point_0():
    out = format_vcard([_card(names=[PersonName(value="Alice", language="en")])])
    assert "VERSION:3.0" in out


def test_cjk_name_survives_intact():
    out = format_vcard([_card(names=[PersonName(value="福原康兒", language="ja")])])
    assert "FN:福原康兒" in out


def test_family_and_given_names_populate_n_field():
    out = format_vcard([_card(names=[PersonName(value="Alice Smith", language="en")])],
                       family_given={"Alice Smith": ("Smith", "Alice")})
    assert "N:Smith;Alice;;;" in out


def test_multiple_phones_each_get_a_line():
    out = format_vcard([_card(
        names=[PersonName(value="Alice", language="en")],
        phones=[Phone(value="03-1234-5678", type="work"),
                Phone(value="090-1111-2222", type="mobile")],
    )])
    assert "TEL;TYPE=WORK,VOICE:03-1234-5678" in out
    assert "TEL;TYPE=CELL,VOICE:090-1111-2222" in out


def test_fax_uses_the_fax_type():
    out = format_vcard([_card(names=[PersonName(value="A", language="en")],
                              phones=[Phone(value="03-9999", type="fax")])])
    assert "TEL;TYPE=WORK,FAX:03-9999" in out


def test_emails_are_typed():
    out = format_vcard([_card(names=[PersonName(value="A", language="en")],
                              emails=[Email(value="a@example.com", type="work")])])
    assert "EMAIL;TYPE=INTERNET,WORK:a@example.com" in out


def test_org_carries_company_and_department():
    out = format_vcard([_card(names=[PersonName(value="A", language="en")],
                              positions=[Position(company="NXT", department="Sales", title="Director")])])
    assert "ORG:NXT;Sales" in out
    assert "TITLE:Director" in out


def test_semicolons_in_values_are_escaped():
    """An unescaped ; would split the field and corrupt the import."""
    out = format_vcard([_card(names=[PersonName(value="A;B", language="en")])])
    assert r"FN:A\;B" in out


def test_newlines_in_notes_become_escaped_n():
    out = format_vcard([Card(id="c1", notes="line one\nline two",
                             person=Person(names=[PersonName(value="A", language="en")]))])
    assert r"NOTE:line one\nline two" in out


def test_full_birthday_is_emitted_plainly():
    out = format_vcard([_card(names=[PersonName(value="A", language="en")], birthday="1980-05-17")])
    assert "BDAY:1980-05-17" in out


def test_year_unknown_birthday_uses_the_apple_omit_year_convention():
    """'--MM-DD' is vCard 4 syntax; 3.0 readers need the Apple form."""
    out = format_vcard([_card(names=[PersonName(value="A", language="en")], birthday="--05-17")])
    assert "BDAY;X-APPLE-OMIT-YEAR=1604:1604-05-17" in out


def test_address_fields_are_positional():
    out = format_vcard([_card(
        names=[PersonName(value="A", language="en")],
        addresses=[Address(type="work", street="1-2-3 Chuo", city="Taipei",
                           state="", postal_code="100", country="Taiwan")],
    )])
    assert "ADR;TYPE=WORK:;;1-2-3 Chuo;Taipei;;100;Taiwan" in out


def test_person_with_no_name_is_skipped_not_crashed():
    out = format_vcard([_card(names=[])])
    assert "BEGIN:VCARD" not in out


def test_lines_are_crlf_terminated():
    """RFC 2426 requires CRLF; some importers reject bare LF."""
    out = format_vcard([_card(names=[PersonName(value="A", language="en")])])
    assert "BEGIN:VCARD\r\n" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python3 -m pytest tests/test_vcard_export.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.vcard_export'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Format legacy Card objects as a vCard 3.0 document.

Version 3.0 rather than 4.0 because macOS Contacts — the destination that
matters here — handles it most reliably.

Pure formatting: takes the same normalised Card shape that build_legacy_card()
produces for the Odoo and Google exporters, and returns text. No database, no
filesystem.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

# Map our phone types onto vCard TYPE parameters.
_PHONE_TYPES = {
    "work": "WORK,VOICE",
    "mobile": "CELL,VOICE",
    "fax": "WORK,FAX",
}


def _escape(value: str) -> str:
    """Escape per RFC 2426 §2.4.2. Backslash first, or we double-escape."""
    return (
        value.replace("\\", "\\\\")
        .replace(";", r"\;")
        .replace(",", r"\,")
        .replace("\n", r"\n")
    )


def _line(name: str, value: str) -> str:
    return f"{name}:{value}\r\n"


def _birthday_line(birthday: str) -> str:
    """Full dates emit plainly; year-unknown uses Apple's 3.0 convention.

    Our storage uses the vCard 4 style '--MM-DD' for an unknown year, which a
    3.0 reader does not understand. Apple's convention is a sentinel year of
    1604 plus X-APPLE-OMIT-YEAR.
    """
    if birthday.startswith("--") and len(birthday) == 7:
        return _line("BDAY;X-APPLE-OMIT-YEAR=1604", f"1604{birthday[1:]}")
    return _line("BDAY", birthday)


def _display_name(card) -> str:
    """First current name wins. Matches how the collection list picks a name."""
    for n in card.person.names:
        if n.value:
            return n.value
    return ""


def format_vcard(
    cards: Iterable,
    family_given: Optional[Dict[str, Tuple[str, str]]] = None,
) -> str:
    """Return a vCard 3.0 document for the given cards.

    `family_given` optionally maps a display name to (family, given) so the
    N field can be structured. Where it is absent — which is the norm for CJK
    names that were never split — N carries the full name in the family slot,
    which is what Contacts does with a single-field name anyway.
    """
    family_given = family_given or {}
    out: List[str] = []

    for card in cards:
        full = _display_name(card)
        if not full:
            # No name means nothing to import; skip rather than emit a blank.
            continue

        out.append("BEGIN:VCARD\r\n")
        out.append(_line("VERSION", "3.0"))
        out.append(_line("FN", _escape(full)))

        family, given = family_given.get(full, (full, ""))
        out.append(_line("N", f"{_escape(family)};{_escape(given)};;;"))

        # Additional names (romanised, Chinese, ...) become nicknames so they
        # remain searchable in Contacts without competing with FN.
        for n in card.person.names:
            if n.value and n.value != full:
                out.append(_line("NICKNAME", _escape(n.value)))

        for pos in card.person.positions:
            if pos.company:
                org = _escape(pos.company)
                if pos.department:
                    org = f"{org};{_escape(pos.department)}"
                out.append(_line("ORG", org))
            if pos.title:
                out.append(_line("TITLE", _escape(pos.title)))

        for phone in card.person.phones:
            if phone.value:
                type_param = _PHONE_TYPES.get(phone.type, "WORK,VOICE")
                out.append(_line(f"TEL;TYPE={type_param}", _escape(phone.value)))

        for email in card.person.emails:
            if email.value:
                kind = "WORK" if email.type != "personal" else "HOME"
                out.append(_line(f"EMAIL;TYPE=INTERNET,{kind}", _escape(email.value)))

        for addr in card.person.addresses:
            kind = "WORK" if addr.type != "home" else "HOME"
            # ADR is positional: PO box; extended; street; locality; region;
            # postal code; country. Leaving a slot empty is correct, not lazy.
            parts = ";".join([
                "", "",
                _escape(addr.street or ""),
                _escape(addr.city or ""),
                _escape(addr.state or ""),
                _escape(addr.postal_code or ""),
                _escape(addr.country or ""),
            ])
            out.append(_line(f"ADR;TYPE={kind}", parts))

        if card.person.website:
            out.append(_line("URL", _escape(card.person.website)))

        if card.person.birthday:
            out.append(_birthday_line(card.person.birthday))

        if getattr(card, "notes", ""):
            out.append(_line("NOTE", _escape(card.notes)))

        out.append("END:VCARD\r\n")

    return "".join(out)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python3 -m pytest tests/test_vcard_export.py -v`
Expected: PASS, 15 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/vcard_export.py tests/test_vcard_export.py
git commit -m "feat: add vCard 3.0 formatter for card export"
```

---

## Task 2: vCard export endpoint

**Files:**
- Modify: `app/routers/v2/export.py`
- Test: `tests/test_vcard_endpoint.py`

- [ ] **Step 1: Write the failing test**

```python
"""The .vcf endpoint mirrors the CSV endpoint's contract."""


def test_vcard_endpoint_rejects_empty_card_ids(client_with_test_db):
    r = client_with_test_db.get("/api/v2/export/vcard?card_ids=")
    assert r.status_code == 400


def test_vcard_endpoint_returns_vcard_content_type(client_with_test_db):
    r = client_with_test_db.get("/api/v2/export/vcard?card_ids=does-not-exist")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/vcard")


def test_vcard_endpoint_sets_a_download_filename(client_with_test_db):
    r = client_with_test_db.get("/api/v2/export/vcard?card_ids=does-not-exist")
    assert "contacts.vcf" in r.headers["content-disposition"]


def test_vcard_endpoint_is_utf8(client_with_test_db):
    """CJK names must not be mangled by the response encoding."""
    r = client_with_test_db.get("/api/v2/export/vcard?card_ids=does-not-exist")
    assert "charset=utf-8" in r.headers["content-type"].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python3 -m pytest tests/test_vcard_endpoint.py -v`
Expected: FAIL — 404 on all four

- [ ] **Step 3: Write minimal implementation**

Add to `app/routers/v2/export.py`, directly after `export_csv`. Add the import at the top of the file:

```python
from app.services.vcard_export import format_vcard
```

then the endpoint:

```python
@router.get("/vcard")
async def export_vcard(
    card_ids: str = Query(..., description="Comma-separated card external IDs"),
    db: AsyncSession = Depends(get_db),
):
    """Download a .vcf for the requested cards.

    Double-clicking the result on macOS imports straight into Contacts, which
    syncs onward to the user's iPhone. This is an interoperability path, not a
    backup — it carries no card images, occasions, or merge history.

    GET /api/v2/export/vcard?card_ids=abc,def
    """
    ext_ids = [cid.strip() for cid in card_ids.split(",") if cid.strip()]
    if not ext_ids:
        raise HTTPException(status_code=400, detail="card_ids must not be empty")

    # Same loader as export_csv: one normalisation path for every exporter.
    legacy_cards = []
    family_given: dict[str, tuple[str, str]] = {}
    for ext_id in ext_ids:
        db_card = await _load_full_card(db, ext_id)
        if db_card is None:
            continue  # silently skip missing cards, as the CSV export does
        legacy = build_legacy_card(
            db_card,
            db_card.person,
            db_card.person.contact_details,
            db_card.person.positions,
        )
        legacy_cards.append(legacy)

        # Structured N field, where the database actually knows the split.
        for name in db_card.person.names:
            if name.is_current and name.full_name and name.family_name:
                given = (name.full_name.replace(name.family_name, "", 1)).strip()
                family_given[name.full_name] = (name.family_name, given)

    text = format_vcard(legacy_cards, family_given=family_given)

    return Response(
        content=text.encode("utf-8"),
        media_type="text/vcard; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="contacts.vcf"'},
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python3 -m pytest tests/test_vcard_endpoint.py -v`
Expected: PASS, 4 passed

- [ ] **Step 5: Verify a real import into Contacts**

```bash
launchctl unload ~/Library/LaunchAgents/co.nxta.nxt-a1-backend.plist && launchctl load ~/Library/LaunchAgents/co.nxta.nxt-a1-backend.plist
sleep 3
# Take two real card ids from the collection page URL bar or the API:
curl -s "localhost:8000/api/v2/cards?limit=2" | head -c 400
curl -s "localhost:8000/api/v2/export/vcard?card_ids=<id1>,<id2>" -o /tmp/contacts.vcf
open /tmp/contacts.vcf
```

Confirm in Contacts that both people appear, CJK names are intact, and phone numbers carry the right labels.

> **Watch out:** the app has an SPA catch-all. If `/tmp/contacts.vcf` opens in a
> browser as HTML, the route did not register — check the byte count and the
> first line, not the status code.

- [ ] **Step 6: Commit**

```bash
git add app/routers/v2/export.py tests/test_vcard_endpoint.py
git commit -m "feat: add GET /api/v2/export/vcard endpoint"
```

---

## Task 3: Widen search

**Files:**
- Modify: `app/routers/v2/persons.py`
- Test: `tests/test_person_search.py`

**Why this matters most:** the spec names retrieval as the app's primary value. Today search matches only current person names and current organisation names — not titles, not emails, not phone numbers, and not the occasion where the card was collected. "The person from the Rotary dinner in March" is the query this audience actually has.

- [ ] **Step 1: Write the failing test**

```python
"""Search must reach every field a user would remember someone by."""
import pytest
from sqlalchemy import select

from app.db.models import (
    Card, ContactDetail, Occasion, Organization, OrganizationName,
    Person, PersonName, Position, PositionDetail,
)


@pytest.fixture
def seeded(client_with_test_db):
    """One person with every searchable field populated."""
    import asyncio

    async def _seed():
        async with client_with_test_db.session_maker() as db:
            person = Person(notes="met at the golf day, keen on ceramics")
            db.add(person)
            await db.flush()

            db.add(PersonName(person_id=person.id, full_name="山田太郎",
                              language="ja", name_type="primary", is_current=True))

            org = Organization()
            db.add(org)
            await db.flush()
            db.add(OrganizationName(org_id=org.id, name="Acme Trading",
                                    language="en", is_current=True))

            pos = Position(person_id=person.id, org_id=org.id)
            db.add(pos)
            await db.flush()
            db.add(PositionDetail(position_id=pos.id, language="en",
                                  title="Chief Ceramics Officer", department="Kilns"))

            db.add(ContactDetail(person_id=person.id, detail_type="email_work",
                                 value="taro@acme.example"))
            db.add(ContactDetail(person_id=person.id, detail_type="phone_mobile",
                                 value="090-5555-1234"))

            occ = Occasion(name="Rotary March Dinner", location="Taipei")
            db.add(occ)
            await db.flush()
            db.add(Card(person_id=person.id, occasion_id=occ.id))

            await db.commit()

    asyncio.run(_seed())
    return client_with_test_db


def _search(client, q):
    r = client.get(f"/api/v2/persons?q={q}")
    assert r.status_code == 200
    return r.json()


def test_finds_by_person_name(seeded):
    assert len(_search(seeded, "山田")) == 1


def test_finds_by_company_name(seeded):
    assert len(_search(seeded, "Acme")) == 1


def test_finds_by_job_title(seeded):
    assert len(_search(seeded, "Ceramics Officer")) == 1


def test_finds_by_department(seeded):
    assert len(_search(seeded, "Kilns")) == 1


def test_finds_by_email_fragment(seeded):
    assert len(_search(seeded, "taro@")) == 1


def test_finds_by_phone_fragment(seeded):
    assert len(_search(seeded, "5555")) == 1


def test_finds_by_notes(seeded):
    assert len(_search(seeded, "ceramics")) == 1


def test_finds_by_occasion_name(seeded):
    """The query this audience actually has."""
    assert len(_search(seeded, "Rotary March")) == 1


def test_finds_by_occasion_location(seeded):
    assert len(_search(seeded, "Taipei")) == 1


def test_returns_nothing_for_an_unrelated_term(seeded):
    assert _search(seeded, "zzzznotpresent") == []


def test_a_person_matching_twice_appears_once(seeded):
    """'Ceramics' hits both the title and the notes — the union must dedupe."""
    assert len(_search(seeded, "ceramics")) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python3 -m pytest tests/test_person_search.py -v`
Expected: FAIL — the title, department, email, phone, notes, and occasion tests return 0 results

- [ ] **Step 3: Write minimal implementation**

In `app/routers/v2/persons.py`, replace the `if q:` branch of `list_persons`. Add the imports it needs at the top of the file:

```python
from app.db.models import Card, ContactDetail, Occasion, PositionDetail
```

Then the branch:

```python
    if q:
        pattern = f"%{q}%"

        # Each collector returns person_ids; the union is what we look up.
        # Adding a searchable field means adding one collector here.
        collectors = [
            # Current person names
            select(PersonName.person_id).where(
                PersonName.is_current == True,  # noqa: E712
                PersonName.full_name.ilike(pattern),
            ),
            # Current organisation names, via the person's positions
            select(Position.person_id)
            .join(OrganizationName, OrganizationName.org_id == Position.org_id)
            .where(
                OrganizationName.is_current == True,  # noqa: E712
                OrganizationName.name.ilike(pattern),
            ),
            # Job title and department
            select(Position.person_id)
            .join(PositionDetail, PositionDetail.position_id == Position.id)
            .where(
                or_(
                    PositionDetail.title.ilike(pattern),
                    PositionDetail.department.ilike(pattern),
                )
            ),
            # Every contact detail shares one column, so a single clause covers
            # email, phone, address, website and social handles.
            select(ContactDetail.person_id).where(
                or_(
                    ContactDetail.value.ilike(pattern),
                    ContactDetail.label.ilike(pattern),
                )
            ),
            # Free-text notes on the person
            select(Person.id).where(Person.notes.ilike(pattern)),
            # Occasion the card was collected at, via the card
            select(Card.person_id)
            .join(Occasion, Occasion.id == Card.occasion_id)
            .where(
                or_(
                    Occasion.name.ilike(pattern),
                    Occasion.location.ilike(pattern),
                    Occasion.notes.ilike(pattern),
                )
            ),
        ]

        all_ids: set[int] = set()
        for stmt_part in collectors:
            all_ids.update((await db.execute(stmt_part)).scalars().all())

        # Search is not paginated: a user searching wants every match, and a
        # personal collection is small enough that this is cheap.
        stmt = select(Person).where(Person.id.in_(all_ids))
    else:
```

Add `or_` to the SQLAlchemy import at the top:

```python
from sqlalchemy import or_, select
```

> **Deliberately not using SQLite FTS.** A few hundred rows on local SQLite does
> not need it, and FTS tokenisation handles CJK badly enough to be its own
> project. Revisit only if measurement on a real collection shows `ILIKE` is too
> slow.

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python3 -m pytest tests/test_person_search.py -v`
Expected: PASS, 11 passed

- [ ] **Step 5: Commit**

```bash
git add app/routers/v2/persons.py tests/test_person_search.py
git commit -m "feat: search titles, contact details, notes and occasions"
```

---

## Task 4: Remove the browse ceiling

**Files:**
- Modify: `app/routers/v2/persons.py`, `frontend/src/api/index.ts`, `frontend/src/pages/CollectionPage.tsx`
- Test: `tests/test_person_pagination.py`

**The bug:** `list_persons` caps at `le=200`, and `CollectionPage` requests `listCards({ limit: 200 })` then filters client-side. A friend who scans 300 cards silently cannot reach most of their own collection — and nothing tells them.

- [ ] **Step 1: Write the failing test**

```python
"""A real collection is bigger than the old 200-row ceiling."""
import asyncio

import pytest

from app.db.models import Person, PersonName


@pytest.fixture
def many_persons(client_with_test_db):
    async def _seed():
        async with client_with_test_db.session_maker() as db:
            for i in range(250):
                p = Person()
                db.add(p)
                await db.flush()
                db.add(PersonName(person_id=p.id, full_name=f"Person {i:04d}",
                                  language="en", name_type="primary", is_current=True))
            await db.commit()

    asyncio.run(_seed())
    return client_with_test_db


def test_limit_above_200_is_accepted(many_persons):
    r = many_persons.get("/api/v2/persons?limit=1000")
    assert r.status_code == 200, r.text
    assert len(r.json()) == 250


def test_offset_pages_through_the_collection(many_persons):
    first = many_persons.get("/api/v2/persons?limit=100&offset=0").json()
    second = many_persons.get("/api/v2/persons?limit=100&offset=100").json()
    assert len(first) == 100
    assert len(second) == 100
    assert {p["external_id"] for p in first}.isdisjoint({p["external_id"] for p in second})


def test_search_is_not_truncated_by_the_page_size(many_persons):
    """Every 'Person 0' match must come back, not just one page of them."""
    r = many_persons.get("/api/v2/persons?q=Person 0")
    assert r.status_code == 200
    assert len(r.json()) == 250
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python3 -m pytest tests/test_person_pagination.py -v`
Expected: FAIL — `test_limit_above_200_is_accepted` returns 422, since `limit` is `le=200`

- [ ] **Step 3: Raise the backend ceiling**

In `app/routers/v2/persons.py`, change the `limit` parameter of `list_persons`:

```python
    # A personal card collection is bounded by how many hands a human shakes,
    # so a high ceiling is safe and a low one silently hides people's data.
    limit: int = Query(500, le=5000),
```

Apply the same change to `list_cards` in `app/routers/v2/cards.py`, which is currently `Query(50, le=500)`:

```python
    limit: int = Query(500, le=5000),
```

- [ ] **Step 4: Raise the frontend ceiling**

In `frontend/src/api/index.ts`, give `listPersons` pagination parameters:

```typescript
export const listPersons = (q?: string, limit = 1000, offset = 0) => {
  const params = new URLSearchParams()
  if (q) params.set('q', q)
  params.set('limit', String(limit))
  params.set('offset', String(offset))
  return get<PersonListItem[]>(`/api/v2/persons?${params}`)
}
```

In `frontend/src/pages/CollectionPage.tsx`, the cards query requests 200. Raise it:

```typescript
  const { data: cards = [] } = useQuery({
    queryKey: ['cards'],
    // A collection is a lifetime of business cards, not a page of them.
    queryFn: () => listCards({ limit: 2000 }),
  })
```

- [ ] **Step 5: Run tests and build**

Run: `venv/bin/python3 -m pytest tests/test_person_pagination.py -v`
Expected: PASS, 3 passed

Run: `cd frontend && npm run build`
Expected: build succeeds

- [ ] **Step 6: Commit**

```bash
git add app/routers/v2/persons.py app/routers/v2/cards.py frontend/src/api/index.ts frontend/src/pages/CollectionPage.tsx tests/test_person_pagination.py
git commit -m "fix: remove the 200-row ceiling that hid large collections"
```

---

## Task 5: Backup archive

**Files:**
- Create: `app/services/backup.py`
- Test: `tests/test_backup.py`

- [ ] **Step 1: Write the failing test**

```python
"""Backup captures the database and the card images together."""
import zipfile

from app.services import backup


def _fake_data_dir(tmp_path):
    (tmp_path / "images" / "card-1").mkdir(parents=True)
    (tmp_path / "images" / "card-1" / "1.jpg").write_bytes(b"front")
    (tmp_path / "temp").mkdir()
    (tmp_path / "temp" / "scratch.jpg").write_bytes(b"transient")
    (tmp_path / "meishi.db").write_bytes(b"sqlite-bytes")
    return tmp_path


def test_archive_contains_the_database(tmp_path):
    data = _fake_data_dir(tmp_path / "data")
    out = tmp_path / "backup.zip"
    backup.create_backup(data, out)
    with zipfile.ZipFile(out) as z:
        assert "meishi.db" in z.namelist()


def test_archive_contains_card_images(tmp_path):
    data = _fake_data_dir(tmp_path / "data")
    out = tmp_path / "backup.zip"
    backup.create_backup(data, out)
    with zipfile.ZipFile(out) as z:
        assert "images/card-1/1.jpg" in z.namelist()


def test_archive_excludes_temp(tmp_path):
    """temp/ holds in-flight scan images; restoring them would be noise."""
    data = _fake_data_dir(tmp_path / "data")
    out = tmp_path / "backup.zip"
    backup.create_backup(data, out)
    with zipfile.ZipFile(out) as z:
        assert not any(n.startswith("temp/") for n in z.namelist())


def test_archive_excludes_the_config_file(tmp_path):
    """config.json holds a live API key and must never leave in a backup."""
    data = _fake_data_dir(tmp_path / "data")
    (data / "config.json").write_text('{"anthropic_api_key": "sk-ant-secret"}')
    out = tmp_path / "backup.zip"
    backup.create_backup(data, out)
    with zipfile.ZipFile(out) as z:
        assert "config.json" not in z.namelist()


def test_archive_includes_a_manifest(tmp_path):
    data = _fake_data_dir(tmp_path / "data")
    out = tmp_path / "backup.zip"
    backup.create_backup(data, out)
    with zipfile.ZipFile(out) as z:
        assert "nxt-a1-backup.json" in z.namelist()


def test_validate_accepts_an_archive_we_made(tmp_path):
    data = _fake_data_dir(tmp_path / "data")
    out = tmp_path / "backup.zip"
    backup.create_backup(data, out)
    assert backup.validate_backup(out) is True


def test_validate_rejects_an_unrelated_zip(tmp_path):
    """Restore is destructive, so an unrecognised archive must be refused."""
    out = tmp_path / "random.zip"
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("hello.txt", "not a backup")
    assert backup.validate_backup(out) is False


def test_restore_replaces_the_data_directory(tmp_path):
    source = _fake_data_dir(tmp_path / "source")
    archive = tmp_path / "backup.zip"
    backup.create_backup(source, archive)

    target = tmp_path / "target"
    target.mkdir()
    (target / "meishi.db").write_bytes(b"old-database")
    (target / "images").mkdir()
    (target / "images" / "stale.jpg").write_bytes(b"should be gone")

    backup.restore_backup(archive, target)

    assert (target / "meishi.db").read_bytes() == b"sqlite-bytes"
    assert (target / "images" / "card-1" / "1.jpg").read_bytes() == b"front"
    assert not (target / "images" / "stale.jpg").exists()


def test_restore_leaves_config_untouched(tmp_path):
    """Restoring someone's collection must not wipe their API key."""
    source = _fake_data_dir(tmp_path / "source")
    archive = tmp_path / "backup.zip"
    backup.create_backup(source, archive)

    target = tmp_path / "target"
    target.mkdir()
    (target / "config.json").write_text('{"anthropic_api_key": "sk-ant-keepme"}')

    backup.restore_backup(archive, target)
    assert "sk-ant-keepme" in (target / "config.json").read_text()


def test_restore_refuses_an_invalid_archive(tmp_path):
    bad = tmp_path / "random.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("hello.txt", "not a backup")

    target = tmp_path / "target"
    target.mkdir()
    (target / "meishi.db").write_bytes(b"precious")

    try:
        backup.restore_backup(bad, target)
        assert False, "expected ValueError"
    except ValueError:
        pass

    # The existing collection must survive a refused restore untouched.
    assert (target / "meishi.db").read_bytes() == b"precious"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python3 -m pytest tests/test_backup.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.backup'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Back up and restore ~/.nxt-a1/.

The collection is the product: a friend's entire card database lives on one
laptop with no other copy, and a non-technical user will not have Time Machine
configured. A vCard export is not a substitute — it loses card images,
occasions, relationships and merge history.

Two things are deliberately excluded from the archive:
  temp/        in-flight scan images, meaningless after the fact
  config.json  holds a live API key; a backup gets emailed and copied around
"""
from __future__ import annotations

import json
import logging
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

MANIFEST_NAME = "nxt-a1-backup.json"
BACKUP_FORMAT_VERSION = 1

# Never archived. config.json is excluded for secrecy, temp/ for irrelevance.
_EXCLUDED_NAMES = {"config.json", MANIFEST_NAME}
_EXCLUDED_DIRS = {"temp"}


def create_backup(data_dir: Path, dest: Path) -> Path:
    """Zip the database and card images into `dest`. Returns `dest`."""
    dest.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(data_dir.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(data_dir)
            if relative.parts and relative.parts[0] in _EXCLUDED_DIRS:
                continue
            if relative.name in _EXCLUDED_NAMES:
                continue
            archive.write(path, arcname=str(relative))

        archive.writestr(MANIFEST_NAME, json.dumps({
            "format_version": BACKUP_FORMAT_VERSION,
            "created_at": datetime.now().isoformat(),
            "application": "nxt-a1-meishi",
        }, indent=2))

    return dest


def validate_backup(archive_path: Path) -> bool:
    """True only for an archive this application produced.

    Restore destroys the current collection, so anything unrecognised is
    refused rather than half-applied.
    """
    try:
        with zipfile.ZipFile(archive_path) as archive:
            if MANIFEST_NAME not in archive.namelist():
                return False
            manifest = json.loads(archive.read(MANIFEST_NAME))
            return manifest.get("application") == "nxt-a1-meishi"
    except (zipfile.BadZipFile, json.JSONDecodeError, KeyError, OSError):
        return False


def restore_backup(archive_path: Path, data_dir: Path) -> None:
    """Replace the collection in `data_dir` with the archive's contents.

    This REPLACES rather than merges: anything in the current collection that
    is not in the archive is gone. The UI must say so before calling this.

    Validation happens before anything is deleted, so a refused restore leaves
    the existing collection exactly as it was.
    """
    if not validate_backup(archive_path):
        raise ValueError("Not a recognised NXT-A1 backup archive")

    # Clear only what the archive owns. config.json is deliberately preserved,
    # so restoring a collection does not wipe the user's API key.
    db_path = data_dir / "meishi.db"
    if db_path.exists():
        db_path.unlink()
    images_dir = data_dir / "images"
    if images_dir.exists():
        shutil.rmtree(images_dir)

    with zipfile.ZipFile(archive_path) as archive:
        for name in archive.namelist():
            if name == MANIFEST_NAME:
                continue
            # Refuse path traversal: a crafted archive must not write outside
            # the data directory.
            target = (data_dir / name).resolve()
            if not str(target).startswith(str(data_dir.resolve())):
                raise ValueError(f"Archive entry escapes the data directory: {name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(name) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python3 -m pytest tests/test_backup.py -v`
Expected: PASS, 10 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/backup.py tests/test_backup.py
git commit -m "feat: add backup and restore of the ~/.nxt-a1 data directory"
```

---

## Task 6: Backup endpoints

**Files:**
- Create: `app/routers/v2/backup.py`
- Modify: `app/main.py`
- Test: `tests/test_backup_endpoints.py`

- [ ] **Step 1: Write the failing test**

```python
"""Download a backup; upload one to restore."""
import io
import zipfile


def test_download_returns_a_zip(client_with_test_db):
    r = client_with_test_db.get("/api/v2/backup/download")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert zipfile.ZipFile(io.BytesIO(r.content)).namelist()


def test_download_filename_carries_the_date(client_with_test_db):
    r = client_with_test_db.get("/api/v2/backup/download")
    assert "nxt-a1-backup-" in r.headers["content-disposition"]
    assert ".zip" in r.headers["content-disposition"]


def test_restore_rejects_a_non_backup_zip(client_with_test_db):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("hello.txt", "not a backup")
    buf.seek(0)

    r = client_with_test_db.post(
        "/api/v2/backup/restore",
        files={"file": ("random.zip", buf, "application/zip")},
    )
    assert r.status_code == 400


def test_restore_rejects_a_non_zip_upload(client_with_test_db):
    r = client_with_test_db.post(
        "/api/v2/backup/restore",
        files={"file": ("notes.txt", io.BytesIO(b"plain text"), "text/plain")},
    )
    assert r.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python3 -m pytest tests/test_backup_endpoints.py -v`
Expected: FAIL — 404 on all four

- [ ] **Step 3: Write minimal implementation**

Create `app/routers/v2/backup.py`:

```python
"""Backup and restore endpoints.

Kept in their own router because restore is the most destructive operation the
application exposes and benefits from being easy to find and review.
"""
from __future__ import annotations

import logging
import tempfile
from datetime import date
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.config import settings
from app.services.backup import create_backup, restore_backup, validate_backup

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/backup", tags=["backup"])


@router.get("/download")
async def download_backup():
    """Zip the collection and hand it back as a download."""
    filename = f"nxt-a1-backup-{date.today().isoformat()}.zip"
    # A temp file rather than an in-memory buffer: a collection with several
    # hundred card images is larger than we want to hold in RAM.
    tmp_dir = Path(tempfile.mkdtemp())
    archive = create_backup(settings.data_dir, tmp_dir / filename)

    return FileResponse(
        path=str(archive),
        media_type="application/zip",
        filename=filename,
    )


@router.post("/restore")
async def upload_restore(file: UploadFile = File(...)):
    """Replace the current collection with the uploaded archive.

    Validation happens before anything is deleted, so a rejected upload leaves
    the existing collection untouched.
    """
    tmp_dir = Path(tempfile.mkdtemp())
    staged = tmp_dir / "upload.zip"
    with open(staged, "wb") as fh:
        fh.write(await file.read())

    if not validate_backup(staged):
        raise HTTPException(
            status_code=400,
            detail={"error_code": "not_a_backup"},
        )

    try:
        restore_backup(staged, settings.data_dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error_code": "not_a_backup"}) from exc

    logger.info("Collection restored from uploaded backup.")
    return {"restored": True}
```

- [ ] **Step 4: Register the router**

In `app/main.py`, add to the v2 imports:

```python
from app.routers.v2 import backup as v2_backup
```

and register it:

```python
app.include_router(v2_backup.router)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `venv/bin/python3 -m pytest tests/test_backup_endpoints.py -v`
Expected: PASS, 4 passed

- [ ] **Step 6: Commit**

```bash
git add app/routers/v2/backup.py app/main.py tests/test_backup_endpoints.py
git commit -m "feat: add backup download and restore endpoints"
```

---

## Task 7: Frontend — backup controls, vCard destination, strings

**Files:**
- Create: `frontend/src/api/backup.ts`
- Modify: `frontend/src/pages/SettingsPage.tsx`, `frontend/src/components/ExportDestinationSelector.tsx`, `frontend/src/i18n.ts`

- [ ] **Step 1: Add i18n strings in all three languages**

TypeScript enforces key parity, so all three blocks are mandatory.

English:

```typescript
    // Backup
    backupTitle: 'Backup',
    backupHelp: 'Your contacts live only on this Mac. Save a copy somewhere safe.',
    backupDownload: 'Back up my collection',
    backupRestore: 'Restore from a backup',
    backupRestoreWarning: 'Restoring REPLACES your current collection. Anything scanned since that backup will be lost. Continue?',
    backupRestoreConfirm: 'Yes, replace my collection',
    backupRestoreCancel: 'Cancel',
    backupRestoreDone: 'Collection restored.',
    backupRestoreBadFile: 'That file is not an NXT-A1 backup.',

    // vCard
    destVcard: 'vCard (.vcf) — import into Contacts',
```

Japanese:

```typescript
    backupTitle: 'バックアップ',
    backupHelp: '連絡先はこのMacにのみ保存されています。安全な場所にコピーを保存してください。',
    backupDownload: 'コレクションをバックアップ',
    backupRestore: 'バックアップから復元',
    backupRestoreWarning: '復元すると現在のコレクションは置き換えられます。そのバックアップ以降にスキャンした内容は失われます。続けますか？',
    backupRestoreConfirm: 'はい、置き換えます',
    backupRestoreCancel: 'キャンセル',
    backupRestoreDone: 'コレクションを復元しました。',
    backupRestoreBadFile: 'このファイルはNXT-A1のバックアップではありません。',

    destVcard: 'vCard (.vcf) — 連絡先に取り込む',
```

Traditional Chinese:

```typescript
    backupTitle: '備份',
    backupHelp: '您的聯絡人僅儲存在這台 Mac 上。請將副本保存到安全的位置。',
    backupDownload: '備份我的收藏',
    backupRestore: '從備份還原',
    backupRestoreWarning: '還原將取代您目前的收藏，該備份之後掃描的內容將會遺失。要繼續嗎？',
    backupRestoreConfirm: '是，取代我的收藏',
    backupRestoreCancel: '取消',
    backupRestoreDone: '收藏已還原。',
    backupRestoreBadFile: '此檔案不是 NXT-A1 備份。',

    destVcard: 'vCard (.vcf) — 匯入聯絡人',
```

- [ ] **Step 2: Write the backup API client**

```typescript
// Backup and restore. Both move whole files, so neither goes through the JSON
// helpers in client.ts.

export async function downloadBackup(): Promise<void> {
  const res = await fetch('/api/v2/backup/download')
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)

  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `nxt-a1-backup-${new Date().toISOString().slice(0, 10)}.zip`
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

export async function restoreBackup(file: File): Promise<void> {
  const form = new FormData()
  form.append('file', file)
  const res = await fetch('/api/v2/backup/restore', { method: 'POST', body: form })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
}
```

- [ ] **Step 3: Add the backup section to Settings**

Following the existing `<section>` pattern in `SettingsPage`:

```tsx
import { useRef, useState } from 'react'
import { downloadBackup, restoreBackup } from '../api/backup'

function BackupSection() {
  const { t } = useLang()
  const fileInput = useRef<HTMLInputElement>(null)
  const [error, setError] = useState('')

  async function handleRestore(file: File) {
    // Restore replaces rather than merges, so it must be confirmed explicitly.
    if (!window.confirm(t.backupRestoreWarning)) return
    setError('')
    try {
      await restoreBackup(file)
      window.location.reload()
    } catch {
      setError(t.backupRestoreBadFile)
    }
  }

  return (
    <section>
      <h2 className="text-sm font-medium text-gray-700 mb-3">{t.backupTitle}</h2>
      <p className="text-sm text-gray-500 mb-3">{t.backupHelp}</p>
      <div className="flex gap-3">
        <button
          className="rounded-md bg-gray-900 px-3 py-1.5 text-sm text-white"
          onClick={() => downloadBackup()}
        >
          {t.backupDownload}
        </button>
        <button
          className="rounded-md border border-gray-300 px-3 py-1.5 text-sm"
          onClick={() => fileInput.current?.click()}
        >
          {t.backupRestore}
        </button>
        <input
          ref={fileInput}
          type="file"
          accept=".zip"
          className="hidden"
          onChange={e => {
            const file = e.target.files?.[0]
            if (file) handleRestore(file)
            e.target.value = ''
          }}
        />
      </div>
      {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
    </section>
  )
}
```

Render `<BackupSection />` alongside the existing sections.

- [ ] **Step 4: Add vCard as an export destination**

In `frontend/src/components/ExportDestinationSelector.tsx`, add an entry to the `DESTINATIONS` array:

```tsx
  { key: 'vcard', label: 'vCard (.vcf)', download: true },
```

and handle it in the download branch, beside the existing `google_csv` case:

```tsx
        } else if (dest === 'vcard') {
          await fetchAndDownload(
            `${baseUrl}/vcard?card_ids=${encodeURIComponent(idsParam)}`,
            'contacts.vcf',
          )
```

- [ ] **Step 5: Build**

Run: `cd frontend && npm run build`
Expected: build succeeds

- [ ] **Step 6: Verify in the browser**

1. Settings → "Back up my collection" downloads a `.zip`. Open it and confirm `meishi.db` and `images/` are present and `config.json` is **not**.
2. Export a `.vcf` from the collection, double-click it, confirm the contacts land in Contacts with CJK names intact.
3. Search the collection for a job title, an email fragment, and an occasion name. All three should return results that name search alone would have missed.
4. Restore: **use a copy of the data directory, not the real one.** Back up first, then restore that same archive, and confirm the collection is unchanged and the confirmation dialog appeared.

> This is the most destructive feature in the app. Do not test restore against
> Koji's real collection without a verified backup in hand first.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/backup.ts frontend/src/pages/SettingsPage.tsx frontend/src/components/ExportDestinationSelector.tsx frontend/src/i18n.ts
git commit -m "feat: backup controls in Settings and vCard export destination"
```

---

## Task 8: Full-suite verification

- [ ] **Step 1: Backend suite**

Run: `venv/bin/python3 -m pytest tests/ -v`
Expected: PASS, including the six new test files

- [ ] **Step 2: Frontend build**

Run: `cd frontend && npm run build`
Expected: build succeeds

- [ ] **Step 3: Confirm the image-store invariants still hold**

Restore rewrites the images tree, so re-run the project's own checker to prove
`side_order` contiguity and filename alignment survived:

```bash
PYTHONPATH=. venv/bin/python3 -m scripts.check_image_store
```
Expected: no violations. If the module path differs, find it with `ls scripts/`.

- [ ] **Step 4: Open the PR**

```bash
git push -u origin feat/community-edition-a2
gh pr create --title "Community Edition A2: collection value" --body "$(cat <<'EOF'
Implements Phase A2 of the Community Edition spec: W6, W11, W12.

- vCard 3.0 export (`GET /api/v2/export/vcard`), sharing the `build_legacy_card` path with the CSV exporters
- Search widened from names+company to titles, departments, contact details, notes, and occasions
- Removed the 200-row ceiling that silently hid large collections
- Backup and restore of `~/.nxt-a1/`, excluding `config.json` so a backup never carries an API key

Spec: `docs/superpowers/specs/2026-09-08-community-edition-distribution-design.md`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
