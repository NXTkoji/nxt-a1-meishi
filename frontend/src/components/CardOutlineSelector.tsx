/**
 * CardOutlineSelector
 *
 * Two interaction modes depending on the `onDetectCorners` prop:
 *
 * TAP MODE (onDetectCorners provided):
 *   User taps the center of a card → backend detects 4 corners via CV.
 *   If confidence is 0 (fallback rectangle), corners are shown in amber.
 *   All 4 corners remain drag-adjustable before confirming.
 *   "Pick background" lets the user click the table/mat once; that point is
 *   sent with every detection (and amber guesses are re-detected with it),
 *   which fixes photos whose edges aren't the table — e.g. cards on an
 *   envelope lying on a floor.
 *
 * DRAG MODE (no onDetectCorners):
 *   Original drag-to-draw rectangle interaction.
 *
 * Normalized [0,1] coordinates are returned to the parent in both modes.
 */
import React, { useRef, useState, useCallback, useEffect } from 'react'
import type { Point } from '../api/sessions'
import { useLang } from '../LangContext'

interface Props {
  imageUrl: string
  cardCount: number
  onComplete: (polygons: Point[][]) => void
  onCancel: () => void
  /** If provided, activates tap mode. Called with the tap seed, already-defined polygons and the picked background point; resolves corners + confidence. */
  onDetectCorners?: (seed: Point, existing: Point[][], bgPoint: Point | null) => Promise<{ corners: Point[]; confidence: number }>
}

const COLORS = ['#ef4444', '#f97316', '#22c55e', '#3b82f6', '#a855f7', '#ec4899']
const FALLBACK_COLOR = '#f59e0b' // amber — signals user should adjust corners

type Polygon = [Point, Point, Point, Point]
// offset = corner minus pointer at grab time (normalized), so the corner keeps
// its position under the cursor instead of snapping to the pointer
interface DragHandle { polyIdx: number; cornerIdx: number; offset: Point }
// seed = the tap that produced the outline (tap mode), kept so it can be re-detected
interface PolyMeta { confidence: number; seed?: Point }

