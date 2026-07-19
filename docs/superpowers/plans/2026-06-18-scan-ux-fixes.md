# Scan UX Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 6 UX issues in the ScanPage: IME-safe occasion input, CCW rotation, false-positive mixed-crop warning, auto-delete empty groups, drag-to-pair hint, and bottom "Start Analysis" button.

**Architecture:** All fixes are isolated to `ScanPage.tsx` (frontend) and one endpoint in `app/routers/v2/sessions.py` (backend). No new files, no schema changes, no API versioning needed.

**Tech Stack:** React 19, TypeScript, FastAPI, Pillow, Tailwind CSS. Build with `cd frontend && npm run build`. Deploy with `./deploy.sh`.

---

## Files Modified

- `frontend/src/pages/ScanPage.tsx` — All frontend fixes (Tasks 1, 3, 4, 5, 6)
- `frontend/src/api/sessions.ts` — Add `direction` param to `rotateImage` (Task 2)
- `app/routers/v2/sessions.py` — Accept `direction` query param in `rotate_image` endpoint (Task 2)

---

### Task 1: IME-safe Occasion input

**Files:**
- Modify: `frontend/src/pages/ScanPage.tsx` — `OccasionPicker` component (~line 950)

**Problem:** Pressing Enter to confirm a CJK IME candidate (e.g. selecting a kanji) fires the `addMutation.mutate()` call, saving an incomplete occasion name.

**Fix:** Track IME composition state with a `useRef`. Skip the Enter-as-save handler while composing.

- [ ] **Step 1: Locate OccasionPicker input (~line 992)**

Find the `<input>` inside the `adding ? (...)` block in `OccasionPicker`. It currently has:
```tsx
onKeyDown={e => {
  if (e.key === 'Enter' && newName.trim()) addMutation.mutate(newName.trim())
  if (e.key === 'Escape') { setAdding(false); setNewName('') }
}}
```

- [ ] **Step 2: Add composing ref and composition event handlers**

Add `const isComposing = useRef(false)` near the top of `OccasionPicker` (after `const [newName, setNewName] = useState('')`):

```tsx
const isComposing = useRef(false)
```

- [ ] **Step 3: Update the input element**

Replace the `<input>` element with:
```tsx
<input
  type="text"
  value={newName}
  onChange={e => setNewName(e.target.value)}
  onCompositionStart={() => { isComposing.current = true }}
  onCompositionEnd={() => { isComposing.current = false }}
  onKeyDown={e => {
    if (e.key === 'Enter' && newName.trim() && !isComposing.current) addMutation.mutate(newName.trim())
    if (e.key === 'Escape') { setAdding(false); setNewName('') }
  }}
  placeholder={t.occasionNewPlaceholder}
  className="flex-1 border border-gray-300 rounded px-2 py-0.5 text-xs focus:outline-none focus:ring-1 focus:ring-blue-400"
  autoFocus
/>
```

- [ ] **Step 4: Manual test**

Build: `cd frontend && npm run build`

1. Open the app, start a scan session, reach the grouping stage.
2. Open the Occasion "Add new" input.
3. Type Japanese/Chinese using an IME (e.g. type `ro` → select ロータリー via Enter).
4. Verify the candidate selection Enter does NOT save yet.
5. Verify pressing Enter again (or clicking Save) DOES save the name correctly.

---

### Task 2: Counter-clockwise rotation

**Files:**
- Modify: `app/routers/v2/sessions.py` — `rotate_image` endpoint (~line 221)
- Modify: `frontend/src/api/sessions.ts` — `rotateImage` function (~line 74)
- Modify: `frontend/src/pages/ScanPage.tsx` — `handleRotate`, ungrouped rotate button, CardGroupCard rotate button

**Backend change:** Accept optional `direction` query param (`cw` | `ccw`). Default `cw`.

- [ ] **Step 1: Update backend endpoint**

In `app/routers/v2/sessions.py`, change the `rotate_image` function signature and rotation logic:

```python
@router.post("/{sid}/images/{img_id}/rotate")
async def rotate_image(
    sid: str,
    img_id: int,
    direction: str = Query(default="cw", regex="^(cw|ccw)$"),
    db: AsyncSession = Depends(get_db),
):
    # ... existing session/image lookup code stays unchanged ...
    
    # Replace the existing rotate line:
    degrees = -90 if direction == "cw" else 90
    rotated = pil_img.rotate(degrees, expand=True)
```

Make sure `Query` is imported — add it to the FastAPI imports at the top of the file if not already present:
```python
from fastapi import APIRouter, Depends, HTTPException, Query
```

- [ ] **Step 2: Deploy backend**

```bash
cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi"
./deploy.sh
```

- [ ] **Step 3: Update API function**

In `frontend/src/api/sessions.ts`, update `rotateImage`:

