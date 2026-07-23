# Unify "Personal" Category + Add Birthday Field

**Date:** 2026-07-23
**Status:** Design approved, pending implementation plan

## Problem

The card review/edit UI (`ParsedCardEditor.tsx`) renders **one "Personal N" section per name variant**. A person with a Chinese name and an English name gets "Personal 1", "Personal 2", etc. This is misleading:

- The underlying data model already stores personal contacts (mobile, personal email, home address, socials, relationship) as a **flat, person-level `contact_details[]` list** — not per name.
- The UI already renders those personal contacts **only under the first name** (`i === 0`), so "Personal 2" / "Personal 3" are empty name-variant boxes labeled "Personal", which reads as if each name has its own personal category.

Separately, there is **no birthday field** anywhere in the pipeline (schema, models, parser, UI, or sync).

## Goals

1. Present a **single "Personal" category** per person, with the multiple name variants nested inside it and the person-level fields shown once.
2. Add a **birthday** field, stored in the app and synced to **Google Contacts**.

## Non-Goals

- **Odoo birthday sync.** `res.partner` has no standard birthday field; we are not adding an `x_studio_birthday` custom field at this time.
- No change to the personal/work contact partitioning logic, drag-and-drop between sections, or the Organization sections.
- No change to how name variants are extracted or matched.

## Design

### 1. UI restructure — `frontend/src/components/ParsedCardEditor.tsx`

This is a **UI-only** change for the unification part; the data model already stores contacts at person-level.

- Replace the `parsed.names.map(...)` block (currently rendering N `"Personal N"` sections) with **one `"Personal"` section**.
- Inside that single section, render name variants as **nested rows**. Each row keeps its existing controls: language chip, `name_type`, and full/family/given `FieldRow`s, plus a remove button (shown only when `names.length > 1`). The `+ Add name` button stays.
- Render the personal-contacts `ContactSubSection` **once**, below the name rows. Remove the `i === 0` special-case guard.
- Add a **Birthday** row at the bottom of the section, rendered by a **new dedicated `BirthdayField` component** (not `FieldRow` — see §1a), bound to the new top-level `parsed.birthday` value.
- Update the header comment block at the top of the file to describe the new single-Personal layout.

### 1a. Birthday input component — `BirthdayField`

`FieldRow` is a plain free-text `<input>` with no validation, which is unsuitable for a date. Birthday gets its own small component so invalid values are impossible to enter and the year-less case is supported.

