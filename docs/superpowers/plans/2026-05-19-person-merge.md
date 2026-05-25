# Person Merge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users select 2+ Person records on the CollectionPage persons view, pick a primary, and merge all cards/names/contact details into the surviving record.

**Architecture:** New `POST /api/v2/persons/{primary_ext_id}/merge` endpoint does all work in a single DB transaction (reassign rows, concatenate notes, count duplicates, delete sources). Frontend adds select-mode checkboxes + floating action bar to CollectionPage, plus a 2-step MergeModal.

**Tech Stack:** FastAPI + SQLAlchemy (async), React 19, TanStack Query, Tailwind CSS

---

## File Map

| File | Change |
|------|--------|
| `app/schemas/api.py` | Add `MergeRequest`, `MergeResult` Pydantic models |
| `app/routers/v2/persons.py` | Add `merge_persons` endpoint |
| `tests/test_person_merge.py` | New — backend unit tests |
| `frontend/src/types/index.ts` | Add `MergeResult` interface |
| `frontend/src/api/index.ts` | Add `mergePersons()` function |
| `frontend/src/i18n.ts` | Add merge i18n keys (ja + en) |
| `frontend/src/components/MergeModal.tsx` | New — 2-step merge modal |
| `frontend/src/pages/CollectionPage.tsx` | Add select mode, action bar, MergeModal wiring |

---

## Task 1: Backend schemas

**Files:**
- Modify: `app/schemas/api.py`

- [ ] **Step 1: Add MergeRequest and MergeResult to schemas**

Open `app/schemas/api.py` and add at the end of the file:

```python
class MergeRequest(BaseModel):
    source_ids: List[str]  # external_ids of persons to be merged INTO primary


class MergeResult(BaseModel):
    person: PersonOut
    duplicate_contact_count: int
```

Make sure `List` is imported (it already is if the file uses `List` elsewhere; otherwise add `from typing import List`).

- [ ] **Step 2: Verify import**

```bash
cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi"
python3 -c "from app.schemas.api import MergeRequest, MergeResult; print('OK')"
```

Expected output: `OK`

- [ ] **Step 3: Commit**

```bash
git add app/schemas/api.py
git commit -m "feat: add MergeRequest and MergeResult schemas"
```

---

## Task 2: Backend merge endpoint

**Files:**
- Modify: `app/routers/v2/persons.py`

- [ ] **Step 1: Write the failing test first**

Create `tests/test_person_merge.py`:

```python
"""Unit tests for merge_persons endpoint signature and logic."""
import inspect
import pytest


def test_merge_endpoint_exists():
    from app.routers.v2.persons import merge_persons
    sig = inspect.signature(merge_persons)
    assert 'primary_ext_id' in sig.parameters
    assert 'body' in sig.parameters


def test_merge_request_schema():
    from app.schemas.api import MergeRequest
    req = MergeRequest(source_ids=["abc", "def"])
    assert req.source_ids == ["abc", "def"]


def test_merge_result_schema():
    from app.schemas.api import MergeResult, PersonOut
    # MergeResult requires a PersonOut — just verify it accepts duplicate_contact_count
    import inspect
    sig = inspect.signature(MergeResult)
    assert 'duplicate_contact_count' in sig.parameters
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi"
python3 -m pytest tests/test_person_merge.py -v
```

Expected: `test_merge_endpoint_exists` FAILS with ImportError; others PASS.

- [ ] **Step 3: Implement the merge endpoint**

Add to `app/routers/v2/persons.py` after the `delete_person` endpoint (around line 227):