```typescript
export const rotateImage = (
  sid: string,
  imgId: number,
  direction: 'cw' | 'ccw' = 'cw',
): Promise<{ id: number; image_filename: string }> =>
  post(`${BASE}/${sid}/images/${imgId}/rotate?direction=${direction}`)
```

- [ ] **Step 4: Update handleRotate in ScanPage**

In `ScanPage.tsx`, update `handleRotate` (~line 446):

```typescript
const handleRotate = async (img: SessionImage, direction: 'cw' | 'ccw' = 'cw') => {
  if (!session) return
  await rotateImage(session.external_id, img.id, direction)
  setImgCacheBust(prev => ({ ...prev, [img.id]: Date.now() }))
}
```

- [ ] **Step 5: Add CCW button in ungrouped area (~line 738)**

The ungrouped area currently has one rotate button. Replace it with two:

```tsx
<button
  className="bg-gray-100 text-xs px-1.5 py-0.5 rounded text-gray-600 hover:bg-gray-300"
  onClick={() => handleRotate(img, 'ccw')}
  title="Rotate 90° counter-clockwise"
>
  ↺
</button>
<button
  className="bg-gray-100 text-xs px-1.5 py-0.5 rounded text-gray-600 hover:bg-gray-300"
  onClick={() => handleRotate(img)}
  title="Rotate 90° clockwise"
>
  ↻
</button>
```

- [ ] **Step 6: Update CardGroupCard rotate button (~line 1164)**

`CardGroupCard` receives `onRotateImage` which calls `handleRotate(img)`. The prop type needs the direction parameter. Update the prop type and usage:

In `CardGroupCard` props interface (~line 1040):
```typescript
onRotateImage: (img: SessionImage, direction?: 'cw' | 'ccw') => void
```

Update the call site in the parent (where `onRotateImage={handleRotate}` is passed — around line 800) — no change needed since `handleRotate` already accepts `direction`.

Inside `CardGroupCard`, replace the single rotate button (~line 1164) with two buttons:

```tsx
<button
  className="bg-gray-100 text-xs px-1.5 py-0.5 rounded text-gray-600 hover:bg-gray-300"
  onClick={async () => {
    await onRotateImage(img, 'ccw')
    setLocalCacheBust(prev => ({ ...prev, [img.id]: Date.now() }))
  }}
  title="Rotate 90° counter-clockwise"
>
  ↺
</button>
<button
  className="bg-gray-100 text-xs px-1.5 py-0.5 rounded text-gray-600 hover:bg-gray-300"
  onClick={async () => {
    await onRotateImage(img)
    setLocalCacheBust(prev => ({ ...prev, [img.id]: Date.now() }))
  }}
  title="Rotate 90° clockwise"
>
  ↻
</button>
```

- [ ] **Step 7: Build and manual test**

```bash
cd frontend && npm run build
```

Open the app, upload a card image, verify ↺ rotates CCW and ↻ rotates CW. Hard-refresh (Cmd+Shift+R) if old bundle is cached.

---

### Task 3: Fix false-positive mixed-crop warning

**Files:**
- Modify: `frontend/src/pages/ScanPage.tsx` — `hasMixedCropState` computed value (~line 313)

**Problem:** `hasMixedCropState` fires when ANY un-suffixed image coexists with ANY `_cardN` image — even if the un-suffixed images are separate single-card photos unrelated to the cropped ones.

**Fix:** Only flag an un-suffixed image if there's at least one `_cardN` image sharing the same source prefix (i.e. the original multi-card photo was partially but not fully cropped).

- [ ] **Step 1: Replace hasMixedCropState logic (~line 313)**

Replace:
```typescript
const hasMixedCropState = (() => {
  if (!canAutoPairByPos) return false
  const hasCropped = ungrouped.some(i => getCardPos(i.image_filename) !== null)
  const hasUncropped = ungrouped.some(i => getCardPos(i.image_filename) === null)
  return hasCropped && hasUncropped
})()
```

With:
```typescript
const hasMixedCropState = (() => {
  if (!canAutoPairByPos) return false
  // Collect the source prefixes of all cropped (_cardN) images
  const croppedPrefixes = new Set(
    ungrouped
      .map(i => getSourcePrefix(i.image_filename))
      .filter((p): p is string => p !== null)
  )
  if (croppedPrefixes.size === 0) return false
  // An un-suffixed image is "problematically uncropped" only if its base name
  // matches a source prefix that also produced cropped siblings — meaning the
  // user cropped some cards from that photo but left the original un-cleaned.
  return ungrouped.some(i => {
    if (getCardPos(i.image_filename) !== null) return false  // is itself cropped
    const base = i.image_filename.replace(/\.[^.]+$/, '')   // strip extension
    return croppedPrefixes.has(base)
  })
})()
```

- [ ] **Step 2: Build and verify**

```bash
cd frontend && npm run build
```