- **Month** — a `<select>` dropdown, options 1–12 (display can be localized month names; value is the zero-padded number). Required for a non-empty birthday.
- **Day** — a `<select>` dropdown, options 1–31. Required for a non-empty birthday. (Day options are **not** narrowed by month; over-long days like Feb 30 are tolerated rather than dynamically pruned — keeps the component simple, and Google's `Date` accepts them. Optional refinement, not required: narrow day count to the selected month.)
- **Year** — an **optional** numeric text `<input>` (`inputMode="numeric"`, digits only, 4-digit). Left blank ⇒ year-unknown.
- **Serialization to `parsed.birthday`:**
  - Year present ⇒ `YYYY-MM-DD`.
  - Year blank ⇒ `--MM-DD`.
  - Month/Day both empty ⇒ empty string / unset (no birthday).
- **Deserialization:** parse an incoming `YYYY-MM-DD` or `--MM-DD` string back into the three controls; unparseable/empty ⇒ all controls empty.
- **Clear affordance:** a way to reset the field to empty (e.g. an "empty" option at the top of the Month/Day selects, or a small ✕), so a birthday can be removed.
- Placed in the same file as the editor (or a sibling component file), reusing existing Tailwind styling for visual consistency with `FieldRow`.

### 2. i18n — `frontend/src/i18n.ts`

- Replace the numbered `nameSection(n)` label with a single **`personalSection`** label ("個人" / "Personal") for the section header.
- Keep the per-name-row sub-label showing the name index/type if useful, but the **section** is no longer numbered.
- Relabel `addNameLabel` to a name-oriented string ("＋ 名前を追加" / "+ Add name") — it adds a name variant, not a "Personal".
- Add **`fieldBirthday`** ("誕生日" / "Birthday", plus the zh-TW label "生日").

### 3. Birthday storage

Birthday is a per-person value. It must be persisted in the DB and travel through the same two-representation path the app already uses (`ParsedCard` for input/editing; the `app/models/card.py` `Card`/`Person` for Google export). The concrete chain:

1. **`app/schemas/parsed_card.py`** — add a top-level field to `ParsedCard`:
   `birthday: Optional[str] = None`  (`YYYY-MM-DD`; **year-optional**, stored as `--MM-DD` when the year is unknown, since a birthday is commonly month/day only).
2. **`frontend/src/types/index.ts`** — mirror it on the `ParsedCard` interface: `birthday?: string`.
3. **`app/db/models.py`** — add a `birthday: Mapped[Optional[str]] = mapped_column(String(16))` column to the `Person` ORM model (`persons` table). String, not `Date`, to allow the year-less `--MM-DD` form.
4. **Alembic migration** — new revision under `migrations/` adding the `persons.birthday` column (nullable; no backfill needed).
5. **`app/routers/v2/sessions.py`** — when creating/updating the DB `Person` (around the `Person(...)` construction at ~L662 and the upsert path at ~L733), persist `draft.parsed.birthday` onto `person.birthday`.
6. **`app/models/card.py`** — add `birthday: str = ""` to the pydantic `Person` model (the representation consumed by Google sync).
7. **`app/routers/v2/export.py`** — when assembling the `LegacyCard` from DB records (~L160), copy `db_person.birthday` into the pydantic `Person.birthday`.

### 4. Parser — `app/services/claude_parser.py`

- Add `birthday` to the Claude extraction prompt/schema so it is captured when printed on a card. This is rare on business cards, so it is low priority — **manual entry in the editor is the primary path** — but including it keeps the pipeline consistent.

### 5. Google Contacts sync — `app/services/google_contacts.py`

- In `_build_person_body`, when `person.birthday` is set, add:
  `body["birthdays"] = [{"date": <Date object>, "text": person.birthday}]`
- Reuse the existing `_parse_iso_date` helper, **extended so the year is optional** (the People API accepts a `Date` with `month`/`day` and no `year`). For a stored `--MM-DD`, emit `{"month": M, "day": D}` with no `year` key.
- No extra sync wiring needed: `updatePersonFields` is auto-derived from `body.keys()` in `sync_to_google`, so `birthdays` is included on updates automatically.

## Data Flow

```
Card image / manual entry
  → claude_parser   (ParsedCard.birthday extracted, or blank)
  → editor UI       (ParsedCardEditor: single Personal section, birthday field)
  → sessions.py     (persist ParsedCard.birthday → DB persons.birthday)
  → export.py       (DB persons.birthday → pydantic Person.birthday in LegacyCard)
  → google_contacts._build_person_body → body["birthdays"]
  → People API (create/update)
```

## Edge Cases

- **Year unknown:** entered by leaving the `BirthdayField` year input blank; stored as `--MM-DD`. Google `Date` emitted without a `year` key.
- **Invalid input:** prevented at entry — month/day are dropdowns (no free text), year accepts digits only. No separate validation pass needed.
- **No birthday:** field absent from `body`, so nothing is written/cleared incorrectly (it simply is not among `body.keys()`).
- **Multiple names, zero names:** the single Personal section renders whatever name rows exist (including none); the `+ Add name` button always allows adding one.
- **Existing cards without birthday:** `Optional`/default-empty fields keep them valid; no migration required.

## Testing

- **UI:** a person with 2+ name variants renders exactly one "Personal" section with all name rows nested and personal contacts shown once; adding/removing name rows works.
- **BirthdayField:** month/day dropdowns reject invalid values by construction; year accepts digits only; full date serializes to `YYYY-MM-DD`, year-blank to `--MM-DD`, empty to unset; an existing stored value deserializes back into the three controls; clearing removes the birthday. Round-trips into `parsed.birthday`.
- **Schema/model:** `ParsedCard` and both `Person` representations accept and preserve `birthday` (full date and year-less).
- **Migration:** Alembic upgrade adds `persons.birthday` cleanly on the existing DB; a birthday entered in the editor round-trips through DB persistence and back out via `export.py`.
- **Google sync:** `_build_person_body` produces a correct `birthdays[]` entry for both full and year-less dates, and omits it when blank; `_parse_iso_date` handles the year-optional case.
- **Regression:** no change to work/organization sections, personal↔work drag-and-drop, or contact partitioning.

## Files Touched

| File | Change |
|------|--------|
| `frontend/src/components/ParsedCardEditor.tsx` | Single Personal section, nested name rows; render `BirthdayField` |
| `BirthdayField` component (in editor file or sibling) | Month/Day dropdowns + optional numeric Year; serialize `YYYY-MM-DD` / `--MM-DD` |
| `frontend/src/i18n.ts` | `personalSection`, relabel `addNameLabel`, add `fieldBirthday` |
| `frontend/src/types/index.ts` | `birthday?: string` on `ParsedCard` |
| `app/schemas/parsed_card.py` | `birthday: Optional[str]` on `ParsedCard` |
| `app/db/models.py` | `birthday` column on `Person` (`persons` table) |
| `migrations/` | New Alembic revision adding `persons.birthday` |
| `app/routers/v2/sessions.py` | Persist `parsed.birthday` onto DB `Person` |
| `app/models/card.py` | `birthday: str` on pydantic `Person` |
| `app/routers/v2/export.py` | Copy `db_person.birthday` into `LegacyCard` Person |
| `app/services/claude_parser.py` | Birthday extraction in prompt/schema |
| `app/services/google_contacts.py` | `birthdays[]` in `_build_person_body`; year-optional `_parse_iso_date` |