```python
@router.post("/{primary_ext_id}/merge", response_model=MergeResult)
async def merge_persons(
    primary_ext_id: str,
    body: MergeRequest,
    db: AsyncSession = Depends(get_db),
):
    """Merge N source persons into primary. All cards, names, contact details,
    and positions are reassigned. Sources are deleted. Returns merged PersonOut
    and count of duplicate contact details detected."""

    # Load primary
    primary = await db.scalar(select(Person).where(Person.external_id == primary_ext_id))
    if not primary:
        raise HTTPException(404, "Primary person not found")

    # Filter out primary from source_ids (idempotent)
    source_ext_ids = [sid for sid in body.source_ids if sid != primary_ext_id]
    if not source_ext_ids:
        return MergeResult(
            person=await _load_person_out(db, primary),
            duplicate_contact_count=0,
        )

    # Load source persons — 404 if any missing
    sources = []
    for ext_id in source_ext_ids:
        p = await db.scalar(select(Person).where(Person.external_id == ext_id))
        if not p:
            raise HTTPException(404, f"Source person not found: {ext_id}")
        sources.append(p)

    source_ids = [p.id for p in sources]

    # Reassign all child rows to primary
    for table, col in [
        (Card, Card.person_id),
        (PersonName, PersonName.person_id),
        (ContactDetail, ContactDetail.person_id),
        (Position, Position.person_id),
    ]:
        await db.execute(
            update(table)
            .where(col.in_(source_ids))
            .values({col: primary.id})
        )

    # Concatenate notes
    source_notes = [p.notes for p in sources if p.notes]
    if source_notes:
        existing = primary.notes or ""
        combined = "\n".join(filter(None, [existing] + source_notes))
        primary.notes = combined

    await db.flush()

    # Count duplicate contact details: same (detail_type, lower(trim(value)))
    dup_count_row = await db.execute(
        select(func.count())
        .select_from(
            select(ContactDetail.detail_type, func.lower(func.trim(ContactDetail.value)))
            .where(ContactDetail.person_id == primary.id)
            .group_by(ContactDetail.detail_type, func.lower(func.trim(ContactDetail.value)))
            .having(func.count() > 1)
            .subquery()
        )
    )
    duplicate_contact_count = dup_count_row.scalar() or 0

    # Delete source persons (cascade handles PersonRelationship)
    for p in sources:
        await db.delete(p)

    await db.flush()

    person_out = await _load_person_out(db, primary)
    return MergeResult(person=person_out, duplicate_contact_count=duplicate_contact_count)
```

Also update the SQLAlchemy import line at the top of `persons.py` to add `func` and `update`:

```python
from sqlalchemy import func, or_, select, update
```

And add the missing model imports — `Card` and `ContactDetail` from `app.db.models`:

```python
from app.db.models import (
    Card,
    ContactDetail,
    Organization,
    OrganizationName,
    Person,
    PersonName,
    Position,
    PositionDetail,
)
```

And add to the schemas import:
```python
from app.schemas.api import (
    ContactDetailOut,
    MergeRequest,
    MergeResult,
    OrgNameOut,
    PersonCreate,
    PersonListItem,
    PersonNameOut,
    PersonOut,
    PositionDetailOut,
    PositionOut,
)
```

- [ ] **Step 4: Run tests — all should pass**

```bash
cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi"
python3 -m pytest tests/test_person_merge.py -v
```

Expected: 3 tests PASS.

- [ ] **Step 5: Smoke-test the import**

```bash
python3 -c "from app.routers.v2.persons import merge_persons; print('OK')"
```

Expected: `OK`

- [ ] **Step 6: Deploy and verify endpoint exists**

```bash
./deploy.sh
sleep 3
curl -s -o /dev/null -w "%{http_code}" -X POST \
  http://localhost:8000/api/v2/persons/nonexistent/merge \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $(grep VITE_API_KEY .env | cut -d= -f2)" \
  -d '{"source_ids":[]}'
```

Expected: `404` (primary not found — proves endpoint is live).

- [ ] **Step 7: Commit**

```bash
git add app/routers/v2/persons.py app/schemas/api.py tests/test_person_merge.py
git commit -m "feat: add POST /api/v2/persons/{id}/merge endpoint"
```

---

## Task 3: Frontend types + API client

**Files:**
- Modify: `frontend/src/types/index.ts`
- Modify: `frontend/src/api/index.ts`

- [ ] **Step 1: Add MergeResult type**

In `frontend/src/types/index.ts`, add after the `PersonListItem` interface (around line 204):

```typescript
export interface MergeResult {
  person: Person
  duplicate_contact_count: number
}
```

- [ ] **Step 2: Add mergePersons API function**

In `frontend/src/api/index.ts`, add after `deletePerson` (around line 65):

```typescript
export const mergePersons = (primaryExtId: string, sourceIds: string[]) =>
  post<import('../types').MergeResult>(`/api/v2/persons/${primaryExtId}/merge`, { source_ids: sourceIds })
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi/frontend"
bun run tsc --noEmit 2>&1 | head -20
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/api/index.ts
git commit -m "feat: add MergeResult type and mergePersons API call"
```

---

## Task 4: i18n keys

**Files:**
- Modify: `frontend/src/i18n.ts`

- [ ] **Step 1: Add Japanese keys**

In `frontend/src/i18n.ts`, in the `ja` object, add after `personDeleted` (around line 143):

