import { useState } from 'react'
import type { ReactNode } from 'react'
import { useInfiniteQuery } from '@tanstack/react-query'
import { listCards } from '../api'
import type { CardListItem } from '../types'
import { LoadError, LoadMore } from './LoadMore'
import { useLang } from '../LangContext'

/** The API's maximum limit for GET /api/v2/cards. A month holding more than this
 *  needs more than one request — see the pagination note on the component. */
const PAGE = 500

/**
 * The one canonical month key: "YYYY-MM", zero-padded, e.g. "2026-09".
 *
 * This is the wire format for `?month=` and is also used for React keys and the
 * eager-month set in CollectionPage. Every caller must build keys through this
 * function: an unpadded "2026-9" on one side and a padded "2026-09" on the other
 * would never match, and the failure would be silent (every month quietly
 * collapsed) rather than an error.
 */
export function monthKey(year: number, month: number): string {
  return `${year}-${String(month).padStart(2, '0')}`
}

interface Props {
  year: number
  /** 1-12, as the facets endpoint reports it. */
  month: number
  /** Authoritative count from the facets endpoint — correct even before any fetch,
   *  because the backend buckets and filters by the same filing-date expression. */
  count: number
  defaultExpanded: boolean
  renderCard: (card: CardListItem) => ReactNode
}

/**
 * One month in the browse tree.
 *
 * Two things make this correct at any collection size:
 *  1. Cards are fetched only once the month is expanded, so opening the Collection
 *     page costs one facets request plus the eagerly expanded months, not the whole
 *     table.
 *  2. It paginates *within* the month. A month larger than PAGE would otherwise
 *     silently truncate — exactly the bug this rewrite removes at the page level.
 *     The facet count, not the length of the last response, decides whether more
 *     remain, so the pager cannot stop one page short.
 */
export function MonthSection({ year, month, count, defaultExpanded, renderCard }: Props) {
  const { t } = useLang()
  const [expanded, setExpanded] = useState(defaultExpanded)
  const key = monthKey(year, month)

  // Pagination lives in the TanStack cache under ONE stable key per month. Each
  // "Load more" appends a page to that entry, so:
  //  - already-loaded pages stay on screen while the next one fetches (no blank grid),
  //  - page n costs exactly one request, not a re-request of pages 1..n,
  //  - collapsing and re-expanding (or the year hiding this section) replays from
  //    cache instead of refetching.
  const {
    data,
    isFetching,
    isFetchingNextPage,
    isError,
    isFetchNextPageError,
    fetchNextPage,
    refetch,
  } = useInfiniteQuery({
    queryKey: ['cards', 'month', key], // no page count in the key
    queryFn: ({ pageParam }) => listCards({ month: key, limit: PAGE, offset: pageParam }),
    initialPageParam: 0,
    // The facet count stays the authority on whether more remain — same invariant,
    // expressed as the next offset rather than a page count. Returning undefined
    // tells TanStack there is no next page.
    getNextPageParam: (_last, all) => {
      const loaded = all.reduce((n, p) => n + p.length, 0)
      return loaded < count ? loaded : undefined
    },
    enabled: expanded,
  })
  const cards = data?.pages.flat() ?? []

  return (
    <div className="ml-4 mb-2">
      <button
        className="flex items-center gap-2 text-xs font-medium text-gray-500 py-0.5 hover:text-blue-500"
        onClick={() => setExpanded(v => !v)}
        aria-expanded={expanded}
      >
        {/* The glyph is purely visual; aria-expanded carries the state for screen readers. */}
        <span className="text-gray-400" aria-hidden="true">{expanded ? '▼' : '▶'}</span>
        <span>{year}/{String(month).padStart(2, '0')}</span>
        <span className="text-gray-400">({count})</span>
      </button>

      {expanded && (
        // The first page failed: there is nothing to show, so show the failure rather
        // than an empty grid that reads as "this month has no cards". Retrying re-runs
        // the whole query.
        isError && cards.length === 0 ? (
          <LoadError onRetry={() => refetch()} isRetrying={isFetching} />
        ) : (
          <>
            {isFetching && cards.length === 0 ? (
              <p className="text-xs text-gray-400 py-2">{t.loading}</p>
            ) : (
              <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3 mt-2">
                {cards.map(renderCard)}
              </div>
            )}
            {/* A later page failed: keep the cards already loaded, and swap the pager
                for an explicit error whose retry fetches just the missing page. */}
            {isFetchNextPageError ? (
              <LoadError onRetry={() => fetchNextPage()} isRetrying={isFetchingNextPage} />
            ) : (
              <LoadMore
                loaded={cards.length}
                total={count}
                isLoading={isFetching}
                onLoadMore={() => fetchNextPage()}
              />
            )}
          </>
        )
      )}
    </div>
  )
}
