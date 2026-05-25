# Center-Tap Corner Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the drag-rectangle interaction in `CardOutlineSelector` with a single center-tap that auto-detects the card's 4 corners using OpenCV contour detection, keeping the existing corner drag-to-adjust UI for correction.

**Architecture:** User taps the center of a card → frontend calls `POST /detect-corners` with normalized seed coordinates → backend runs OpenCV Canny + contour quad detection around the seed → returns 4 corners + confidence → frontend places draggable corner handles. If confidence is 0 (fallback rectangle), corners are shown in amber to signal the user should adjust. The drag-rectangle code path is kept intact behind an optional `onDetectCorners` prop so rollback is a one-line change in `ScanPage.tsx`.

**Tech Stack:** Python 3, OpenCV (`cv2`), NumPy, FastAPI, React 19, TypeScript

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `app/services/card_detector.py` | Modify | Add `detect_corners_from_seed()` function |
| `app/routers/v2/sessions.py` | Modify | Add `POST /{sid}/images/{img_id}/detect-corners` endpoint |
| `frontend/src/api/sessions.ts` | Modify | Add `detectCorners()` API function |
| `frontend/src/components/CardOutlineSelector.tsx` | Modify | Add tap mode; keep drag mode as fallback |
| `frontend/src/pages/ScanPage.tsx` | Modify | Pass `onDetectCorners` callback to activate tap mode |
| `tests/test_detect_corners.py` | Create | Unit tests for `detect_corners_from_seed` |

---

## Task 1: Backend service — `detect_corners_from_seed`

**Files:**
- Modify: `app/services/card_detector.py` (add after line 1040, before `manual_crop_cards`)
- Create: `tests/test_detect_corners.py`

### Step 1.1: Write the failing test

- [ ] Create `tests/test_detect_corners.py`:

```python
"""Unit tests for detect_corners_from_seed."""
import io
import numpy as np
import pytest
from PIL import Image as PILImage
from unittest.mock import patch, MagicMock


def _make_test_image_with_card() -> bytes:
    """
    Create a synthetic 600x400 white image with a dark-bordered card rectangle
    at roughly center, so contour detection can find it.
    Card region: x=150..450, y=100..300 (gray fill, black border).
    """
    img = np.ones((400, 600, 3), dtype=np.uint8) * 220  # light gray background
    img[100:300, 150:450] = 200                          # slightly lighter card area
    img[100:102, 150:450] = 50                           # top border
    img[298:300, 150:450] = 50                           # bottom border
    img[100:300, 150:152] = 50                           # left border
    img[100:300, 448:450] = 50                           # right border
    pil = PILImage.fromarray(img.astype(np.uint8))
    buf = io.BytesIO()
    pil.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def test_detect_corners_from_seed_returns_four_points():
    from app.services.card_detector import detect_corners_from_seed

    img_bytes = _make_test_image_with_card()

    with patch("app.services.card_detector.read_temp_image", return_value=img_bytes), \
         patch("app.services.card_detector._resize_bytes", return_value=img_bytes):
        corners, confidence = detect_corners_from_seed("fake/path.jpg", 0.5, 0.5)

    assert len(corners) == 4
    for pt in corners:
        assert "x" in pt and "y" in pt
        assert 0.0 <= pt["x"] <= 1.0
        assert 0.0 <= pt["y"] <= 1.0


def test_detect_corners_from_seed_high_confidence_for_clear_card():
    from app.services.card_detector import detect_corners_from_seed

    img_bytes = _make_test_image_with_card()

    with patch("app.services.card_detector.read_temp_image", return_value=img_bytes), \
         patch("app.services.card_detector._resize_bytes", return_value=img_bytes):
        corners, confidence = detect_corners_from_seed("fake/path.jpg", 0.5, 0.5)

    # A synthetic card with clear borders should produce confidence > 0
    assert confidence > 0.0


def test_detect_corners_from_seed_fallback_when_no_contour():
    from app.services.card_detector import detect_corners_from_seed

    # Uniform image — no edges, no contours
    img = PILImage.fromarray(np.full((400, 600, 3), 200, dtype=np.uint8))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    img_bytes = buf.getvalue()

    with patch("app.services.card_detector.read_temp_image", return_value=img_bytes), \
         patch("app.services.card_detector._resize_bytes", return_value=img_bytes):
        corners, confidence = detect_corners_from_seed("fake/path.jpg", 0.5, 0.5)

    assert len(corners) == 4
    assert confidence == 0.0   # fallback rectangle


def test_detect_corners_seed_outside_image_returns_fallback():
    """Seed clamped to image bounds; should not raise."""
    from app.services.card_detector import detect_corners_from_seed

    img = PILImage.fromarray(np.full((400, 600, 3), 200, dtype=np.uint8))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    img_bytes = buf.getvalue()

    with patch("app.services.card_detector.read_temp_image", return_value=img_bytes), \
         patch("app.services.card_detector._resize_bytes", return_value=img_bytes):
        corners, confidence = detect_corners_from_seed("fake/path.jpg", 1.5, -0.5)

    assert len(corners) == 4
```

