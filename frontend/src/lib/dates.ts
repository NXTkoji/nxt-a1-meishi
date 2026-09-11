import type { Lang } from '../i18n'

/**
 * Date display for the whole app.
 *
 * Every date on screen follows two rules:
 *
 *  1. Format by the UI language (`lang` from LangContext), never the browser locale.
 *     A bare `toLocaleDateString()` uses the browser locale, so a Japanese UI on an
 *     en-US browser printed "8/29/2026" — right next to a raw "2026-08-25".
 *
 *  2. Show the calendar date the backend files a record under: the leading YYYY-MM-DD
 *     of the wire string, taken literally. The API sends two shapes:
 *       - received_date: "2026-09-07"                  (a DATE column)
 *       - created_at:    "2026-09-09T01:35:05.235877"  (naive UTC from datetime.utcnow(), no "Z")
 *     Passing either to `new Date()` can shift the day. The date-only form is read as
 *     UTC midnight (the previous day anywhere west of UTC); the naive timestamp is read
 *     as *local* time (8 hours off in Taipei). And the Collection page buckets cards by
 *     EXTRACT(month FROM coalesce(received_date, created_at)) on the raw UTC value, so a
 *     locally converted date could contradict the month section the card is listed in.
 */

/** One formatter per language, built on first use: a month section renders many dates. */
const formatters = new Map<Lang, Intl.DateTimeFormat>()

function formatterFor(lang: Lang): Intl.DateTimeFormat {
  let formatter = formatters.get(lang)
  if (!formatter) {
    // dateStyle 'medium' is unambiguous in all three languages:
    //   ja "2026/09/07", en "Sep 7, 2026", zh-TW "2026年9月7日".
    // A numeric style gives en "09/07/2026" — month-first, easily misread as 9 July.
    // timeZone 'UTC' pairs with the UTC-midnight Date built in formatDate, so the
    // formatter itself can never move the day.
    formatter = new Intl.DateTimeFormat(lang, { dateStyle: 'medium', timeZone: 'UTC' })
    formatters.set(lang, formatter)
  }
  return formatter
}

const LEADING_DATE = /^(\d{4})-(\d{2})-(\d{2})/

/**
 * Format an API date or timestamp as a calendar date in the UI language.
 *
 * A value that does not start with YYYY-MM-DD is returned unchanged, so malformed
 * data shows as-is instead of as "Invalid Date".
 */
export function formatDate(value: string, lang: Lang): string {
  const match = value.match(LEADING_DATE)
  if (!match) return value
  const [, year, month, day] = match
  const utcMidnight = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day)))
  return formatterFor(lang).format(utcMidnight)
}

/**
 * The date a card is filed under: when it was received, falling back to when it was
 * scanned. Mirrors `_filing_date()` in app/routers/v2/cards.py — keep the two in step,
 * or a card's displayed date will disagree with the month section it appears in.
 */
export function formatFilingDate(
  card: { received_date?: string; created_at: string },
  lang: Lang,
): string {
  return formatDate(card.received_date ?? card.created_at, lang)
}

/**
 * Today on the user's own calendar, as "YYYY-MM-DD" — the default for a date input
 * such as the Scan page's received date.
 *
 * Why not `new Date().toISOString().slice(0, 10)`: toISOString() is always UTC, so in
 * Taipei (UTC+8) it returns yesterday's date from 00:00 to 07:59 local time. A card
 * scanned then was saved one day early — and on the 1st of a month, filed under the
 * previous month on the Collection page.
 *
 * Why local here, when rule 2 above displays the UTC date of `created_at`: that rule is
 * about reading back a UTC timestamp the backend already bucketed. `received_date` is a
 * DATE column with no time zone; it records the day the person handed over the card,
 * and that day is the one on the user's wall calendar.
 *
 * Built from getFullYear/getMonth/getDate (all local-time getters) and zero-padded,
 * which is exactly the value format `<input type="date">` requires.
 */
export function todayLocalISODate(): string {
  const now = new Date()
  const year = now.getFullYear()
  // getMonth() is 0-based (January = 0), so add 1 before padding.
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}
