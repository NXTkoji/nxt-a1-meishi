# Unify Personal Category + Birthday Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the per-name "Personal N" sections in the card editor into a single "Personal" category, and add a birthday field that is stored, edited via a validating month/day/year control, and synced to Google Contacts.

**Architecture:** Birthday is a per-person value that threads through the app's two card representations — `ParsedCard` (input/editing) and the `app/models/card.py` `Person`/`Card` (Google export) — plus a new `persons.birthday` DB column. The UI change is a pure re-layout of `ParsedCardEditor` (the data model already stores personal contacts at person-level) with a dedicated `BirthdayField` component. Backend logic (serialization + Google body building) is covered by pytest; the frontend is verified by type-check/build + browser.

**Tech Stack:** FastAPI, SQLAlchemy (async) + Alembic, Pydantic, React 19 + TypeScript + Vite + Tailwind, Google People API. Python tests: pytest via `venv/bin/pytest`. Frontend has no JS test runner — verify with `tsc -b && vite build`.

**Working directory:** All paths are relative to the git root `nxt-a1-meishi/`. Current branch is `feat/person-merge`; consider a dedicated branch before starting (see handoff).

---

## File Structure

**Backend (Python):**
- `app/schemas/parsed_card.py` — add `birthday` to `ParsedCard` (editing representation).
- `app/services/claude_parser.py` — pass `birthday` through from Claude JSON; document it in the prompt schema.
- `app/models/card.py` — add `birthday` to the pydantic `Person` (export representation).
- `app/services/google_contacts.py` — new `_parse_birthday` helper (year-optional) + emit `birthdays[]`.
- `app/db/models.py` — add `birthday` column to the `Person` ORM model.
- `migrations/versions/e1f2a3b4c5d6_add_birthday_to_persons.py` — new Alembic revision.
- `app/routers/v2/sessions.py` — persist `parsed.birthday` onto the DB `Person`.
- `app/routers/v2/export.py` — copy DB `person.birthday` into the export `Person`.
- `tests/test_birthday.py` — new pytest module covering schema, parser, and Google body.

**Frontend (TypeScript/React):**
- `frontend/src/types/index.ts` — add `birthday?` to the `ParsedCard` interface.
- `frontend/src/i18n.ts` — add/relabel Personal + birthday strings (both `ja` and `en`).
- `frontend/src/components/BirthdayField.tsx` — new component + pure serialize/deserialize helpers.
- `frontend/src/components/ParsedCardEditor.tsx` — single Personal section, nested name rows, render `BirthdayField`.

**Task order:** backend bottom-up (schema → services → DB → wiring), then frontend. Each task ends with a commit.

---

## Task 1: `birthday` on the `ParsedCard` schema

**Files:**
- Modify: `app/schemas/parsed_card.py:84-94`
- Test: `tests/test_birthday.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_birthday.py`:

```python
"""Tests for the birthday field across schema, parser, and Google sync."""


def test_parsedcard_accepts_birthday():
    from app.schemas.parsed_card import ParsedCard
    card = ParsedCard(birthday="1990-05-20")
    assert card.birthday == "1990-05-20"


def test_parsedcard_birthday_defaults_none():
    from app.schemas.parsed_card import ParsedCard
    assert ParsedCard().birthday is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/test_birthday.py::test_parsedcard_accepts_birthday -v`
Expected: FAIL — `TypeError`/validation error, `ParsedCard` has no `birthday` field.

- [ ] **Step 3: Add the field**

In `app/schemas/parsed_card.py`, inside `class ParsedCard`, add `birthday` right after `card_date` (currently line 89):