### Step 1.2: Run the tests to confirm they fail

- [ ] Run:

```bash
cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi"
python -m pytest tests/test_detect_corners.py -v 2>&1 | tail -20
```

Expected: FAIL with `ImportError` or `AttributeError` (function does not exist yet).

### Step 1.3: Implement `detect_corners_from_seed` in `card_detector.py`

- [ ] Add the following function to `app/services/card_detector.py`, immediately before the `async def detect_and_split` function (around line 849):

```python
def detect_corners_from_seed(
    temp_relative_path: str,
    seed_x: float,
    seed_y: float,
) -> tuple[list[dict], float]:
    """
    Find the 4 corners of the business card nearest the seed point.

    Uses OpenCV Canny edge detection + contour quadrilateral approximation.
    If no quad-shaped contour contains the seed, returns a default rectangle
    centered on the seed with confidence 0.0.

    Args:
        temp_relative_path: Relative path under TEMP_DIR (passed to read_temp_image).
        seed_x: Normalized [0, 1] x coordinate of the tap point.
        seed_y: Normalized [0, 1] y coordinate of the tap point.

    Returns:
        (corners, confidence)
        corners — list of 4 dicts {"x": float, "y": float} in TL/TR/BR/BL order,
                  coordinates normalized to [0, 1].
        confidence — 0.0 (fallback rectangle) to 1.0 (high-confidence quad).
    """
    raw = read_temp_image(temp_relative_path)
    working = _resize_bytes(raw)
    img_pil = Image.open(io.BytesIO(working)).convert("RGB")
    img_w, img_h = img_pil.size

    # Clamp seed to valid pixel range
    seed_px = int(max(0, min(img_w - 1, seed_x * img_w)))
    seed_py = int(max(0, min(img_h - 1, seed_y * img_h)))

    img_bgr = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 30, 100)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # Cards must be at least 1% of image area to filter out noise
    MIN_AREA = img_w * img_h * 0.01

    best_quad: np.ndarray | None = None
    best_area = 0.0

    for contour in contours:
        area = cv2.contourArea(contour)
        if area < MIN_AREA:
            continue
        # Only consider contours that actually contain the seed point
        if cv2.pointPolygonTest(contour, (float(seed_px), float(seed_py)), False) < 0:
            continue
        # Try successively looser epsilon until we get a 4-point approximation
        arc = cv2.arcLength(contour, True)
        for eps_factor in (0.02, 0.04, 0.06, 0.08):
            approx = cv2.approxPolyDP(contour, eps_factor * arc, True)
            if len(approx) == 4:
                if area > best_area:
                    best_area = area
                    best_quad = approx.reshape(4, 2).astype(np.float32)
                break

    if best_quad is not None:
        sorted_pts = _sort_quad_points(best_quad)
        corners = [
            {"x": float(p[0] / img_w), "y": float(p[1] / img_h)}
            for p in sorted_pts
        ]
        # Scale confidence: 20% of image area → 1.0
        confidence = float(min(1.0, best_area / (img_w * img_h) * 5))
        logger.info(
            "detect_corners_from_seed: seed=(%.3f,%.3f) → quad area=%.0f confidence=%.2f",
            seed_x, seed_y, best_area, confidence,
        )
        return corners, confidence

    # Fallback: axis-aligned rectangle centered on the seed,
    # assuming standard business card aspect ratio ~1.75:1.
    card_w = 0.35
    card_h = card_w / 1.75
    x1 = max(0.0, seed_x - card_w / 2)
    y1 = max(0.0, seed_y - card_h / 2)
    x2 = min(1.0, seed_x + card_w / 2)
    y2 = min(1.0, seed_y + card_h / 2)
    fallback_corners = [
        {"x": x1, "y": y1},
        {"x": x2, "y": y1},
        {"x": x2, "y": y2},
        {"x": x1, "y": y2},
    ]
    logger.info(
        "detect_corners_from_seed: no quad found for seed=(%.3f,%.3f) → fallback rectangle",
        seed_x, seed_y,
    )
    return fallback_corners, 0.0
```

