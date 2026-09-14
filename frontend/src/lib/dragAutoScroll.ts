/**
 * Auto-scroll the window while an HTML5 drag is in progress.
 *
 * Native drag-and-drop barely scrolls the page on its own (Chrome only reacts in a
 * few-pixel strip, Safari hardly at all), so a contact field could not be dragged to an
 * organization that was off-screen. Call `startDragAutoScroll()` from `onDragStart`:
 * while the pointer sits within EDGE_PX of the top or bottom of the viewport, the window
 * scrolls, faster the closer the pointer is to the edge.
 *
 * The scroll runs in a requestAnimationFrame loop rather than inside `dragover`, because
 * `dragover` fires irregularly (and slowly in Safari) — driving the scroll from it would
 * stutter. `dragover` only records the latest pointer position.
 */

const EDGE_PX = 80       // Height of the hot zone at the top and bottom of the viewport
const MAX_SPEED_PX = 18  // Scroll step per frame when the pointer is at the very edge
const IDLE_STOP_MS = 1000 // Safety stop if no dragover arrives (e.g. dragend was lost)

// Only one drag can happen at a time; keep a handle so a new drag replaces a stale loop.
let stopActive: (() => void) | null = null

export function startDragAutoScroll(): void {
  stopActive?.()

  let pointerY: number | null = null
  let lastEventAt = performance.now()
  let frame = 0

  const onDragOver = (e: DragEvent) => {
    // Some browsers report 0,0 for the final dragover of a drag; ignore those.
    if (e.clientX === 0 && e.clientY === 0) return
    pointerY = e.clientY
    lastEventAt = performance.now()
  }

  const tick = () => {
    if (performance.now() - lastEventAt > IDLE_STOP_MS) { stop(); return }
    if (pointerY !== null) {
      const step = scrollStep(pointerY, window.innerHeight)
      if (step !== 0) window.scrollBy(0, step)
    }
    frame = requestAnimationFrame(tick)
  }

  const stop = () => {
    cancelAnimationFrame(frame)
    // Capture phase so a drop handler that stops propagation cannot keep the loop alive.
    document.removeEventListener('dragover', onDragOver, true)
    document.removeEventListener('dragend', stop, true)
    document.removeEventListener('drop', stop, true)
    if (stopActive === stop) stopActive = null
  }

  document.addEventListener('dragover', onDragOver, true)
  // dragend is dispatched on the source element; if a drop re-renders and removes that
  // element, dragend never reaches document — so also stop on drop.
  document.addEventListener('dragend', stop, true)
  document.addEventListener('drop', stop, true)
  stopActive = stop
  frame = requestAnimationFrame(tick)
}

/** Pixels to scroll this frame: negative near the top, positive near the bottom, else 0. */
function scrollStep(y: number, viewportHeight: number): number {
  if (y < EDGE_PX) return -Math.ceil(MAX_SPEED_PX * (1 - y / EDGE_PX))
  const fromBottom = viewportHeight - y
  if (fromBottom < EDGE_PX) return Math.ceil(MAX_SPEED_PX * (1 - fromBottom / EDGE_PX))
  return 0
}
