# Manual Person Entry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow users to create a person record (with names, company, contacts, Met As, Occasion, date) without scanning a business card.

**Architecture:** Reuse the existing scan review UI by detecting a `?manual=1` query param on ScanPage — skipping upload/group/analyze and jumping straight to review with a blank card group. On save, the existing confirm endpoint creates a Person + Card with no CardSide records. One backend guard needs loosening to allow confirm from a freshly-created session.

**Tech Stack:** FastAPI (Python), React 19, TypeScript, TanStack Query, Tailwind CSS, Bun

---

## File Map

| File | Change |
|---|---|
| `app/routers/v2/sessions.py` | Allow confirm from "uploading"/"grouping" status |
| `frontend/src/i18n.ts` | Add `enterManuallyBtn`, `manualEntryTitle` |
| `frontend/src/pages/CollectionPage.tsx` | Add `+ Enter Manually` button |
| `frontend/src/pages/ScanPage.tsx` | Manual mode: skip to review, blank group, hide image area |

---

## Task 1: Backend — allow confirm on a fresh session

The `confirm_session` endpoint rejects sessions not in `"analyzing"` or `"review"` state. A manually-created session stays in `"uploading"` state (no images were uploaded). Add those states to the allowed set.

**Files:**
- Modify: `app/routers/v2/sessions.py:713-714`

- [ ] **Step 1: Open sessions.py and find the status guard in confirm_session**

  The relevant lines are in `confirm_session` (around line 713):
  ```python
  if session.status not in ("analyzing", "review"):
      raise HTTPException(400, "Session must be in analyzing or review state to confirm")
  ```

- [ ] **Step 2: Expand the allowed statuses**

  Replace those two lines with:
  ```python
  if session.status not in ("uploading", "grouping", "analyzing", "review"):
      raise HTTPException(400, "Session is not in a confirmable state")
  ```

- [ ] **Step 3: Deploy**

  ```bash
  cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi"
  ./deploy.sh
  ```

  Expected: deploy completes, backend restarts with no errors.

---

## Task 2: i18n — add manual entry strings

**Files:**
- Modify: `frontend/src/i18n.ts`

- [ ] **Step 1: Add EN strings**

  In the `en` block, after the `navExport` key (around line 153), add:
  ```ts
  enterManuallyBtn: '+ Enter Manually',
  manualEntryTitle: 'Enter Person Details',
  ```

- [ ] **Step 2: Add JA strings**

  In the `ja` block, after the matching `navExport` key (around line 153), add:
  ```ts
  enterManuallyBtn: '＋ 手動入力',
  manualEntryTitle: '人物情報を入力',
  ```

- [ ] **Step 3: Verify TypeScript compiles**

  ```bash
  cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi/frontend"
  bun run build 2>&1 | tail -5
  ```

  Expected: `✓ built in ...ms` with no errors.

---

## Task 3: CollectionPage — add "Enter Manually" button

**Files:**
- Modify: `frontend/src/pages/CollectionPage.tsx:89-92`

- [ ] **Step 1: Find the header row**

  The current header (around line 89):
  ```tsx
  <div className="flex items-center gap-3">
    <h1 className="text-lg font-semibold text-gray-900 flex-1">{t.collectionTitle}</h1>
    <a href="/scan" className="btn-primary text-sm">{t.newScanBtn}</a>
  </div>
  ```

- [ ] **Step 2: Add the secondary button before New Scan**

  Replace with:
  ```tsx
  <div className="flex items-center gap-3">
    <h1 className="text-lg font-semibold text-gray-900 flex-1">{t.collectionTitle}</h1>
    <a href="/scan?manual=1" className="btn-secondary text-sm">{t.enterManuallyBtn}</a>
    <a href="/scan" className="btn-primary text-sm">{t.newScanBtn}</a>
  </div>
  ```

  `btn-secondary` is defined in `frontend/src/index.css:14` — use it as-is.

- [ ] **Step 3: Build and verify**

  ```bash
  cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi/frontend"
  bun run build 2>&1 | tail -5
  ```

  Expected: `✓ built in ...ms`.

---

## Task 4: ScanPage — manual mode

This is the main task. Four sub-changes, all in `frontend/src/pages/ScanPage.tsx`:

1. Detect `?manual=1` on mount and initialize state differently
2. Pass `isManual` into `CardGroupCard`
3. Hide the image area when `isManual` and group has no images
4. Show a simplified stage bar in manual mode

**Files:**
- Modify: `frontend/src/pages/ScanPage.tsx`

---

### 4a: Detect manual mode and initialize blank group

- [ ] **Step 1: Add `isManual` state**

  After the existing state declarations (around line 124), add:
  ```tsx
  const isManual = new URLSearchParams(window.location.search).get('manual') === '1'
  ```

  This is a derived constant (not state) since the URL doesn't change during the session.