export default function CardOutlineSelector({ imageUrl, cardCount, onComplete, onCancel, onDetectCorners }: Props) {
  const { t } = useLang()
  const imgRef = useRef<HTMLImageElement>(null)
  const [imgRect, setImgRect] = useState<DOMRect | null>(null)

  const [polygons, setPolygons] = useState<Polygon[]>([])
  const [polyMeta, setPolyMeta] = useState<PolyMeta[]>([])

  const [dragStart, setDragStart] = useState<Point | null>(null)
  const [dragEnd, setDragEnd] = useState<Point | null>(null)

  const [detecting, setDetecting] = useState(false)
  const [detectError, setDetectError] = useState<string | null>(null)

  // Background (table/mat) point picked by the user; pickingBg = next click sets it
  const [bgPoint, setBgPoint] = useState<Point | null>(null)
  const [pickingBg, setPickingBg] = useState(false)

  const [dragHandle, setDragHandle] = useState<DragHandle | null>(null)
  // remains true from corner mousedown/touchstart until AFTER the spurious click fires
  const cornerDragRef = useRef(false)

  const canCrop = polygons.length >= 1
  const tapMode = !!onDetectCorners

  const getFreshRect = useCallback((): DOMRect | null => {
    if (!imgRef.current) return null
    return imgRef.current.getBoundingClientRect()
  }, [])

  // Keep the overlay pinned to the image. The image moves whenever the bars
  // around it change height — the amber hint line appearing, the error toast,
  // the crop button — not only on window resize. A stale rect drew every
  // outline shifted off its card and made dragged corners jump. Observing the
  // image's container catches all of those layout changes.
  useEffect(() => {
    const measure = () => { const r = getFreshRect(); if (r) setImgRect(r) }
    measure()
    const observer = new ResizeObserver(measure)
    const img = imgRef.current
    if (img) {
      observer.observe(img)
      if (img.parentElement) observer.observe(img.parentElement)
    }
    window.addEventListener('resize', measure)
    return () => {
      observer.disconnect()
      window.removeEventListener('resize', measure)
    }
  }, [getFreshRect])

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

  // Ray-casting point-in-polygon test (normalized coords).
  const pointInPolygon = (pt: Point, poly: Polygon): boolean => {
    let inside = false
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const xi = poly[i].x, yi = poly[i].y
      const xj = poly[j].x, yj = poly[j].y
      const intersect =
        (yi > pt.y) !== (yj > pt.y) &&
        pt.x < ((xj - xi) * (pt.y - yi)) / (yj - yi) + xi
      if (intersect) inside = !inside
    }
    return inside
  }

  const centroid = (poly: Polygon): Point => ({
    x: poly.reduce((s, p) => s + p.x, 0) / poly.length,
    y: poly.reduce((s, p) => s + p.y, 0) / poly.length,
  })

  // True if a point falls inside any already-defined card outline.
  const insideExistingCard = (pt: Point): boolean =>
    polygons.some(poly => pointInPolygon(pt, poly))

  const getClientPos = (e: React.MouseEvent | React.TouchEvent) => {
    if ('touches' in e) {
      const t = e.touches[0] ?? e.changedTouches[0]
      return { clientX: t.clientX, clientY: t.clientY }
    }
    return { clientX: (e as React.MouseEvent).clientX, clientY: (e as React.MouseEvent).clientY }
  }

  // ── TAP MODE handlers ─────────────────────────────────────────────────────

  const handleTap = async (e: React.MouseEvent | React.TouchEvent) => {
    if (dragHandle || detecting || cornerDragRef.current) return
    e.preventDefault()
    const { clientX, clientY } = getClientPos(e)
    const seed = toNorm(clientX, clientY)
    if (!seed || !onDetectCorners) return

    if (pickingBg) {
      await pickBackground(seed)
      return
    }

    // Block-outright: a tap inside an already-defined card must not spawn a new candidate.
    if (insideExistingCard(seed)) {
      setDetectError(t.outlineAlreadyOutlined)
      return
    }

    setDetecting(true)
    setDetectError(null)
    try {
      const { corners, confidence } = await onDetectCorners(seed, polygons, bgPoint)
      if (corners.length !== 4) throw new Error('Unexpected corner count from server')
      setPolygons(prev => [...prev, corners as Polygon])
      setPolyMeta(prev => [...prev, { confidence, seed }])
    } catch {
      setDetectError(t.outlineDetectFailed)
    } finally {
      setDetecting(false)
    }
  }

  // Set the background point, then re-detect every amber (guessed) outline with
  // it — those are exactly the cards the background-less detection missed.
  const pickBackground = async (pt: Point) => {
    if (insideExistingCard(pt)) {
      setDetectError(t.outlineBgOnCard)
      return
    }
    setBgPoint(pt)
    setPickingBg(false)
    setDetectError(null)
    if (!onDetectCorners) return

    const guesses = polyMeta
      .map((m, i) => ({ i, seed: m.seed }))
      .filter(g => polyMeta[g.i].confidence === 0 && g.seed)
    if (guesses.length === 0) return

    setDetecting(true)
    // Sequential: each improved outline is masked out for the next one
    const nextPolys = [...polygons]
    const nextMeta = [...polyMeta]
    try {
      for (const { i, seed } of guesses) {
        const others = nextPolys.filter((_, j) => j !== i)
        const { corners, confidence } = await onDetectCorners(seed!, others, pt)
        if (corners.length === 4 && confidence > 0) {
          nextPolys[i] = corners as Polygon
          nextMeta[i] = { confidence, seed }
        }
      }
    } catch {
      setDetectError(t.outlineDetectFailed)
    } finally {
      setPolygons(nextPolys)
      setPolyMeta(nextMeta)
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
    setDetectError(null)
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
        updated[dragHandle.cornerIdx] = {
          x: Math.max(0, Math.min(1, pt.x + dragHandle.offset.x)),
          y: Math.max(0, Math.min(1, pt.y + dragHandle.offset.y)),
        }
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
      // defer clear so the spurious click (fired after mouseup in same task) sees the flag still set
      setTimeout(() => { cornerDragRef.current = false }, 0)
      return
    }
    if (!dragStart || !dragEnd) return
    const dx = Math.abs(dragEnd.x - dragStart.x)
    const dy = Math.abs(dragEnd.y - dragStart.y)
    if (dx > 0.01 && dy > 0.01) {
      const rect = rectFromPoints(dragStart, dragEnd)
      // Block-outright: a box centered on an already-defined card must not spawn a new candidate.
      if (insideExistingCard(centroid(rect))) {
        setDetectError(t.outlineAlreadyOutlined)
      } else {
        setPolygons(prev => [...prev, rect])
        setPolyMeta(prev => [...prev, { confidence: 1.0 }])
      }
    }
    setDragStart(null)
    setDragEnd(null)
  }

  // Grab a corner handle, remembering where on the handle it was grabbed
  const startCornerDrag = (e: React.MouseEvent | React.TouchEvent, polyIdx: number, cornerIdx: number) => {
    e.stopPropagation()
    cornerDragRef.current = true
    const { clientX, clientY } = getClientPos(e)
    const pt = toNorm(clientX, clientY)
    const corner = polygons[polyIdx][cornerIdx]
    const offset = pt ? { x: corner.x - pt.x, y: corner.y - pt.y } : { x: 0, y: 0 }
    setDragHandle({ polyIdx, cornerIdx, offset })
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
    const color = isFallback ? FALLBACK_COLOR : COLORS[polyIdx % COLORS.length]
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
            onMouseDown={e => startCornerDrag(e, polyIdx, ci)}
            // preventDefault suppresses synthesized mouse events on touch (prevents double-fire)
            onTouchStart={e => { e.preventDefault(); startCornerDrag(e, polyIdx, ci) }}
            onClick={e => e.stopPropagation()}
            onTouchEnd={e => { e.stopPropagation(); setDragHandle(null); setTimeout(() => { cornerDragRef.current = false }, 0) }}
          />
        ))}
      </g>
    )
  }

  // Crosshair marker where the user picked the background
  const renderBgMarker = () => {
    if (!bgPoint) return null
    const { x, y } = toSvg(bgPoint)
    return (
      <g style={{ pointerEvents: 'none' }}>
        <circle cx={x} cy={y} r={10} fill="none" stroke="white" strokeWidth={3} />
        <circle cx={x} cy={y} r={10} fill="none" stroke="#0ea5e9" strokeWidth={1.5} />
        <line x1={x - 16} y1={y} x2={x + 16} y2={y} stroke="#0ea5e9" strokeWidth={1.5} />
        <line x1={x} y1={y - 16} x2={x} y2={y + 16} stroke="#0ea5e9" strokeWidth={1.5} />
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
    ? t.outlineDetecting
    : pickingBg
      ? t.outlinePickBgHeader
    : tapMode
      ? polygons.length === 0
        ? t.outlineTapHeader(cardCount)
        : t.outlineSelectedN(polygons.length)
      : dragStart
        ? t.outlineDragging(polygons.length + 1)
        : polygons.length === 0
          ? t.outlineDragHeader(cardCount)
          : t.outlineSelectedN(polygons.length)

  return (
    <div className="fixed inset-0 z-50 bg-black flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 bg-gray-900 text-white shrink-0">
        <button onClick={onCancel} className="text-sm text-gray-300 hover:text-white">{t.outlineCancel}</button>
        <div className="text-sm font-semibold text-center flex-1">{headerText}</div>
        <button
          onClick={undoLast}
          className="text-sm text-gray-300 hover:text-white disabled:opacity-30"
          disabled={polygons.length === 0 || detecting}
          title={t.outlineUndoHint}
        >
          {t.outlineUndo}
        </button>
      </div>

      {/* Standing instructions — the interaction is not discoverable without them. */}
      <div className="bg-gray-800 px-4 py-2 text-xs text-gray-300 shrink-0 flex items-start gap-3">
        <div className="flex-1 space-y-1">
          <p>{tapMode ? t.outlineHintTap : t.outlineHintDrag}</p>
          {polyMeta.some(m => m.confidence === 0) && (
            <p className="text-amber-300">{bgPoint || !tapMode ? t.outlineHintAmber : t.outlineHintPickBg}</p>
          )}
        </div>
        {tapMode && (
          <button
            onClick={() => { setPickingBg(p => !p); setDetectError(null) }}
            disabled={detecting}
            className={`shrink-0 px-3 py-1.5 rounded-md border text-xs font-medium disabled:opacity-40 ${
              pickingBg
                ? 'bg-sky-500 border-sky-400 text-white'
                : 'border-sky-500 text-sky-300 hover:bg-sky-500/20'
            }`}
          >
            {bgPoint ? t.outlineChangeBg : t.outlinePickBg}
          </button>
        )}
      </div>

      {/* Image + overlay */}
      <div
        className="flex-1 overflow-hidden relative flex items-center justify-center bg-black select-none"
        style={{ touchAction: 'none', cursor: tapMode ? (detecting ? 'wait' : pickingBg ? 'cell' : 'crosshair') : 'default' }}
        {...(tapMode
          ? {
              onClick: handleTap,
              // if finger lifts outside the circle, clear dragHandle; otherwise tap to add card
              onTouchEnd: (e: React.TouchEvent) => { if (dragHandle !== null) { e.preventDefault(); setDragHandle(null); setTimeout(() => { cornerDragRef.current = false }, 0) } else { handleTap(e) } },
              onMouseMove: dragHandle ? handlePointerMove : undefined,
              onTouchMove: dragHandle ? handlePointerMove : undefined,
              onMouseUp: dragHandle ? handlePointerUp : undefined,
            }
          : {
              onMouseDown: handlePointerDown,
              onMouseMove: handlePointerMove,
              onMouseUp: handlePointerUp,
              onTouchStart: handlePointerDown,
              onTouchMove: handlePointerMove,
              onTouchEnd: handlePointerUp,
            }
        )}
      >
        <img
          ref={imgRef}
          src={imageUrl}
          alt={tapMode ? t.outlineAltTap : t.outlineAltDrag}
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
            {renderBgMarker()}
            {!tapMode && renderPreviewRect()}
          </svg>
        )}
        {detecting && (
          <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
            <div className="bg-black/60 text-white text-sm px-4 py-2 rounded-full">
              {t.outlineDetecting}
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
          <>
            <p className="text-center text-gray-400 text-xs mb-2">{t.outlineHintReady}</p>
            <button
              className="w-full bg-green-600 hover:bg-green-500 text-white font-semibold py-3 rounded-lg"
              onClick={() => onComplete(polygons)}
            >
              {t.outlineCropN(polygons.length)}
            </button>
          </>
        ) : (
          // A short prompt, deliberately not the instruction bar's full sentence: both
          // are on screen at once before the first outline, and spec §5 gives the
          // footer its own brief copy.
          <p className="text-center text-gray-400 text-sm">
            {tapMode ? t.outlineFooterTap : t.outlineFooterDrag}
          </p>
        )}
      </div>
    </div>
  )
}
