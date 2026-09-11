import type { Occasion } from '../types'

export interface OccasionMonth {
  year: number
  month: number          // 1-12, or 0 for the "undated" sentinel bucket (see occasionPeriod)
  occasions: Occasion[]
}

export interface OccasionYear {
  year: number            // or 0 for the "undated" sentinel bucket
  months: OccasionMonth[]
}

/** Sentinel bucket for an occasion whose date cannot be parsed. Sorts last (see
 *  groupOccasionsByMonth) and is rendered with t.occasionUndated instead of
 *  t.monthLabel, since "0年0月" would be nonsense. */
const UNDATED = { year: 0, month: 0 }

const LEADING_YEAR_MONTH = /^(\d{4})-(\d{2})/

/**
 * event_date is optional and, in practice, almost never set — both creation paths
 * post only a name. created_at (when it was scanned) is the fallback, and this is
 * the single owner of that rule.
 *
 * Deliberately does NOT use `new Date(...)`. Per the header comment of `lib/dates.ts`,
 * the API sends event_date as a DATE ("2026-04-20") and created_at as a naive-UTC
 * timestamp with no "Z" ("2026-09-09T01:35:05.235877"). `new Date()` reads the first
 * as UTC midnight and the second as LOCAL time — two different interpretations of
 * "no timezone" — which can shift the day, and at month boundaries the month, exactly
 * the bug dates.ts already works around for card filing dates. The fix there, and here,
 * is the same: take the leading YYYY-MM of the wire string literally instead of parsing
 * it into a Date at all.
 *
 * A string that doesn't start with YYYY-MM (missing/malformed) falls into the UNDATED
 * sentinel bucket rather than producing NaN — see the module-level UNDATED constant.
 */
export function occasionPeriod(o: Occasion): { year: number; month: number } {
  const match = (o.event_date ?? o.created_at).match(LEADING_YEAR_MONTH)
  if (!match) return UNDATED
  const [, year, month] = match
  return { year: Number(year), month: Number(month) }
}

/** Newest year first, newest month first; the undated bucket (year 0) sorts last
 *  since it is numerically smallest. Order within a month is preserved. */
export function groupOccasionsByMonth(occasions: Occasion[]): OccasionYear[] {
  const years = new Map<number, Map<number, Occasion[]>>()

  for (const o of occasions) {
    const { year, month } = occasionPeriod(o)
    if (!years.has(year)) years.set(year, new Map())
    const months = years.get(year)!
    if (!months.has(month)) months.set(month, [])
    months.get(month)!.push(o)
  }

  return [...years.entries()]
    .sort((a, b) => b[0] - a[0])
    .map(([year, months]) => ({
      year,
      months: [...months.entries()]
        .sort((a, b) => b[0] - a[0])
        .map(([month, os]) => ({ year, month, occasions: os })),
    }))
}

/** Flat month list across all years — for a <select>, which cannot nest optgroups. */
export function flattenOccasionMonths(occasions: Occasion[]): OccasionMonth[] {
  return groupOccasionsByMonth(occasions).flatMap(y => y.months)
}
