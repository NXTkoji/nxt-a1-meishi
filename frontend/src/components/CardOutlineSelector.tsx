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
interface PolyMeta { confidence: number }

export default function CardOutlineSelector({ imageUrl, cardCount, onComplete, onCancel, onDetectCorners }: Props) {
  const imgRef = useRef<HTMLImageElement>(null)
  const [imgRect, setImgRect] = useState<DOMRect | null>(null)

  const [polygons, setPolygons] = useState<Polygon[]>([])
  const [polyMeta, setPolyMeta] = useState<PolyMeta[]>([])

  const [dragStart, setDragStart] = useState<Point | null>(null)
  const [dragEnd, setDragEnd] = useState<Point | null>(null)

  const [detecting, setDetecting] = useState(false)
  const [detectError, setDetectError] = useState<string | null>(null)

  const [dragHandle, setDragHandle] = useState<DragHandle | null>(null)
  // remains true from corner mousedown/touchstart until AFTER the spurious click fires
  const cornerDragRef = useRef(false)

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
    if (dragHandle || detecting || cornerDragRef.current) return
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
    } catch {
      setDetectError('Corner detection failed — try again')
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
      // defer clear so the spurious click (fired after mouseup in same task) sees the flag still set
      setTimeout(() => { cornerDragRef.current = false }, 0)
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
            onMouseDown={e => { e.stopPropagation(); cornerDragRef.current = true; setDragHandle({ polyIdx, cornerIdx: ci }) }}
            // preventDefault suppresses synthesized mouse events on touch (prevents double-fire)
            onTouchStart={e => { e.stopPropagation(); e.preventDefault(); cornerDragRef.current = true; setDragHandle({ polyIdx, cornerIdx: ci }) }}
            onClick={e => e.stopPropagation()}
            onTouchEnd={e => { e.stopPropagation(); setDragHandle(null); setTimeout(() => { cornerDragRef.current = false }, 0) }}
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
    ? 'Detecting corners…'
    : tapMode
      ? polygons.length === 0
        ? `Tap the center of each card (${cardCount} detected)`
        : `${polygons.length} card${polygons.length > 1 ? 's' : ''} tapped — tap more or Crop`
      : dragStart
        ? `Card ${polygons.length + 1} — drag to define boundary`
        : polygons.length === 0
          ? `Drag around each card (${cardCount} detected)`
          : `${polygons.length} card${polygons.length > 1 ? 's' : ''} outlined — draw more or tap Crop`

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