### Step 1.4: Run the tests to confirm they pass

- [ ] Run:

```bash
cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi"
python -m pytest tests/test_detect_corners.py -v 2>&1 | tail -20
```

Expected: 4 PASSED.

### Step 1.5: Commit

- [ ] Stage and commit:

```bash
git add "app/services/card_detector.py" "tests/test_detect_corners.py"
git commit -m "feat: add detect_corners_from_seed() with OpenCV contour quad detection"
```

---

## Task 2: Backend router — `/detect-corners` endpoint

**Files:**
- Modify: `app/routers/v2/sessions.py` (add after the `count-cards` endpoint, around line 328)

### Step 2.1: Write the failing test (signature check)

- [ ] Add to `tests/test_detect_corners.py`:

```python
def test_detect_corners_endpoint_exists():
    """Confirm the detect_corners route is registered."""
    import inspect
    from app.routers.v2 import sessions as s
    # The router should have a route matching the path pattern
    paths = [r.path for r in s.router.routes]
    assert any("detect-corners" in p for p in paths), \
        f"No detect-corners route found. Routes: {paths}"
```

- [ ] Run:

```bash
python -m pytest tests/test_detect_corners.py::test_detect_corners_endpoint_exists -v 2>&1 | tail -10
```

Expected: FAIL.

### Step 2.2: Add the endpoint to `sessions.py`

- [ ] In `app/routers/v2/sessions.py`, add this block immediately after the `count_cards` endpoint (after the closing of the `count_cards` function, around line 328):

```python
# ---------------------------------------------------------------------------
# 3c. Detect card corners from a seed tap point
# ---------------------------------------------------------------------------

class DetectCornersRequest(BaseModel):
    x: float  # normalized [0, 1] tap x coordinate
    y: float  # normalized [0, 1] tap y coordinate


class DetectCornersResponse(BaseModel):
    corners: list[_Point]  # 4 points: TL, TR, BR, BL in normalized coords
    confidence: float      # 0.0 = fallback rectangle, 1.0 = high-confidence quad


@router.post("/{sid}/images/{img_id}/detect-corners", response_model=DetectCornersResponse)
async def detect_corners(
    sid: str,
    img_id: int,
    body: DetectCornersRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Given a seed tap point on the image, detect the 4 corners of the business
    card nearest that point using OpenCV contour detection.

    Returns 4 normalized corner coordinates (TL/TR/BR/BL) and a confidence
    score (0.0 = fell back to default rectangle, 1.0 = clear quad found).
    """
    session = await _get_session(db, sid)
    img = await db.scalar(
        select(ScanSessionImage).where(
            ScanSessionImage.id == img_id,
            ScanSessionImage.session_id == session.id,
        )
    )
    if not img:
        raise HTTPException(404, "Image not found in this session")

    corners, confidence = card_detector.detect_corners_from_seed(
        img.image_path, body.x, body.y
    )
    return DetectCornersResponse(
        corners=[_Point(x=c["x"], y=c["y"]) for c in corners],
        confidence=confidence,
    )
```

### Step 2.3: Run the test to confirm it passes

- [ ] Run:

```bash
python -m pytest tests/test_detect_corners.py::test_detect_corners_endpoint_exists -v 2>&1 | tail -10
```

Expected: PASS.

### Step 2.4: Run all detect-corners tests

- [ ] Run:

```bash
python -m pytest tests/test_detect_corners.py -v 2>&1 | tail -20
```

Expected: all PASSED.

### Step 2.5: Commit

- [ ] Stage and commit:

```bash
git add "app/routers/v2/sessions.py" "tests/test_detect_corners.py"
git commit -m "feat: add POST /detect-corners endpoint returning quad corners + confidence"
```

---

## Task 3: Frontend API client — `detectCorners()`