- [ ] **Step 2: Modify the mount useEffect to handle manual mode**

  The current useEffect (lines 145–191) handles resume-or-create. Add a manual-mode branch at the **top** of the else block (when there's no stored session), before the existing `createSession()` call:

  ```tsx
  useEffect(() => {
    const stored = sessionStorage.getItem('scan_session_id')
    if (stored) {
      // ... existing resume logic unchanged ...
    } else if (isManual) {
      // Manual entry: create session, jump straight to review with one blank group
      createSession().then(s => {
        sessionStorage.setItem('scan_session_id', s.external_id)
        setSession(s)
        const blankId = crypto.randomUUID()
        setGroups([{
          tempCardId: blankId,
          images: [],
          myCompanyIds: [],
          occasionId: undefined,
          receivedDate: new Date().toISOString().slice(0, 10),
          notes: undefined,
          status: 'done',
          parsed: {
            names: [],
            positions: [],
            contact_details: [],
            languages: [],
          },
        }])
        setStage('review')
      })
    } else {
      // Normal scan: existing createSession() call unchanged
      createSession().then(s => {
        sessionStorage.setItem('scan_session_id', s.external_id)
        setSession(s)
        setStage('uploading')
      })
    }
  }, [])
  ```

  **Important:** The `parsed` object shape must match the `ParsedCard` type defined in `frontend/src/types/index.ts`. Check that type and ensure `names`, `positions`, `contact_details`, and `languages` are all the required fields (add any missing ones as empty arrays/objects).

- [ ] **Step 3: Update the page title for manual mode**

  Around line 596–598, the title is rendered. Make it conditional:
  ```tsx
  <h1 className="text-lg font-semibold text-gray-900">
    {isManual ? t.manualEntryTitle : t.scanTitle}
  </h1>
  ```

---

### 4b: Simplified stage bar for manual mode

The `StageIndicator` component (line 826) always shows 5 steps. Pass `isManual` as a prop to show only Review → Done.

- [ ] **Step 1: Update StageIndicator props**

  Change the function signature:
  ```tsx
  function StageIndicator({ stage, isManual }: { stage: Stage; isManual?: boolean }) {
  ```

- [ ] **Step 2: Filter steps based on isManual**

  Inside StageIndicator, replace the steps array:
  ```tsx
  const allSteps: [Stage, string][] = [
    ['uploading', t.stageUpload],
    ['grouping', t.stageGroup],
    ['analyzing', t.stageAnalyze],
    ['review', t.stageReview],
    ['done', t.stageDone],
  ]
  const steps = isManual
    ? allSteps.filter(([s]) => s === 'review' || s === 'done')
    : allSteps
  ```

- [ ] **Step 3: Pass isManual to StageIndicator at the call site**

  Find the `<StageIndicator stage={stage} />` usage (line 608) and update:
  ```tsx
  <StageIndicator stage={stage} isManual={isManual} />
  ```

---

### 4c: Hide image area in manual mode

The card group card renders images or an empty-slot placeholder. In manual mode with no images, hide the whole image block (no thumbnails, no empty-slot dashed box).

- [ ] **Step 1: Add isManual to CardGroupCard props**

  `CardGroupCard` is defined at `ScanPage.tsx:948` with one call site at line 706.

  In the props interface (lines 952–973), add:
  ```tsx
  isManual?: boolean
  ```

- [ ] **Step 2: Pass isManual at the call site (line 706)**

  Add to the existing prop list at line 706:
  ```tsx
  isManual={isManual}
  ```

- [ ] **Step 3: Conditionally hide image area**

  Inside `CardGroupCard`, the image-rendering block starts at `ScanPage.tsx:1039` (`group.images.slice().sort(...).map(...)`). Wrap the entire section — from that map through the empty-slot placeholder and the "Add Photo" button — with a condition:

  ```tsx
  {!isManual && (
    <>
      {group.images
        .slice()
        .sort(...)
        .map(img => (
          // ... existing image thumbnail code unchanged ...
        ))}
      {group.images.length === 0 && (
        <div className="h-28 w-20 rounded border-2 border-dashed ...">
          <span className="text-xs text-gray-400">{t.emptySlot}</span>
        </div>
      )}
      {/* ... swap button, Add Photo button ... */}
    </>
  )}
  ```

  This hides all image UI when in manual mode, regardless of what's in `group.images`.

---

### 4d: Build and test

- [ ] **Step 1: Build**

  ```bash
  cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi/frontend"
  bun run build 2>&1 | tail -10
  ```

  Expected: `✓ built in ...ms` with no TypeScript errors.

- [ ] **Step 2: Deploy**

  ```bash
  cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi"
  ./deploy.sh
  ```

---

## Task 5: Manual end-to-end test

- [ ] **Step 1: Open the app and verify the button**

  Navigate to `/collection`. Confirm the header shows both `+ Enter Manually` (gray border) and `+ New Scan` (blue) buttons side by side.

- [ ] **Step 2: Click Enter Manually**

  Confirm you land on the scan page at `/scan?manual=1` with:
  - Title shows "Enter Person Details" (EN) or "人物情報を入力" (JA)
  - Stage bar shows only two steps: Review (active, blue) → Done
  - One card group card with no image area — just the ParsedCardEditor fields

- [ ] **Step 3: Enter data and save**

  Fill in at minimum:
  - A name in one language
  - One contact detail (phone or email)
  - Select at least one Met As chip

  Click save. Confirm:
  - Navigates to done stage ("✅ Saved 1 card")
  - Person appears in Collection → Persons tab
  - PersonDetailPage shows the entered names and contacts

- [ ] **Step 4: Verify DB record**

  ```bash
  cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi"
  sqlite3 nxt_a1.db "SELECT c.id, c.received_date, COUNT(cs.id) as sides FROM cards c LEFT JOIN card_sides cs ON cs.card_id = c.id GROUP BY c.id ORDER BY c.id DESC LIMIT 3;"
  ```

  Expected: the newest card has `sides = 0` (no CardSide records), confirming a clean image-free save.