```typescript
    // Merge
    selectPersonsBtn: '選択',
    cancelSelectBtn: 'キャンセル',
    selectedN: (n: number) => `${n} 件選択中`,
    mergeSelectedBtn: (n: number) => `${n} 件をマージ`,
    mergeModalTitle: 'どのレコードを残しますか？',
    mergeModalConfirmTitle: (name: string) => `「${name}」にマージしますか？`,
    mergeModalConfirmBody: (n: number) => `他の ${n} 件のレコードから名刺・名前・連絡先を移動します。それらのレコードは削除されます。`,
    mergeConfirmBtn: 'マージする',
    mergingBtn: 'マージ中…',
    mergeSucceeded: 'マージしました',
    mergeDuplicatesFound: (n: number) => `${n} 件の重複連絡先が見つかりました — 確認してください`,
    mergeError: 'マージに失敗しました',
```

- [ ] **Step 2: Add English keys**

In the `en` object, add after `personDeleted` (around line 354 in the en section):

```typescript
    // Merge
    selectPersonsBtn: 'Select',
    cancelSelectBtn: 'Cancel',
    selectedN: (n: number) => `${n} selected`,
    mergeSelectedBtn: (n: number) => `Merge ${n} persons`,
    mergeModalTitle: 'Which record is primary?',
    mergeModalConfirmTitle: (name: string) => `Merge into "${name}"?`,
    mergeModalConfirmBody: (n: number) => `Cards, names, and contact details from the other ${n} record${n !== 1 ? 's' : ''} will be moved here. Those records will be permanently deleted.`,
    mergeConfirmBtn: 'Confirm Merge',
    mergingBtn: 'Merging…',
    mergeSucceeded: 'Merged successfully',
    mergeDuplicatesFound: (n: number) => `${n} duplicate contact detail${n !== 1 ? 's' : ''} found — review below`,
    mergeError: 'Merge failed',
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi/frontend"
bun run tsc --noEmit 2>&1 | head -20
```

Expected: no errors. If you get a type error about missing keys, both objects must have identical keys — check spelling.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/i18n.ts
git commit -m "feat: add merge i18n keys (ja + en)"
```

---

## Task 5: MergeModal component

**Files:**
- Create: `frontend/src/components/MergeModal.tsx`

- [ ] **Step 1: Create the component**

Create `frontend/src/components/MergeModal.tsx`:

```typescript
/**
 * MergeModal — 2-step modal for merging Person records.
 *
 * Step 1: user picks which person is the primary (surviving) record.
 * Step 2: confirmation before destructive merge.
 */
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { mergePersons } from '../api'
import { useToast } from './Toast'
import { useLang } from '../LangContext'
import type { PersonListItem } from '../types'

interface Props {
  selected: PersonListItem[]
  onClose: () => void
}