**Files:**
- Modify: `frontend/src/api/sessions.ts`

### Step 3.1: Add `detectCorners` to `sessions.ts`

- [ ] In `frontend/src/api/sessions.ts`, add after the `manualSplitImage` export (after line 55):

```typescript
export interface DetectCornersResult {
  corners: Point[]   // 4 points: TL, TR, BR, BL, normalized [0,1]
  confidence: number // 0.0 = fallback rectangle, 1.0 = high-confidence quad
}

export const detectCorners = (
  sid: string,
  imgId: number,
  seed: Point,
): Promise<DetectCornersResult> =>
  post(`${BASE}/${sid}/images/${imgId}/detect-corners`, { x: seed.x, y: seed.y })
```

### Step 3.2: Verify TypeScript compiles

- [ ] Run:

```bash
cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi/frontend"
bun run tsc --noEmit 2>&1 | tail -20
```

Expected: no errors.

### Step 3.3: Commit

- [ ] Stage and commit:

```bash
git add "frontend/src/api/sessions.ts"
git commit -m "feat: add detectCorners() API client function"
```

---

## Task 4: Frontend UI — tap mode in `CardOutlineSelector`

**Files:**
- Modify: `frontend/src/components/CardOutlineSelector.tsx`

The tap mode is activated only when the `onDetectCorners` prop is provided. This keeps the drag-rectangle code path intact for rollback.

### Step 4.1: Update the component

- [ ] Replace the entire contents of `frontend/src/components/CardOutlineSelector.tsx` with:

```tsx
/**
 * CardOutlineSelector
 *
 * Two interaction modes depending on the `onDetectCorners` prop:
 *
 * TAP MODE (onDetectCorners provided):
 *   User taps the center of a card → backend detects 4 corners via CV.
 *   If confidence is 0 (fallback rectangle), corners are shown in amber.
 *   All 4 corners remain drag-adjustable before confirming.
 *
 * DRAG MODE (no onDetectCorners):
 *   Original drag-to-draw rectangle interaction.
 *
 * Normalized [0,1] coordinates are returned to the parent in both modes.
 */
import React, { useRef, useState, useCallback, useEffect } from 'react'
import type { Point } from '../api/sessions'

interface Props {
  imageUrl: string
  cardCount: number
  onComplete: (polygons: Point[][]) => void
  onCancel: () => void
  /** If provided, activates tap mode. Called with the tap seed; resolves corners + confidence. */
  onDetectCorners?: (seed: Point) => Promise<{ corners: Point[]; confidence: number }>
}

const COLORS = ['#ef4444', '#f97316', '#22c55e', '#3b82f6', '#a855f7', '#ec4899']
const FALLBACK_COLOR = '#f59e0b' // amber — signals user should adjust corners

type Polygon = [Point, Point, Point, Point]
interface DragHandle { polyIdx: number; cornerIdx: number }

// Per-polygon metadata used only in tap mode
interface PolyMeta { confidence: number }

export default function CardOutlineSelector({ imageUrl, cardCount, onComplete, onCancel, onDetectCorners }: Props) {
  const imgRef = useRef<HTMLImageElement>(null)
  const [imgRect, setImgRect] = useState<DOMRect | null>(null)

  const [polygons, setPolygons] = useState<Polygon[]>([])
  const [polyMeta, setPolyMeta] = useState<PolyMeta[]>([])

  // Drag-mode state (only used when onDetectCorners is not provided)
  const [dragStart, setDragStart] = useState<Point | null>(null)
  const [dragEnd, setDragEnd] = useState<Point | null>(null)

  // Tap-mode state
  const [detecting, setDetecting] = useState(false)
  const [detectError, setDetectError] = useState<string | null>(null)

  const [dragHandle, setDragHandle] = useState<DragHandle | null>(null)

  const canCrop = polygons.length >= 1
  const tapMode = !!onDetectCorners

  const getFreshRect = useCallback((): DOMRect | null => {
    if (!imgRef.current) return null
    return imgRef.current.getBoundingClientRect()
  }, [])

  useEffect(() => {
    const rect = getFreshRect()
    if (rect) setImgRect(rect)
    const onResize = () => { const r = getFreshRect(); if (r) setImgRect(r) }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [getFreshRect])

  useEffect(() => {
    requestAnimationFrame(() => {
      const r = getFreshRect()
      if (r) setImgRect(r)
    })
  }, [canCrop, getFreshRect])

  const toNorm = useCallback((clientX: number, clientY: number): Point | null => {
    const rect = getFreshRect()
    if (!rect) return null
    return {
      x: Math.max(0, Math.min(1, (clientX - rect.left) / rect.width)),
      y: Math.max(0, Math.min(1, (clientY - rect.top) / rect.height)),
    }
  }, [getFreshRect])

  const toSvg = (pt: Point) => ({
    x: pt.x * (imgRect?.width ?? 0),
    y: pt.y * (imgRect?.height ?? 0),
  })

  const rectFromPoints = (a: Point, b: Point): Polygon => [
    { x: Math.min(a.x, b.x), y: Math.min(a.y, b.y) },
    { x: Math.max(a.x, b.x), y: Math.min(a.y, b.y) },
    { x: Math.max(a.x, b.x), y: Math.max(a.y, b.y) },
    { x: Math.min(a.x, b.x), y: Math.max(a.y, b.y) },
  ]

  const getClientPos = (e: React.MouseEvent | React.TouchEvent) => {
    if ('touches' in e) {
      const t = e.touches[0] ?? e.changedTouches[0]
      return { clientX: t.clientX, clientY: t.clientY }
    }
    return { clientX: (e as React.MouseEvent).clientX, clientY: (e as React.MouseEvent).clientY }
  }

  // ── TAP MODE handlers ─────────────────────────────────────────────────────

  const handleTap = async (e: React.MouseEvent | React.TouchEvent) => {
    if (dragHandle || detecting) return
    e.preventDefault()
    const { clientX, clientY } = getClientPos(e)
    const seed = toNorm(clientX, clientY)
    if (!seed || !onDetectCorners) return

    setDetecting(true)
    setDetectError(null)
    try {
      const { corners, confidence } = await onDetectCorners(seed)
      if (corners.length !== 4) throw new Error('Unexpected corner count from server')
      setPolygons(prev => [...prev, corners as Polygon])
      setPolyMeta(prev => [...prev, { confidence }])
    } catch (err) {
      setDetectError('Corner detection failed — try again or switch to manual mode')
    } finally {
      setDetecting(false)
    }
  }

  // ── DRAG MODE handlers ────────────────────────────────────────────────────

  const handlePointerDown = (e: React.MouseEvent | React.TouchEvent) => {
    if (dragHandle) return
    e.preventDefault()
    const { clientX, clientY } = getClientPos(e)
    const pt = toNorm(clientX, clientY)
    if (!pt) return
    setDragStart(pt)
    setDragEnd(pt)
  }

  const handlePointerMove = (e: React.MouseEvent | React.TouchEvent) => {
    e.preventDefault()
    const { clientX, clientY } = getClientPos(e)
    const pt = toNorm(clientX, clientY)
    if (!pt) return

    if (dragHandle !== null) {
      setPolygons(prev => prev.map((poly, i) => {
        if (i !== dragHandle.polyIdx) return poly
        const updated = [...poly] as Polygon
        updated[dragHandle.cornerIdx] = pt
        return updated
      }))
    } else if (dragStart) {
      setDragEnd(pt)
    }
  }

  const handlePointerUp = (e: React.MouseEvent | React.TouchEvent) => {
    e.preventDefault()
    if (dragHandle) {
      setDragHandle(null)
      return
    }
    if (!dragStart || !dragEnd) return
    const dx = Math.abs(dragEnd.x - dragStart.x)
    const dy = Math.abs(dragEnd.y - dragStart.y)
    if (dx > 0.01 && dy > 0.01) {
      setPolygons(prev => [...prev, rectFromPoints(dragStart, dragEnd)])
      setPolyMeta(prev => [...prev, { confidence: 1.0 }])
    }
    setDragStart(null)
    setDragEnd(null)
  }

  const undoLast = () => {
    setPolygons(prev => prev.slice(0, -1))
    setPolyMeta(prev => prev.slice(0, -1))
    setDragStart(null)
    setDragEnd(null)
  }

  // ── SVG rendering ─────────────────────────────────────────────────────────

  const polyToSvgPoints = (poly: Polygon) =>
    poly.map(p => `${toSvg(p).x},${toSvg(p).y}`).join(' ')

  const renderPolygon = (poly: Polygon, polyIdx: number) => {
    const meta = polyMeta[polyIdx]
    const isFallback = meta?.confidence === 0
    const color = isFallback
      ? FALLBACK_COLOR
      : COLORS[polyIdx % COLORS.length]
    const svgPts = poly.map(toSvg)

    return (
      <g key={polyIdx}>
        <polygon
          points={polyToSvgPoints(poly)}
          fill={`${color}33`}
          stroke={color}
          strokeWidth={2}
          strokeDasharray={isFallback ? '6 3' : undefined}
        />
        <text
          x={svgPts[0].x + 6} y={svgPts[0].y - 6}
          fontSize={11} fill={color} fontWeight="bold"
          stroke="white" strokeWidth={3} paintOrder="stroke"
        >
          {`Card ${polyIdx + 1}${isFallback ? ' ⚠' : ''}`}
        </text>
        {svgPts.map((p, ci) => (
          <circle
            key={ci}
            cx={p.x} cy={p.y} r={12}
            fill={color} stroke="white" strokeWidth={2}
            style={{ cursor: 'grab', pointerEvents: 'all' }}
            onMouseDown={e => { e.stopPropagation(); setDragHandle({ polyIdx, cornerIdx: ci }) }}
            onTouchStart={e => { e.stopPropagation(); setDragHandle({ polyIdx, cornerIdx: ci }) }}
          />
        ))}
      </g>
    )
  }

  const renderPreviewRect = () => {
    if (!dragStart || !dragEnd) return null
    const poly = rectFromPoints(dragStart, dragEnd)
    const color = COLORS[polygons.length % COLORS.length]
    return (
      <polygon
        points={polyToSvgPoints(poly)}
        fill={`${color}22`}
        stroke={color}
        strokeWidth={2}
        strokeDasharray="6 3"
        style={{ pointerEvents: 'none' }}
      />
    )
  }

  // ── Instructions ──────────────────────────────────────────────────────────

  const headerText = detecting
    ? `Detecting corners…`
    : tapMode
      ? polygons.length === 0
        ? `Tap the center of each card (${cardCount} detected)`
        : `${polygons.length} card${polygons.length > 1 ? 's' : ''} tapped — tap more or Crop`
      : dragStart
        ? `Card ${polygons.length + 1} — drag to define boundary`
        : polygons.length === 0
          ? `Drag around each card (${cardCount} detected)`
          : `${polygons.length} card${polygons.length > 1 ? 's' : ''} outlined — draw more or tap Crop`

  // Pointer event props differ by mode
  const imageAreaProps = tapMode
    ? {
        onClick: handleTap,
        onTouchEnd: handleTap,
        onMouseMove: (e: React.MouseEvent) => {
          if (dragHandle) handlePointerMove(e)
        },
        onTouchMove: (e: React.TouchEvent) => {
          if (dragHandle) handlePointerMove(e)
        },
        onMouseUp: (e: React.MouseEvent) => {
          if (dragHandle) handlePointerUp(e)
        },
        onTouchEndCapture: undefined as undefined,
      }
    : {
        onMouseDown: handlePointerDown,
        onMouseMove: handlePointerMove,
        onMouseUp: handlePointerUp,
        onTouchStart: handlePointerDown,
        onTouchMove: handlePointerMove,
        onTouchEnd: handlePointerUp,
      }

  return (
    <div className="fixed inset-0 z-50 bg-black flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 bg-gray-900 text-white shrink-0">
        <button onClick={onCancel} className="text-sm text-gray-300 hover:text-white">Cancel</button>
        <div className="text-sm font-semibold text-center flex-1">{headerText}</div>
        <button
          onClick={undoLast}
          className="text-sm text-gray-300 hover:text-white disabled:opacity-30"
          disabled={polygons.length === 0 || detecting}
        >
          Undo
        </button>
      </div>

      {/* Image + overlay */}
      <div
        className="flex-1 overflow-hidden relative flex items-center justify-center bg-black select-none"
        style={{ touchAction: 'none', cursor: tapMode ? (detecting ? 'wait' : 'crosshair') : 'default' }}
        {...imageAreaProps}
      >
        <img
          ref={imgRef}
          src={imageUrl}
          alt={tapMode ? 'Tap the center of each card' : 'Drag to select card boundaries'}
          className="max-w-full max-h-full object-contain"
          onLoad={() => { const r = getFreshRect(); if (r) setImgRect(r) }}
          draggable={false}
        />
        {imgRect && (
          <svg
            className="fixed pointer-events-none"
            style={{ left: imgRect.left, top: imgRect.top, width: imgRect.width, height: imgRect.height }}
            viewBox={`0 0 ${imgRect.width} ${imgRect.height}`}
          >
            {polygons.map((poly, i) => renderPolygon(poly, i))}
            {!tapMode && renderPreviewRect()}
          </svg>
        )}
        {detecting && (
          <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
            <div className="bg-black/60 text-white text-sm px-4 py-2 rounded-full">
              Detecting corners…
            </div>
          </div>
        )}
      </div>

      {/* Error toast */}
      {detectError && (
        <div className="bg-red-900 text-red-100 text-sm px-4 py-2 text-center shrink-0">
          {detectError}
        </div>
      )}

      {/* Footer */}
      <div className="bg-gray-900 px-4 py-3 shrink-0">
        {canCrop ? (
          <button
            className="w-full bg-green-600 hover:bg-green-500 text-white font-semibold py-3 rounded-lg"
            onClick={() => onComplete(polygons)}
          >
            Crop {polygons.length} card{polygons.length > 1 ? 's' : ''}
          </button>
        ) : (
          <p className="text-center text-gray-400 text-sm">
            {tapMode
              ? 'Tap the center of each business card'
              : 'Press and drag to draw a box around each card'}
          </p>
        )}
      </div>
    </div>
  )
}
```

