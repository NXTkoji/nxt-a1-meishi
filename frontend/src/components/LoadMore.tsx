import { useLang } from '../LangContext'

interface Props {
  /** How many rows are on screen right now. */
  loaded: number
  /** How many exist in total for this query — from a /count or /facets response,
   *  never from the length of what happens to have been fetched. */
  total: number
  onLoadMore: () => void
  isLoading?: boolean
}

/**
 * "Showing n of m" plus a button, rendering nothing once everything is loaded.
 *
 * Shared by the month sections of the Collection tree, and (in a later task) by the
 * search results and the Persons tab. The point of taking `total` from the server
 * rather than inferring "there might be more" from a full page is that the pager can
 * be honest before anything is fetched, and cannot get stuck one page short.
 */
export function LoadMore({ loaded, total, onLoadMore, isLoading }: Props) {
  const { t } = useLang()
  if (loaded >= total) return null

  return (
    <div className="flex flex-col items-center gap-1 py-3">
      <p className="text-xs text-gray-400">{t.showingNofM(loaded, total)}</p>
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