Scenario to test mentally: if you have `IMG_6565.jpg` (single photo, no cropping) and `IMG_6563_card1.jpg`, `IMG_6563_card2.jpg` (cropped from multi-card photo), the warning should NOT appear for `IMG_6565.jpg`. It WOULD appear if `IMG_6563.jpg` (the uncropped original) also still exists in the ungrouped list alongside its `_card1`/`_card2` siblings.

---

### Task 4: Auto-delete empty group after drag-out

**Files:**
- Modify: `frontend/src/pages/ScanPage.tsx` — `moveImageBetweenGroups` callback (~line 475)

**Problem:** When a user drags the only image out of a card group into another group (to link front+back), the source group remains as an empty card with no images, cluttering the UI.

- [ ] **Step 1: Update moveImageBetweenGroups to auto-delete empty source group**

Replace the existing `moveImageBetweenGroups` (~line 475):

```typescript
const moveImageBetweenGroups = useCallback(
  async (imgId: number, fromGroupId: string, toGroupId: string) => {
    if (!session) return
    const fromGroup = groups.find(g => g.tempCardId === fromGroupId)
    if (!fromGroup) return
    const img = fromGroup.images.find(i => i.id === imgId)
    if (!img) return
    const toGroup = groups.find(g => g.tempCardId === toGroupId)
    if (!toGroup) return
    const newSideOrder = toGroup.images.length
    await updateImageGroup(session.external_id, imgId, toGroupId, newSideOrder)
    setGroups(prev => {
      const updated = prev.map(g => {
        if (g.tempCardId === fromGroupId) {
          const remaining = g.images.filter(i => i.id !== imgId)
          return { ...g, images: remaining }
        }
        if (g.tempCardId === toGroupId) {
          return { ...g, images: [...g.images, { ...img, temp_card_id: toGroupId, side_order: newSideOrder }] }
        }
        return g
      })
      // Auto-delete the source group if it's now empty
      return updated.filter(g => g.tempCardId !== fromGroupId || g.images.length > 0)
    })
  },
  [session, groups],
)
```

- [ ] **Step 2: Build**

```bash
cd frontend && npm run build
```

- [ ] **Step 3: Manual test**

1. Upload two card images (or have them auto-grouped as two separate single-card groups).
2. Drag the image from Card #2 and drop it onto Card #1's image area.
3. Verify Card #2 disappears (no empty group remains), and Card #1 now shows two images (front + back).

---

### Task 5: Drag-to-pair hint text

**Files:**
- Modify: `frontend/src/pages/ScanPage.tsx` — groups section header (~line 760)

**Problem:** Users don't know they can drag images between card groups to pair them as front/back. Without a hint, they'll be stuck — especially for issues like Cards #22/#23 (double-sided scan) or #24/#25 (separate photos of front/back).

- [ ] **Step 1: Add hint below the "Card groups" section header**

Find the groups section (~line 758):
```tsx
{/* Card groups */}
{groups.length > 0 && (
  <section className="space-y-3">
    <div className="flex items-center justify-between">
      <h2 className="text-sm font-medium text-gray-700">{t.cardGroupsN(groups.length)}</h2>
      {stage === 'grouping' && (
        ...
      )}
    </div>
```

Add a hint paragraph after the header div, visible only during grouping when there are multiple groups:

```tsx
{stage === 'grouping' && groups.length >= 2 && (
  <p className="text-xs text-gray-400">
    Tip: Drag an image from one card into another card to pair them as front and back.
  </p>
)}
```

Insert this immediately after the closing `</div>` of the header flex row and before the `{groups.map(...)}` call.

- [ ] **Step 2: Build**

```bash
cd frontend && npm run build
```

---

### Task 6: "Start Analysis" button at bottom of card groups list

**Files:**
- Modify: `frontend/src/pages/ScanPage.tsx` — groups section, after `groups.map()` (~line 776)

**Problem:** When there are many card groups, the user must scroll all the way to the top to click "Start Analysis."

- [ ] **Step 1: Add bottom Start Analysis button**

Find the end of the groups section. The structure is:
```tsx
{groups.map((group, gi) => (
  <CardGroupCard ... />
))}
```

After the closing `})}` of the map, before the closing `</section>`, add:

```tsx
{stage === 'grouping' && (
  <div className="flex justify-end pt-2">
    <button
      disabled={groups.every(g => g.images.length === 0)}
      onClick={startAnalysis}
      className="btn-primary text-sm"
    >
      {t.startAnalysis}
    </button>
  </div>
)}
```

- [ ] **Step 2: Build and test**

```bash
cd frontend && npm run build
```

Hard-refresh (Cmd+Shift+R). Upload multiple cards, verify there's a "Start Analysis" button both at the top (next to "Add group") and at the bottom of the card list.

---

## Final Deploy

After all tasks are complete:

```bash
cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi"
./deploy.sh
cd frontend && npm run build
```

Hard-refresh the browser (Cmd+Shift+R).