```python
    # Date printed on the card (e.g. exchange date stamp), YYYY-MM-DD or None
    card_date: Optional[str] = None
    # Person's birthday: "YYYY-MM-DD", or "--MM-DD" when the year is unknown, else None
    birthday: Optional[str] = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_birthday.py -v`
Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/schemas/parsed_card.py tests/test_birthday.py
git commit -m "feat: add birthday field to ParsedCard schema"
```

---

## Task 2: Parser passthrough + prompt schema

**Files:**
- Modify: `app/services/claude_parser.py:221-228` (ParsedCard construction) and `:54-90` (`_SCHEMA`)
- Test: `tests/test_birthday.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_birthday.py`:

```python
def test_parser_maps_birthday_from_json():
    from app.services.claude_parser import _build_parsed_card
    data = {
        "names": [],
        "positions": [],
        "contact_details": [],
        "birthday": "--03-14",
    }
    card = _build_parsed_card(data)
    assert card.birthday == "--03-14"


def test_parser_birthday_absent_is_none():
    from app.services.claude_parser import _build_parsed_card
    card = _build_parsed_card({"names": [], "positions": [], "contact_details": []})
    assert card.birthday is None
```

(`_build_parsed_card(data: dict) -> ParsedCard` at `app/services/claude_parser.py:175` is the function whose body contains the `return ParsedCard(...)` at line 221.)

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/test_birthday.py::test_parser_maps_birthday_from_json -v`
Expected: FAIL — `card.birthday` is `None` (parser drops the key).

- [ ] **Step 3: Pass birthday through**

In `app/services/claude_parser.py`, in the `return ParsedCard(...)` at line 221, add the `birthday` line after `card_date`:

```python
    return ParsedCard(
        names=names,
        positions=positions,
        contact_details=contact_details,
        card_date=data.get("card_date"),
        birthday=data.get("birthday"),
        languages_detected=data.get("languages_detected", []),
        overall_confidence=float(data.get("overall_confidence", 1.0)),
    )
```

- [ ] **Step 4: Document it in the prompt schema**

In `_SCHEMA` (line 87), add `birthday` right after `card_date`:

```python
  "card_date": "YYYY-MM-DD or null",
  "birthday": "person's birthday as YYYY-MM-DD, or --MM-DD if only month/day is known, else null",
  "languages_detected": ["ja", "en"],
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_birthday.py -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services/claude_parser.py tests/test_birthday.py
git commit -m "feat: parse birthday from Claude response and document in prompt schema"
```

---

## Task 3: `birthday` on the pydantic export `Person`

**Files:**
- Modify: `app/models/card.py:56-64`
- Test: `tests/test_birthday.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_birthday.py`:

```python
def test_export_person_has_birthday():
    from app.models.card import Person
    p = Person(birthday="1988-12-01")
    assert p.birthday == "1988-12-01"
    assert Person().birthday == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/test_birthday.py::test_export_person_has_birthday -v`
Expected: FAIL — `Person` has no `birthday`.

- [ ] **Step 3: Add the field**

In `app/models/card.py`, in `class Person` (line 56), add `birthday` after the `relations` field (line 64):

```python
    relations: list[PersonRelation] = Field(default_factory=list)
    birthday: str = ""  # "YYYY-MM-DD" or "--MM-DD" (year unknown), "" if none
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_birthday.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/models/card.py tests/test_birthday.py
git commit -m "feat: add birthday field to export Person model"
```

---

## Task 4: Google Contacts — year-optional date parse + `birthdays[]`

**Files:**
- Modify: `app/services/google_contacts.py:22-28` (new helper) and `:156-159` (body)
- Test: `tests/test_birthday.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_birthday.py`:

```python
def test_parse_birthday_full_date():
    from app.services.google_contacts import _parse_birthday
    assert _parse_birthday("1990-05-20") == {"year": 1990, "month": 5, "day": 20}


def test_parse_birthday_year_optional():
    from app.services.google_contacts import _parse_birthday
    assert _parse_birthday("--05-20") == {"month": 5, "day": 20}


def test_parse_birthday_invalid_returns_none():
    from app.services.google_contacts import _parse_birthday
    assert _parse_birthday("") is None
    assert _parse_birthday("nonsense") is None


def test_build_person_body_includes_birthday():
    from app.models.card import Card, Person, PersonName
    from app.services.google_contacts import _build_person_body
    card = Card(person=Person(
        names=[PersonName(value="Test Person", type="primary", language="en")],
        birthday="--05-20",
    ))
    body = _build_person_body(card)
    assert body["birthdays"] == [{"date": {"month": 5, "day": 20}, "text": "--05-20"}]


def test_build_person_body_omits_blank_birthday():
    from app.models.card import Card, Person, PersonName
    from app.services.google_contacts import _build_person_body
    card = Card(person=Person(
        names=[PersonName(value="Test Person", type="primary", language="en")],
    ))
    assert "birthdays" not in _build_person_body(card)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/test_birthday.py -k birthday -v`