### Step 4.2: Verify TypeScript compiles

- [ ] Run:

```bash
cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi/frontend"
bun run tsc --noEmit 2>&1 | tail -20
```

Expected: no errors.

### Step 4.3: Commit

- [ ] Stage and commit:

```bash
git add "frontend/src/components/CardOutlineSelector.tsx"
git commit -m "feat: add tap mode to CardOutlineSelector; drag mode kept as fallback"
```

---

## Task 5: Wire up `ScanPage.tsx` to use tap mode

**Files:**
- Modify: `frontend/src/pages/ScanPage.tsx`

### Step 5.1: Import `detectCorners` and wire up the callback

- [ ] In `frontend/src/pages/ScanPage.tsx`, find the import block at the top that includes `countCards` and `manualSplitImage` (around line 17). Add `detectCorners` to that import:

```typescript
// Before (example current line):
import { countCards, manualSplitImage, ... } from '../api/sessions'

// After — add detectCorners to the same import:
import { countCards, detectCorners, manualSplitImage, ... } from '../api/sessions'
```

- [ ] Find the `<CardOutlineSelector` JSX block (around line 613). It currently looks like:

```tsx
<CardOutlineSelector
  imageUrl={`/api/v2/sessions/${session.external_id}/temp/${outlineTarget.img.image_filename}${imgCacheBust[outlineTarget.img.id] ? `?t=${imgCacheBust[outlineTarget.img.id]}` : ''}`}
  cardCount={outlineTarget.count}
  onComplete={handleOutlineComplete}
  onCancel={() => setOutlineTarget(null)}
/>
```

