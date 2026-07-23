/**
 * Birthday input: Month + Day dropdowns and an optional numeric Year field.
 *
 * Invalid values are impossible to enter (month/day are constrained selects,
 * year is digit-only). Supports year-unknown birthdays.
 *
 * Serialized value stored on ParsedCard.birthday:
 *   full date       -> "YYYY-MM-DD"
 *   year unknown    -> "--MM-DD"
 *   month/day unset -> ""  (no birthday)
 */
import { useEffect, useState } from 'react'
import { useLang } from '../LangContext'

// month/day are 1-based strings ("1".."12" / "1".."31"); year is a 4-digit string or "".
export function serializeBirthday(year: string, month: string, day: string): string {
  if (!month || !day) return ''
  const mm = month.padStart(2, '0')
  const dd = day.padStart(2, '0')
  if (year) return `${year.padStart(4, '0')}-${mm}-${dd}`
  return `--${mm}-${dd}`
}

export function parseBirthday(value: string | undefined): { year: string; month: string; day: string } {
  if (value) {
    const full = value.match(/^(\d{4})-(\d{2})-(\d{2})$/)
    if (full) return { year: full[1], month: String(Number(full[2])), day: String(Number(full[3])) }
    const noYear = value.match(/^--(\d{2})-(\d{2})$/)
    if (noYear) return { year: '', month: String(Number(noYear[1])), day: String(Number(noYear[2])) }
  }
  return { year: '', month: '', day: '' }
}

export function BirthdayField({
  value,
  onEdit,
}: {
  value: string
  onEdit: (v: string) => void
}) {
  const { t } = useLang()
  // Local state is the source of truth for the three controls. A serialized
  // birthday needs BOTH month and day, but the user sets them one dropdown at a
  // time — so a partial selection (month set, day not yet) must persist locally
  // even though it serializes to "" and stores nothing on the parent yet.
  const [parts, setParts] = useState(() => parseBirthday(value))
  const { year, month, day } = parts

  // Re-sync from the prop only on a genuine external change (e.g. loading an
  // existing card), not on echoes of our own serialization. When our current
  // parts already serialize to the incoming value, leave local state alone so a
  // partial selection isn't wiped.
  useEffect(() => {
    if (serializeBirthday(year, month, day) !== (value ?? '')) {
      setParts(parseBirthday(value))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value])

  const update = (next: Partial<{ year: string; month: string; day: string }>) => {
    const merged = { ...parts, ...next }
    setParts(merged)
    onEdit(serializeBirthday(merged.year, merged.month, merged.day))
  }

  const selectCls = 'text-sm border border-gray-200 rounded px-1 py-0.5 bg-white text-gray-700'

  return (
    <div className="flex items-center gap-2 py-1.5 border-b border-gray-100 last:border-0">
      <span className="w-36 shrink-0 text-xs text-gray-400">{t.fieldBirthday}</span>
      <div className="flex flex-1 items-center gap-1">
        <select className={selectCls} value={month} onChange={e => update({ month: e.target.value })}>
          <option value="">{t.birthdayMonth}</option>
          {Array.from({ length: 12 }, (_, i) => String(i + 1)).map(mo => (
            <option key={mo} value={mo}>{mo}</option>
          ))}
        </select>
        <select className={selectCls} value={day} onChange={e => update({ day: e.target.value })}>
          <option value="">{t.birthdayDay}</option>
          {Array.from({ length: 31 }, (_, i) => String(i + 1)).map(d => (
            <option key={d} value={d}>{d}</option>
          ))}
        </select>
        <input
          className="w-24 text-sm border border-gray-200 rounded px-2 py-0.5"
          inputMode="numeric"
          placeholder={t.birthdayYear}
          value={year}
          onChange={e => update({ year: e.target.value.replace(/\D/g, '').slice(0, 4) })}
        />
      </div>
    </div>
  )
}