Expected: FAIL — `_parse_birthday` does not exist; `birthdays` not in body.

- [ ] **Step 3: Add the `_parse_birthday` helper**

In `app/services/google_contacts.py`, add directly below `_parse_iso_date` (after line 28). A dedicated helper is used rather than modifying `_parse_iso_date`, so the existing `received_date` path (which is always a full date) is untouched:

```python
def _parse_birthday(value: str) -> dict | None:
    """Parse a birthday string into a People API Date object.

    Accepts a full "YYYY-MM-DD" or a year-less "--MM-DD" (year unknown).
    The People API accepts a Date with month/day and no year.
    """
    if not value:
        return None
    try:
        if value.startswith("--"):
            _, month, day = value.split("-")  # "" , MM, DD
            return {"month": int(month), "day": int(day)}
        year, month, day = value.split("-")
        return {"year": int(year), "month": int(month), "day": int(day)}
    except (ValueError, AttributeError):
        return None
```

- [ ] **Step 4: Emit `birthdays[]` in the body**

In `_build_person_body`, add a birthday block immediately after the Events block (after line 159, `body["events"] = ...`):

```python
    # Birthday — full date or year-less "--MM-DD".
    birthday_date = _parse_birthday(person.birthday)
    if birthday_date:
        body["birthdays"] = [{"date": birthday_date, "text": person.birthday}]
```

