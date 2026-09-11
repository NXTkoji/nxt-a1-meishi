/**
 * The one canonical month key: "YYYY-MM", zero-padded, e.g. "2026-09".
 *
 * This is the wire format for `?month=` and is also used for React keys and the
 * eager-month set in CollectionPage. Every caller must build keys through this
 * function: an unpadded "2026-9" on one side and a padded "2026-09" on the other
 * would never match, and the failure would be silent (every month quietly
 * collapsed) rather than an error.
 *
 * It lives in its own module rather than in MonthSection.tsx because a component
 * file may export only components, or React Fast Refresh stops working for it.
 */
export function monthKey(year: number, month: number): string {
  return `${year}-${String(month).padStart(2, '0')}`
}
