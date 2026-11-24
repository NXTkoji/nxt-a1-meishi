/** Small pieces shared by the customer and supplier sections of the Relationship panel. */
import type { PartyStatus } from '../types'
import type { Translations } from '../i18n'

export function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="block text-xs text-gray-500 mb-0.5">{label}</span>
      {children}
      {hint && <span className="block text-xs text-gray-400 mt-0.5">{hint}</span>}
    </label>
  )
}

export function FolderLink({ url, label }: { url?: string | null; label: string }) {
  if (!url) return null
  return (
    <a href={url} target="_blank" rel="noreferrer" className="text-blue-600 hover:underline">
      {label}
    </a>
  )
}

/** What the select holds: a status, or '' for none. */
export type StatusValue = PartyStatus | ''

/**
 * Status selector for one role (customer or supplier).
 *
 * A status that has a code can only move between Active and Inactive. Without a code,
 * Active is not set here: choosing it opens the review-and-approve flow (onNeedsApproval),
 * and Inactive is unavailable because there is nothing to deactivate.
 */
export function StatusSelect({ t, value, hasCode, busy, onChange, onNeedsApproval }: {
  t: Translations
  value: StatusValue
  hasCode: boolean
  busy?: boolean
  onChange: (next: StatusValue) => void
  onNeedsApproval: () => void
}) {
  const options: { value: StatusValue; label: string; disabled: boolean }[] = [
    { value: '', label: t.statusNone, disabled: hasCode },
    { value: 'potential', label: t.statusPotential, disabled: hasCode },
    { value: 'active', label: t.statusActive, disabled: false },
    { value: 'inactive', label: t.statusInactive, disabled: !hasCode },
  ]
  return (
    <select
      className="rounded border border-gray-300 px-2 py-1 text-xs focus:border-blue-500 focus:outline-none disabled:opacity-50"
      value={value}
      disabled={busy}
      title={hasCode ? t.relLockedHint : undefined}
      onChange={e => {
        const next = e.target.value as StatusValue
        if (next === 'active' && !hasCode) { onNeedsApproval(); return }
        if (next !== value) onChange(next)
      }}
    >
      {options.map(o => (
        <option key={o.value} value={o.value} disabled={o.disabled && o.value !== value}>{o.label}</option>
      ))}
    </select>
  )
}
