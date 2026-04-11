/**
 * CardOutlineSelector
 *
 * User drags a rectangle around each card. After dragging, each corner handle
 * can be dragged individually to correct perspective (skewed photos).
 *
 * Normalized [0,1] coordinates are returned to the parent.
 */
import React, { useRef, useState, useCallback, useEffect } from 'react'
import type { Point } from '../api/sessions'

interface Props {
  imageUrl: string
  cardCount: number
  onComplete: (polygons: Point[][]) => void
  onCancel: () => void
}

const COLORS = ['#ef4444', '#f97316', '#22c55e', '#3b82f6', '#a855f7', '#ec4899']

// A placed polygon — 4 corners in TL/TR/BR/BL order (normalized)
type Polygon = [Point, Point, Point, Point]

// Which polygon+corner is being dragged for fine-tuning
interface DragHandle { polyIdx: number; cornerIdx: number }

export default function CardOutlineSelector({ imageUrl, cardCount, onComplete, onCancel }: Props) {
  const imgRef = useRef<HTMLImageElement>(null)
  // imgRect is read fresh on every pointer event — never cached in state
  // so accumulated error across cards is impossible
  const [imgRect, setImgRect] = useState<DOMRect | null>(null)

  // Completed polygons
  const [polygons, setPolygons] = useState<Polygon[]>([])

  // Live drag state for drawing a new rectangle
  const [dragStart, setDragStart] = useState<Point | null>(null)
  const [dragEnd, setDragEnd] = useState<Point | null>(null)

  // Which corner handle is being dragged for adjustment
  const [dragHandle, setDragHandle] = useState<DragHandle | null>(null)

  const canCrop = polygons.length >= 1

  // Always read fresh rect from the DOM — never rely on stale cached value
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

  // Re-read imgRect after footer height changes (text ↔ button swap)
  useEffect(() => {
    requestAnimationFrame(() => {
      const r = getFreshRect()
      if (r) setImgRect(r)
    })
  }, [canCrop, getFreshRect])

  // Convert client coordinates to normalized image coords using a fresh rect read
  const toNorm = useCallback((clientX: number, clientY: number): Point | null => {
    const rect = getFreshRect()
    if (!rect) return null
    return {
      x: Math.max(0, Math.min(1, (clientX - rect.left) / rect.width)),
      y: Math.max(0, Math.min(1, (clientY - rect.top) / rect.height)),
    }
  }, [getFreshRect])

  // Convert normalized → SVG pixel coords (uses cached rect for rendering only — fine)
  const toSvg = (pt: Point) => ({
    x: pt.x * (imgRect?.width ?? 0),
    y: pt.y * (imgRect?.height ?? 0),
  })

  // Build a rect polygon from two opposite corners
  const rectFromPoints = (a: Point, b: Point): Polygon => [
    { x: Math.min(a.x, b.x), y: Math.min(a.y, b.y) }, // TL
    { x: Math.max(a.x, b.x), y: Math.min(a.y, b.y) }, // TR
    { x: Math.max(a.x, b.x), y: Math.max(a.y, b.y) }, // BR
    { x: Math.min(a.x, b.x), y: Math.max(a.y, b.y) }, // BL
  ]

  // ── Pointer events on the image area ─────────────────────────────────────

  const getClientPos = (e: React.MouseEvent | React.TouchEvent) => {
    if ('touches' in e) {
      const t = e.touches[0] ?? e.changedTouches[0]
      return { clientX: t.clientX, clientY: t.clientY }
    }
    return { clientX: (e as React.MouseEvent).clientX, clientY: (e as React.MouseEvent).clientY }
  }

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
      // Dragging a corner handle to adjust an existing polygon
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
    // Only register if the drag had meaningful size (>1% of image)
    const dx = Math.abs(dragEnd.x - dragStart.x)
    const dy = Math.abs(dragEnd.y - dragStart.y)
    if (dx > 0.01 && dy > 0.01) {
      setPolygons(prev => [...prev, rectFromPoints(dragStart, dragEnd)])
    }
    setDragStart(null)
    setDragEnd(null)
  }

  const undoLast = () => {
    setPolygons(prev => prev.slice(0, -1))
    setDragStart(null)
    setDragEnd(null)
  }

  // ── SVG rendering ─────────────────────────────────────────────────────────

  const polyToSvgPoints = (poly: Polygon) =>
    poly.map(p => `${toSvg(p).x},${toSvg(p).y}`).join(' ')

  const renderPolygon = (poly: Polygon, color: string, label: string, polyIdx: number) => {
    const svgPts = poly.map(toSvg)
    return (
      <g key={polyIdx}>
        <polygon
          points={polyToSvgPoints(poly)}
          fill={`${color}33`}
          stroke={color}
          strokeWidth={2}
        />
        <text
          x={svgPts[0].x + 6} y={svgPts[0].y - 6}
          fontSize={11} fill={color} fontWeight="bold"
          stroke="white" strokeWidth={3} paintOrder="stroke"
        >
          {label}
        </text>
        {/* Draggable corner handles */}
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

  const headerText = dragStart
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
          disabled={polygons.length === 0}
        >
          Undo
        </button>
      </div>

      {/* Image + overlay */}
      <div
        className="flex-1 overflow-hidden relative flex items-center justify-center bg-black select-none"
        onMouseDown={handlePointerDown}
        onMouseMove={handlePointerMove}
        onMouseUp={handlePointerUp}
        onTouchStart={handlePointerDown}
        onTouchMove={handlePointerMove}
        onTouchEnd={handlePointerUp}
        style={{ touchAction: 'none' }}
      >
        <img
          ref={imgRef}
          src={imageUrl}
          alt="Drag to select card boundaries"
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
            {polygons.map((poly, i) =>
              renderPolygon(poly, COLORS[i % COLORS.length], `Card ${i + 1}`, i)
            )}
            {renderPreviewRect()}
          </svg>
        )}
      </div>

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
            Press and drag to draw a box around each card
          </p>
        )}
      </div>
    </div>
  )
}
