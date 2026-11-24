/** Non-component helpers for the Relationship panel (kept apart so fast refresh works). */

/** The API reports errors as "422 Unprocessable Entity: {"detail": "..."}"; show only the detail. */
export function errorText(err: unknown): string {
  const raw = err instanceof Error ? err.message : String(err)
  const start = raw.indexOf('{')
  if (start >= 0) {
    try {
      const detail = JSON.parse(raw.slice(start)).detail
      if (typeof detail === 'string') return detail
      if (Array.isArray(detail)) return detail.map(d => d.msg ?? String(d)).join('; ')
    } catch { /* fall through to the raw text */ }
  }
  return raw
}

export const inputCls = 'w-full rounded border border-gray-300 px-2 py-1 text-sm focus:border-blue-500 focus:outline-none'
export const btnPrimary = 'rounded bg-blue-600 px-3 py-1 text-sm text-white hover:bg-blue-700 disabled:opacity-50'
export const btnGhost = 'rounded border border-gray-300 px-3 py-1 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50'