(No extra sync wiring needed: `sync_to_google` derives `updatePersonFields` from `body.keys()`, so `birthdays` is included on updates automatically.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_birthday.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services/google_contacts.py tests/test_birthday.py
git commit -m "feat: sync birthday to Google Contacts (year-optional)"
```

---

## Task 5: DB column + Alembic migration

**Files:**
- Modify: `app/db/models.py:85` (Person ORM model)
- Create: `migrations/versions/e1f2a3b4c5d6_add_birthday_to_persons.py`

- [ ] **Step 1: Add the ORM column**

In `app/db/models.py`, in `class Person` (line 77), add `birthday` after the `notes` column (line 85). `String` and `Optional` are already imported in this file:

```python
    notes: Mapped[Optional[str]] = mapped_column(Text)
    birthday: Mapped[Optional[str]] = mapped_column(String(16))  # "YYYY-MM-DD" or "--MM-DD"
```

- [ ] **Step 2: Create the migration**

Create `migrations/versions/e1f2a3b4c5d6_add_birthday_to_persons.py` (chains from the current head `d15c68815a27`):

```python
"""add_birthday_to_persons

Revision ID: e1f2a3b4c5d6
Revises: d15c68815a27
Create Date: 2026-07-23

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, None] = 'd15c68815a27'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('persons', sa.Column('birthday', sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column('persons', 'birthday')
```

- [ ] **Step 3: Verify the head is what this migration chains from**

Run: `venv/bin/alembic heads`
Expected: `d15c68815a27 (head)`. If it differs, set `down_revision` to the reported head id.

- [ ] **Step 4: Apply the migration**

Run: `venv/bin/alembic upgrade head`
Expected: log line `Running upgrade d15c68815a27 -> e1f2a3b4c5d6, add_birthday_to_persons`, no error.

- [ ] **Step 5: Verify the column exists**

Run: `venv/bin/python -c "import sqlite3; c=sqlite3.connect('nxt_a1.db'); print([r[1] for r in c.execute('PRAGMA table_info(persons)')])"`
Expected: the printed list includes `'birthday'`.
(If the app's DB file is `app.db` rather than `nxt_a1.db`, run the same check against that file — both exist in the repo; the live one is whatever `alembic.ini`/settings point at.)

- [ ] **Step 6: Commit**

```bash
git add app/db/models.py migrations/versions/e1f2a3b4c5d6_add_birthday_to_persons.py
git commit -m "feat: add persons.birthday column and migration"
```

---

## Task 6: Persist birthday on confirm (`sessions.py`)

**Files:**
- Modify: `app/routers/v2/sessions.py:656-667`

- [ ] **Step 1: Set birthday after the Person is found/created**

In `_confirm_one_card`, after the person is resolved (the `if draft.match_person_id: ... else: ...` block ending at line 664) and before `# 2. Names`, add:

```python
        person = Person(external_id=str(uuid.uuid4()))
        db.add(person)
        await db.flush()

    # 1b. Birthday — set only when the card provides one (never clobber with blank)
    if draft.parsed.birthday:
        person.birthday = draft.parsed.birthday

    # 2. Names
    await _upsert_person_names(db, person.id, draft.parsed)
```

- [ ] **Step 2: Type-check the module imports cleanly**

Run: `venv/bin/python -c "import app.routers.v2.sessions"`
Expected: no error (module imports; `Person` ORM now has `birthday`).

- [ ] **Step 3: Commit**

```bash
git add app/routers/v2/sessions.py
git commit -m "feat: persist parsed birthday onto Person on card confirm"
```

---

## Task 7: Carry birthday into the export `Person` (`export.py`)

**Files:**
- Modify: `app/routers/v2/export.py:142-151`

- [ ] **Step 1: Pass birthday into `LegacyPerson`**

In the DB→legacy conversion, add `birthday` to the `LegacyPerson(...)` construction (line 142):

```python
    legacy_person = LegacyPerson(
        names=names,
        positions=legacy_positions,
        phones=phones,
        emails=emails,
        addresses=addresses,
        website=website,
        social=social,
        relations=relations,
        birthday=person.birthday or "",
    )
```

- [ ] **Step 2: Type-check the module imports cleanly**

Run: `venv/bin/python -c "import app.routers.v2.export"`
Expected: no error.

- [ ] **Step 3: Commit**

```bash
git add app/routers/v2/export.py
git commit -m "feat: include birthday when exporting Person for Google sync"
```

---

## Task 8: Frontend type

**Files:**
- Modify: `frontend/src/types/index.ts:38-46`

- [ ] **Step 1: Add `birthday` to `ParsedCard`**

In `frontend/src/types/index.ts`, in `interface ParsedCard`, add `birthday` after `card_date`:

```typescript
export interface ParsedCard {
  names: ParsedName[]
  positions: ParsedPosition[]
  contact_details: ParsedContactDetail[]
  card_date?: string
  birthday?: string
  notes?: string
  languages_detected: string[]
  overall_confidence: number
}
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && npx tsc -b`
Expected: no new errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types/index.ts
git commit -m "feat: add birthday to ParsedCard type"
```

---

## Task 9: i18n strings

**Files:**
- Modify: `frontend/src/i18n.ts` — `ja` block (~76-91) and `en` block (~294-309)

- [ ] **Step 1: Update the `ja` block**

In the `ja` block, change `nameSection` to a name-variant label, relabel `addNameLabel`, and add the new keys (place them next to the existing `nameSection`/`personalContactsLabel` lines):

```typescript
    // ParsedCardEditor
    nameSection: (n: number) => `名前 ${n}`,
    personalSection: '個人',
    orgSection: (n: number) => `組織 ${n}`,
    personalContactsLabel: '個人連絡先',
    workContactsLabel: '勤務先連絡先',
    addFieldLabel: '＋ フィールド追加',
    addOrgLabel: '＋ 組織追加',
    addNameLabel: '＋ 名前を追加',
    addTitleLabel: '＋ 役職追加',
    removeLabel: '削除',
    fieldBirthday: '誕生日',
    birthdayMonth: '月',
    birthdayDay: '日',
    birthdayYear: '年（任意）',
```

- [ ] **Step 2: Update the `en` block**

Apply the parallel changes in the `en` block:

```typescript
    // ParsedCardEditor
    nameSection: (n: number) => `Name ${n}`,
    personalSection: 'Personal',
    orgSection: (n: number) => `Organization ${n}`,
    personalContactsLabel: 'Personal Contacts',
    workContactsLabel: 'Work Contacts',
    addFieldLabel: '+ Add Field',
    addOrgLabel: '+ Add Organization',
    addNameLabel: '+ Add name',
    addTitleLabel: '+ Add Title',
    removeLabel: 'Remove',
    fieldBirthday: 'Birthday',
    birthdayMonth: 'Month',
    birthdayDay: 'Day',
    birthdayYear: 'Year (optional)',
```

- [ ] **Step 3: Type-check**

Run: `cd frontend && npx tsc -b`
Expected: no new errors (both language blocks share the same key set, so a missing key in one would error here).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/i18n.ts
git commit -m "feat: add Personal section + birthday i18n strings"
```

---

## Task 10: `BirthdayField` component

**Files:**
- Create: `frontend/src/components/BirthdayField.tsx`

- [ ] **Step 1: Create the component with pure serialize/deserialize helpers**

Create `frontend/src/components/BirthdayField.tsx`:

```tsx
/**
 * Birthday input: Month + Day dropdowns and an optional numeric Year field.
 *
 * Invalid values are impossible to enter (month/day are constrained selects,
 * year is digit-only). Supports year-unknown birthdays.
 *
 * Serialized value stored on ParsedCard.birthday:
 *   full date      -> "YYYY-MM-DD"
 *   year unknown   -> "--MM-DD"
 *   month/day unset-> ""  (no birthday)
 */
import { useMemo } from 'react'
import { useLang } from '../LangContext'

// month/day are 1-based strings ("1".."12" / "1".."31"); year is a 4-digit string or "".
export function serializeBirthday(year: string, month: string, day: string): string {
  if (!month || !day) return ''
  const mm = month.padStart(2, '0')
  const dd = day.padStart(2, '0')
  if (year) return `${year.padStart(4, '0')}-${mm}-${dd}`
  return `--${mm}-${dd}`
}

export function parseBirthday(value: string | undefined): { year: string; month: string; day: string } {
  if (value) {
    const full = value.match(/^(\d{4})-(\d{2})-(\d{2})$/)
    if (full) return { year: full[1], month: String(Number(full[2])), day: String(Number(full[3])) }
    const noYear = value.match(/^--(\d{2})-(\d{2})$/)
    if (noYear) return { year: '', month: String(Number(noYear[1])), day: String(Number(noYear[2])) }
  }
  return { year: '', month: '', day: '' }
}

export function BirthdayField({
  value,
  onEdit,
}: {
  value: string
  onEdit: (v: string) => void
}) {
  const { t } = useLang()
  const { year, month, day } = useMemo(() => parseBirthday(value), [value])
  const update = (y: string, mo: string, d: string) => onEdit(serializeBirthday(y, mo, d))

  const selectCls = 'text-sm border border-gray-200 rounded px-1 py-0.5 bg-white text-gray-700'

  return (
    <div className="flex items-center gap-2 py-1.5 border-b border-gray-100 last:border-0">
      <span className="w-36 shrink-0 text-xs text-gray-400">{t.fieldBirthday}</span>
      <div className="flex flex-1 items-center gap-1">
        <select className={selectCls} value={month} onChange={e => update(year, e.target.value, day)}>
          <option value="">{t.birthdayMonth}</option>
          {Array.from({ length: 12 }, (_, i) => String(i + 1)).map(mo => (
            <option key={mo} value={mo}>{mo}</option>
          ))}
        </select>
        <select className={selectCls} value={day} onChange={e => update(year, month, e.target.value)}>
          <option value="">{t.birthdayDay}</option>
          {Array.from({ length: 31 }, (_, i) => String(i + 1)).map(d => (
            <option key={d} value={d}>{d}</option>
          ))}
        </select>
        <input
          className="w-24 text-sm border border-gray-200 rounded px-2 py-0.5"
          inputMode="numeric"
          placeholder={t.birthdayYear}
          value={year}
          onChange={e => update(e.target.value.replace(/\D/g, '').slice(0, 4), month, day)}
        />
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && npx tsc -b`
Expected: no new errors. (If `useLang`'s type does not yet include the new keys, that means Task 9 was not applied — apply it first.)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/BirthdayField.tsx
git commit -m "feat: add BirthdayField component"
```

---

## Task 11: Restructure `ParsedCardEditor` into one Personal section

**Files:**
- Modify: `frontend/src/components/ParsedCardEditor.tsx` — import (line 12 area), header comment (lines 1-10), and the render block (lines 499-537)

- [ ] **Step 1: Import `BirthdayField`**

Below the existing `ConfidenceBadge` import (line 12), add:

```typescript
import { BirthdayField } from './BirthdayField'
```

- [ ] **Step 2: Update the header comment**

Replace the layout description at the top of the file (lines 4-6) with:

```typescript
 * Layout:
 *   Personal        ──  one section: all name variants (nested rows) +
 *                       shared personal contacts + birthday
 *   Organization N  ──  company/title/dept + work contacts
```

- [ ] **Step 3: Replace the per-name sections with a single Personal section**

Replace the entire block from `{/* Personal sections (formerly "Names") */}` through the standalone `+ Add name` button (lines 499-537) with:

```tsx
      {/* Personal — one section: name variants + shared personal contacts + birthday */}
      <section className="rounded-lg border border-gray-200">
        <div className="bg-gray-50 px-3 py-1.5 flex items-center gap-2">
          <span className="font-medium text-xs text-gray-600">{t.personalSection}</span>
        </div>
        <div className="px-3 pt-1">

          {/* Name variants */}
          {parsed.names.map((n, i) => (
            <div key={i} className="border-b border-gray-100 pb-1 mb-1">
              <div className="flex items-center gap-2 pt-1">
                <span className="text-xs text-gray-400">{t.nameSection(i + 1)}</span>
                <span className="text-xs bg-gray-200 text-gray-600 rounded px-1">{n.language}</span>
                <span className="text-xs text-gray-400">{n.name_type}</span>
                {parsed.names.length > 1 && (
                  <button className="ml-auto text-xs text-red-400 hover:text-red-600" onClick={() => deleteName(i)}>
                    {t.removeLabel}
                  </button>
                )}
              </div>
              <FieldRow label={t.fieldFullName} value={n.full_name.value} confidence={n.full_name.confidence} onEdit={v => setNameField(i, 'full_name', v)} onCommit={(old, nv) => onCorrection?.({ field_path: `names[${i}].full_name`, claude_value: old, user_value: nv, correction_type: 'field_value' })} />
              {n.family_name && <FieldRow label={t.fieldFamilyName} value={n.family_name.value} confidence={n.family_name.confidence} onEdit={v => setNameField(i, 'family_name', v)} onCommit={(old, nv) => onCorrection?.({ field_path: `names[${i}].family_name`, claude_value: old, user_value: nv, correction_type: 'field_value' })} />}
              {n.given_name && <FieldRow label={t.fieldGivenName} value={n.given_name.value} confidence={n.given_name.confidence} onEdit={v => setNameField(i, 'given_name', v)} onCommit={(old, nv) => onCorrection?.({ field_path: `names[${i}].given_name`, claude_value: old, user_value: nv, correction_type: 'field_value' })} />}
            </div>
          ))}

          <button className="text-xs text-blue-500 hover:text-blue-700 pl-1" onClick={addName}>
            {t.addNameLabel}
          </button>

          {/* Shared personal contacts (rendered once) */}
          <ContactSubSection
            label={t.personalContactsLabel}
            sectionKey="personal"
            contacts={personalContacts}
            availableTypes={PERSONAL_TYPES}
            onEdit={setContact}
            onDelete={deleteContact}
            onAdd={addContact}
            onMoveHere={payload => moveContact(payload, 'personal', 0)}
            onEditCommit={(idx, old, nv) => onCorrection?.({ field_path: `contact_details[${idx}].value`, claude_value: old, user_value: nv, correction_type: 'field_value' })}
          />

          {/* Birthday */}
          <BirthdayField
            value={parsed.birthday ?? ''}
            onEdit={v => onChange({ ...parsed, birthday: v || undefined })}
          />
        </div>
      </section>
```

Notes for the implementer:
- This removes the old `{i === 0 && (...)}` guard around `ContactSubSection` — contacts now render exactly once, outside the name loop.
- The standalone `+ Add name` button that previously sat *after* the `.map` (old lines 535-537) is now *inside* the section; do not leave a duplicate behind.
- `ContactSubSection`, `PERSONAL_TYPES`, `personalContacts`, `setContact`, `deleteContact`, `addContact`, `moveContact`, `addName`, `deleteName`, `setNameField` are all already defined in this file — no new handlers needed.

- [ ] **Step 4: Type-check + build**

Run: `cd frontend && npx tsc -b && npm run build`
Expected: build succeeds, no type errors. A fresh content-hashed bundle appears in `frontend/dist/`.

- [ ] **Step 5: Browser verification**

Start the backend/preview and open the scan review UI (the step that renders `ParsedCardEditor` for a parsed card). Hard-refresh (Cmd+Shift+R) to bust the cached bundle. Verify:
1. A card with 2+ name variants shows **exactly one** "Personal" section, with each name as a nested row (language chip + type + remove).
2. Personal contacts appear **once**, below the name rows.
3. The Birthday row shows Month/Day dropdowns + a Year field. Selecting Month=5, Day=20, Year blank, then saving/advancing, persists and reloads as 5 / 20 / blank. Adding a year (e.g. 1990) round-trips too. Clearing Month empties the birthday.
4. Organization sections are unchanged; personal↔work drag-and-drop still works.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/ParsedCardEditor.tsx
git commit -m "feat: unify Personal category into a single section with nested names + birthday"
```

---

## End-to-end verification (after all tasks)

- [ ] Run the full Python test suite: `venv/bin/pytest tests/test_birthday.py -v` → all PASS.
- [ ] Confirm no regressions in existing tests: `venv/bin/pytest -q`.
- [ ] Manual end-to-end: scan/confirm a card with a birthday entered → confirm the DB `persons.birthday` is populated → trigger a Google Contacts sync/export for that person → verify the contact in Google Contacts shows the birthday (full date and, in a second case, a month/day-only birthday).

---

## Self-Review

**Spec coverage:**
- §1 single Personal section, nested name rows, contacts once → Task 11. ✅
- §1a `BirthdayField` (Month/Day dropdowns, optional numeric Year, serialize/deserialize, clear) → Task 10. ✅
- §2 i18n (`personalSection`, relabel `addNameLabel`, `fieldBirthday`) → Task 9. ✅
- §3 storage chain: `ParsedCard` (Task 1), TS type (Task 8), DB column + migration (Task 5), sessions persist (Task 6), pydantic `Person` (Task 3), export copy (Task 7). ✅
- §4 parser prompt + passthrough → Task 2. ✅
- §5 Google `birthdays[]` + year-optional parse → Task 4. ✅
- Non-goal (no Odoo) → no Odoo task. ✅

**Placeholder scan:** No TBD/TODO. The one lookup note (Task 2, confirming the parser function name) includes the exact grep to resolve it and the anchor (`return ParsedCard(` at line 221). No vague "add validation" steps — validation is structural (dropdowns/digit-filter).

**Type consistency:** `serializeBirthday`/`parseBirthday` signatures match their call sites in `BirthdayField`. `_parse_birthday` returns a dict consumed as `body["birthdays"][0]["date"]`. `person.birthday` is `str` (pydantic, default `""`) everywhere it's read in `_build_person_body`/`export.py`, and `Optional[str]` on the DB model and `ParsedCard`. `ParsedCard.birthday` is `undefined`-able in TS and `None`-able in Python; the editor coerces `v || undefined` so empty never persists as `""`.