Replace it with:

```tsx
<CardOutlineSelector
  imageUrl={`/api/v2/sessions/${session.external_id}/temp/${outlineTarget.img.image_filename}${imgCacheBust[outlineTarget.img.id] ? `?t=${imgCacheBust[outlineTarget.img.id]}` : ''}`}
  cardCount={outlineTarget.count}
  onComplete={handleOutlineComplete}
  onCancel={() => setOutlineTarget(null)}
  onDetectCorners={(seed) =>
    detectCorners(session.external_id, outlineTarget.img.id, seed)
  }
/>
```

### Step 5.2: Verify TypeScript compiles

- [ ] Run:

```bash
cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi/frontend"
bun run tsc --noEmit 2>&1 | tail -20
```

Expected: no errors.

### Step 5.3: Commit

- [ ] Stage and commit:

```bash
git add "frontend/src/pages/ScanPage.tsx"
git commit -m "feat: wire center-tap corner detection into ScanPage via onDetectCorners prop"
```

---

## Task 6: Deploy and smoke test

### Step 6.1: Deploy backend

- [ ] Run:

```bash
cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi"
./deploy.sh
```

Wait for the script to complete and confirm the service is back up.

### Step 6.2: Build frontend

- [ ] Run:

```bash
cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi/frontend"
bun run build 2>&1 | tail -20
```

