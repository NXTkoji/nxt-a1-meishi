import { useLang } from '../LangContext'

interface Props {
  /** How many rows are on screen right now. */
  loaded: number
  /** How many exist in total for this query — from a /count or /facets response,
   *  never from the length of what happens to have been fetched.
   *
   *  `undefined` means "not known yet" (the count query is still in flight, or it
   *  failed). That is NOT the same as zero: the pager must stay usable, because
   *  hiding it would strand the user with no way to load the rest. */
  total?: number
  onLoadMore: () => void
  isLoading?: boolean
}

/**
 * "Showing n of m" plus a button, rendering nothing once everything is loaded.
 *
 * Shared by the month sections of the Collection tree, the search results, and the
 * country sections of the Persons tab. The point of taking `total` from the server
 * rather than inferring "there might be more" from a full page is that the pager can
 * be honest before anything is fetched, and cannot get stuck one page short.
 */
export function LoadMore({ loaded, total, onLoadMore, isLoading }: Props) {
  const { t } = useLang()
  // Only a *known* total can prove everything is loaded. With an unknown total we
  // keep the button, since "more may exist" is the only safe assumption.
  if (total !== undefined && loaded >= total) return null

  return (
    <div className="flex flex-col items-center gap-1 py-3">
      {/* "Showing n of m" needs a real m — omit the line rather than print "of 0". */}
      {total !== undefined && (
        <p className="text-xs text-gray-400">{t.showingNofM(loaded, total)}</p>
      )}
      <button
        onClick={onLoadMore}
        disabled={isLoading}
        className="btn-secondary text-sm disabled:opacity-50"
      >
        {isLoading ? t.loading : t.loadMore}
      </button>
    </div>
  )
}

interface LoadErrorProps {
  /** Re-run the failed request — typically TanStack's `refetch` or `fetchNextPage`. */
  onRetry: () => void
  /** True while the retry is in flight, so the button cannot be double-fired. */
  isRetrying?: boolean
}

/**
 * The counterpart to LoadMore for a request that failed.
 *
 * It exists so a failed fetch is never rendered as ordinary UI — an empty grid, "no
 * results", or the "scan your first card" empty state would all tell the user their
 * data is missing when it is only unreachable. It always offers a retry rather than
 * a dead end.
 *
 * It lives beside LoadMore because the same list views (month sections, search
 * results, and country sections) need both.
 */
export function LoadError({ onRetry, isRetrying }: LoadErrorProps) {
  const { t } = useLang()
  return (
    <div role="alert" className="flex flex-col items-center gap-2 py-6">
      <p className="text-sm text-red-600">{t.loadError}</p>
      <button
        onClick={onRetry}
        disabled={isRetrying}
        className="btn-secondary text-sm disabled:opacity-50"
      >
        {isRetrying ? t.loading : t.retry}
      </button>
    </div>
  )
}