export function MergeModal({ selected, onClose }: Props) {
  const { t } = useLang()
  const { showToast } = useToast()
  const qc = useQueryClient()
  const [step, setStep] = useState<1 | 2>(1)
  const [primaryId, setPrimaryId] = useState<string>(selected[0]?.external_id ?? '')

  const primary = selected.find(p => p.external_id === primaryId)
  const sources = selected.filter(p => p.external_id !== primaryId)

  const mutation = useMutation({
    mutationFn: () =>
      mergePersons(primaryId, sources.map(p => p.external_id)),
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ['persons'] })
      qc.invalidateQueries({ queryKey: ['cards'] })
      const msg = result.duplicate_contact_count > 0
        ? t.mergeDuplicatesFound(result.duplicate_contact_count)
        : t.mergeSucceeded
      showToast(msg)
      window.location.href = `/persons/${result.person.external_id}`
    },
    onError: () => {
      showToast(t.mergeError, 'error')
      onClose()
    },
  })

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />

      <div className="relative bg-white rounded-t-2xl sm:rounded-2xl w-full sm:max-w-md mx-0 sm:mx-4 p-6 space-y-5 shadow-xl">
        {step === 1 ? (
          <>
            <h2 className="text-lg font-semibold text-gray-900">{t.mergeModalTitle}</h2>
            <div className="space-y-2">
              {selected.map(p => (
                <label
                  key={p.external_id}
                  className={`flex items-center gap-3 p-3 rounded-xl border cursor-pointer transition-colors ${
                    primaryId === p.external_id
                      ? 'border-blue-500 bg-blue-50'
                      : 'border-gray-200 hover:bg-gray-50'
                  }`}
                >
                  <input
                    type="radio"
                    name="primary"
                    value={p.external_id}
                    checked={primaryId === p.external_id}
                    onChange={() => setPrimaryId(p.external_id)}
                    className="accent-blue-600"
                  />
                  <div className="w-8 h-8 rounded-full bg-blue-100 text-blue-700 flex items-center justify-center text-sm font-medium shrink-0">
                    {(p.primary_name ?? '?').charAt(0)}
                  </div>
                  <span className="text-sm font-medium text-gray-900">
                    {p.primary_name ?? '(No name)'}
                  </span>
                </label>
              ))}
            </div>
            <div className="flex justify-end gap-3">
              <button onClick={onClose} className="btn-secondary text-sm">{t.cancelBtn}</button>
              <button
                onClick={() => setStep(2)}
                disabled={!primaryId}
                className="btn-primary text-sm"
              >
                Next →
              </button>
            </div>
          </>
        ) : (
          <>
            <h2 className="text-lg font-semibold text-gray-900">
              {t.mergeModalConfirmTitle(primary?.primary_name ?? '')}
            </h2>
            <p className="text-sm text-gray-600">
              {t.mergeModalConfirmBody(sources.length)}
            </p>
            <div className="flex justify-between items-center">
              <button onClick={() => setStep(1)} className="text-sm text-blue-500 hover:text-blue-700">
                ← Back
              </button>
              <button
                onClick={() => mutation.mutate()}
                disabled={mutation.isPending}
                className="btn-primary text-sm bg-red-600 hover:bg-red-700 disabled:opacity-50"
              >
                {mutation.isPending ? t.mergingBtn : t.mergeConfirmBtn}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi/frontend"
bun run tsc --noEmit 2>&1 | head -20
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/MergeModal.tsx
git commit -m "feat: add MergeModal component (2-step merge UI)"
```

---

## Task 6: CollectionPage select mode + action bar

**Files:**
- Modify: `frontend/src/pages/CollectionPage.tsx`

- [ ] **Step 1: Add select-mode state and imports**

At the top of `CollectionPage.tsx`, add `MergeModal` to the imports:

```typescript
import { MergeModal } from '../components/MergeModal'
```

Inside `CollectionPage()`, add state after the existing `useState` declarations:

```typescript
const [selectMode, setSelectMode] = useState(false)
const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
const [showMergeModal, setShowMergeModal] = useState(false)

const toggleSelect = (extId: string) =>
  setSelectedIds(prev => {
    const next = new Set(prev)
    next.has(extId) ? next.delete(extId) : next.add(extId)
    return next
  })

const exitSelectMode = () => {
  setSelectMode(false)
  setSelectedIds(new Set())
}

const selectedPersons = persons.filter(p => selectedIds.has(p.external_id))
```

- [ ] **Step 2: Add Select button to persons view header**

In the tab row section (around line 141–155), add a "Select" button that only shows when `view === 'persons'`. Replace the current tab buttons block with:

```typescript
      <div className="flex items-center gap-2">
        <div className="flex rounded-lg border border-gray-200 overflow-hidden text-sm">
          <button
            onClick={() => { setView('cards'); exitSelectMode() }}
            className={`px-3 py-1.5 ${view === 'cards' ? 'bg-blue-600 text-white' : 'text-gray-600 hover:bg-gray-50'}`}
          >
            {t.tabCards}
          </button>
          <button
            onClick={() => setView('persons')}
            className={`px-3 py-1.5 ${view === 'persons' ? 'bg-blue-600 text-white' : 'text-gray-600 hover:bg-gray-50'}`}
          >
            {t.tabPersons}
          </button>
        </div>
        <input
          type="search"
          placeholder={t.searchPlaceholder}
          value={q}
          onChange={e => setQ(e.target.value)}
          className="flex-1 border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        {view === 'persons' && !selectMode && (
          <button
            onClick={() => setSelectMode(true)}
            className="text-sm px-3 py-1 rounded border border-gray-300 text-gray-600 hover:bg-gray-50 transition-colors"
          >
            {t.selectPersonsBtn}
          </button>
        )}
        {view === 'persons' && selectMode && (
          <button
            onClick={exitSelectMode}
            className="text-sm px-3 py-1 rounded border border-gray-300 text-gray-600 hover:bg-gray-50 transition-colors"
          >
            {t.cancelSelectBtn}
          </button>
        )}
        <a
          href="/export"
          className="text-sm px-3 py-1 rounded border border-gray-300 text-gray-600 hover:bg-gray-50 transition-colors"
        >
          {t.exportBtn}
        </a>
      </div>