Expected: build succeeds with no errors.

### Step 6.3: Smoke test checklist

Manual test with a real photo containing 1-2 business cards:

- [ ] Upload a photo with a single card on a contrasting background → tap center → verify 4 corners snap to card edges, confidence > 0 (red corners, not amber).
- [ ] Tap a card with low contrast (e.g. white card on white table) → verify amber ⚠ corners appear → drag corners to correct → Crop works.
- [ ] Upload a photo with 2 cards → tap each center → verify 2 polygons placed → Crop splits correctly.
- [ ] Tap outside any card (background area) → verify either no polygon placed (contour misses seed) or fallback amber rectangle appears.
- [ ] Tap → Undo → verify polygon removed and counter resets.
- [ ] Tap → adjust a corner handle → Crop → verify warped card looks correct.

### Step 6.4: Decision gate

After smoke testing, decide whether to keep the feature or roll back.

**Keep:** The tap flow is faster and accurate on ≥70% of test cards.

**Roll back:** Remove the `onDetectCorners` prop from the `<CardOutlineSelector>` call in `ScanPage.tsx`. That single prop removal restores drag mode with no other changes needed.

---

## Task 7: Push branch and open GitHub Pull Request

### Step 7.1: Create a feature branch from current main

- [ ] Run:

```bash
cd "/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi"
git checkout -b feat/center-tap-corner-detection
```

### Step 7.2: Verify all commits are on the branch

- [ ] Run:

```bash
git log main..feat/center-tap-corner-detection --oneline
```

Expected: the 5 feature commits from Tasks 1–5 are listed.

### Step 7.3: Push branch to GitHub

- [ ] Run:

```bash
git push -u origin feat/center-tap-corner-detection
```

### Step 7.4: Open the Pull Request

- [ ] Run:

```bash
gh pr create \
  --title "feat: center-tap corner detection for business card scanning" \
  --body "$(cat <<'EOF'
## Summary

- Adds `detect_corners_from_seed()` to `card_detector.py`: OpenCV Canny + contour quadrilateral detection anchored on a user-provided seed point
- Adds `POST /api/v2/sessions/{sid}/images/{img_id}/detect-corners` endpoint
- Updates `CardOutlineSelector` with tap mode: user taps the card center → backend returns 4 corners + confidence; all corners remain drag-adjustable
- Amber ⚠ corners signal low-confidence (fallback rectangle); user corrects by dragging
- Activates tap mode in `ScanPage.tsx` via a single `onDetectCorners` prop

## Change request

Approved 2026-05-17. Replaces 2-corner drag interaction. Rollback: remove `onDetectCorners` prop from `ScanPage.tsx` — no backend change or migration needed.

## Test plan

- [ ] Upload photo with clear-bordered card on contrasting background → tap center → verify red corners snap to card edges
- [ ] Upload white card on white background → verify amber ⚠ corners appear → drag to correct → Crop succeeds
- [ ] Upload photo with 2 cards → tap each → verify 2 polygons → Crop splits correctly
- [ ] Tap background (no card) → verify graceful fallback (amber rectangle or no polygon)
- [ ] Tap → Undo → verify polygon removed
- [ ] All backend unit tests pass: `pytest tests/test_detect_corners.py -v`
- [ ] TypeScript compiles: `bun run tsc --noEmit`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] Copy the PR URL from the output and share it for review.

### Step 7.5: After smoke test passes — merge PR

Once the smoke test checklist in Task 6 is complete and the feature is accepted:

- [ ] Merge the PR on GitHub (squash or merge commit — your preference).
- [ ] Delete the remote branch after merge.
- [ ] Pull main locally:

```bash
git checkout main && git pull origin main
```

---

## Rollback Reference (one-line change)

To revert to drag mode without removing any code:

In `frontend/src/pages/ScanPage.tsx`, delete the `onDetectCorners` prop line:

```tsx
// Remove this line only:
onDetectCorners={(seed) =>
  detectCorners(session.external_id, outlineTarget.img.id, seed)
}
```

Then rebuild frontend and redeploy backend (no backend change needed). The `detect-corners` endpoint can be removed at leisure in a follow-up cleanup commit.