```

- [ ] **Step 3: Update the persons list to support select mode**

In the persons list section (around line 243–261), replace each person `<a>` tag with a conditional element that renders a `<div>` with checkbox in select mode, or the original `<a>` link otherwise:

```typescript
                    <div className="ml-4 divide-y divide-gray-100 rounded-xl border border-gray-200 bg-white">
                      {group.map(p => {
                        const isSelected = selectedIds.has(p.external_id)
                        if (selectMode) {
                          return (
                            <div
                              key={p.id}
                              onClick={() => toggleSelect(p.external_id)}
                              className={`flex items-center gap-3 px-4 py-3 cursor-pointer transition-colors ${isSelected ? 'bg-blue-50' : 'hover:bg-gray-50'}`}
                            >
                              <input
                                type="checkbox"
                                checked={isSelected}
                                onChange={() => toggleSelect(p.external_id)}
                                onClick={e => e.stopPropagation()}
                                className="accent-blue-600 w-4 h-4 shrink-0"
                              />
                              <div className="w-8 h-8 rounded-full bg-blue-100 text-blue-700 flex items-center justify-center text-sm font-medium shrink-0">
                                {(p.primary_name ?? '?').charAt(0)}
                              </div>
                              <div>
                                <p className="text-sm font-medium text-gray-900">{p.primary_name ?? t.noName}</p>
                                <p className="text-xs text-gray-400">{new Date(p.created_at).toLocaleDateString()}</p>
                              </div>
                            </div>
                          )
                        }
                        return (
                          <a
                            key={p.id}
                            href={`/persons/${p.external_id}`}
                            className="flex items-center gap-3 px-4 py-3 hover:bg-gray-50 transition-colors"
                          >
                            <div className="w-8 h-8 rounded-full bg-blue-100 text-blue-700 flex items-center justify-center text-sm font-medium shrink-0">
                              {(p.primary_name ?? '?').charAt(0)}
                            </div>
                            <div>
                              <p className="text-sm font-medium text-gray-900">{p.primary_name ?? t.noName}</p>
                              <p className="text-xs text-gray-400">{new Date(p.created_at).toLocaleDateString()}</p>
                            </div>
                          </a>
                        )
                      })}
                    </div>
```

- [ ] **Step 4: Add floating action bar and MergeModal**

Just before the closing `</div>` of the whole CollectionPage return (after the persons/cards conditional blocks, around line 268), add:

```typescript
      {/* Floating action bar — visible in select mode with ≥2 selected */}
      {selectMode && selectedIds.size >= 2 && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 flex items-center gap-3 bg-white border border-gray-200 rounded-2xl shadow-xl px-5 py-3 z-40">
          <span className="text-sm text-gray-600">{t.selectedN(selectedIds.size)}</span>
          <button
            onClick={() => setShowMergeModal(true)}
            className="btn-primary text-sm"
          >
            {t.mergeSelectedBtn(selectedIds.size)}
          </button>
        </div>
      )}

      {/* Merge modal */}
      {showMergeModal && selectedPersons.length >= 2 && (
        <MergeModal
          selected={selectedPersons}
          onClose={() => {
            setShowMergeModal(false)
            exitSelectMode()
          }}
        />
      )}
```

- [ ] **Step 5: Verify TypeScript compiles**

```bash
cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi/frontend"
bun run tsc --noEmit 2>&1 | head -20
```

Expected: no errors.

- [ ] **Step 6: Build frontend**

```bash
bun run build 2>&1 | tail -10
```

Expected: build succeeds with no errors.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/CollectionPage.tsx
git commit -m "feat: add select mode and merge action bar to CollectionPage"
```

---

## Task 7: Deploy and smoke test

- [ ] **Step 1: Deploy backend**

```bash
cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi"
./deploy.sh
```

- [ ] **Step 2: Verify merge endpoint is live**

```bash
curl -s \
  -X POST http://localhost:8000/api/v2/persons/doesnotexist/merge \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $(grep VITE_API_KEY .env | cut -d= -f2)" \
  -d '{"source_ids":["also-doesnotexist"]}' \
  | python3 -m json.tool
```

Expected: `{"detail": "Primary person not found"}`

- [ ] **Step 3: Open the app and test the flow manually**

1. Navigate to Collection → Persons tab
2. Tap "Select" — checkboxes should appear on all person rows
3. Check 2 persons — floating action bar should appear: "2 selected" + "Merge 2 persons"
4. Tap "Merge 2 persons" — MergeModal Step 1 opens showing both names as radio options
5. Select one as primary, tap "Next →" — Step 2 shows confirmation text
6. Tap "Confirm Merge" — navigates to surviving PersonDetailPage, toast appears
7. Verify both business card thumbnails appear in the linked cards section

- [ ] **Step 4: Final commit if any fixes were made during testing**

```bash
git add -p  # stage only relevant changes
git commit -m "fix: merge flow smoke test corrections"
```
