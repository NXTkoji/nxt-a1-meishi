import { useState } from 'react'
import type { ReactNode } from 'react'
import { useInfiniteQuery } from '@tanstack/react-query'
import { listCards } from '../api'
import type { CardListItem } from '../types'
import { LoadError, LoadMore } from './LoadMore'
import { useLang } from '../LangContext'
import { monthKey } from '../lib/monthKey'

/** The API's maximum limit for GET /api/v2/cards. A month holding more than this
 *  needs more than one request — see the pagination note on the component. */
const PAGE = 500

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
    isLoadingError,
    isFetchNextPageError,
    hasNextPage,
    fetchNextPage,
    refetch,
  } = useInfiniteQuery({
    queryKey: ['cards', 'month', key], // no page count in the key
    queryFn: ({ pageParam }) => listCards({ month: key, limit: PAGE, offset: pageParam }),
    initialPageParam: 0,
    // The facet count decides whether more remain — same invariant, expressed as the
    // next offset rather than a page count. Returning undefined tells TanStack there is
    // no next page.
    getNextPageParam: (last, all) => {
      // An empty page means the month ran out, whatever the facet said. Facets are
      // cached (staleTime), so a card deleted or re-dated since they loaded leaves the
      // header count too high; without this, `hasNextPage` would stay true forever
      // behind a "Load more" that fetches nothing.
      if (last.length === 0) return undefined
      const loaded = all.reduce((n, p) => n + p.length, 0)
      return loaded < count ? loaded : undefined
    },
    enabled: expanded,
  })
  // No `?? []`: `undefined` means "no page yet" (in flight or paused), which the render
  // must keep distinct from "this month has no cards".
  const cards = data?.pages.flat()

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

      {/* Rendered from query status, not from isFetching/isLoading — see the status-order
          note on the facets query in CollectionPage.tsx. A paused first page is pending
          but not fetching, which an `isFetching` guard would have shown as an empty grid. */}
      {expanded && (
        // The first page failed: there is nothing to show, so show the failure rather
        // than an empty grid that reads as "this month has no cards". Retrying re-runs
        // the whole query. Having no page yet is what separates this from a failed
        // LATER page (also `isError`), which is handled below without hiding the grid.
        isLoadingError ? (
          <LoadError onRetry={() => refetch()} isRetrying={isFetching} />
        ) : cards === undefined ? (
          // No page has arrived yet — whether the request is in flight or paused.
          <p className="text-xs text-gray-400 py-2">{t.loading}</p>
        ) : (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3 mt-2">
              {cards.map(renderCard)}
            </div>
            {/* Gated on hasNextPage, which getNextPageParam derives from the facet count
                and an empty last page. A later page that failed leaves hasNextPage true,
                so the error branch stays reachable: it keeps the cards already loaded and
                its retry fetches just the missing page. */}
            {hasNextPage && (isFetchNextPageError ? (
              <LoadError onRetry={() => fetchNextPage()} isRetrying={isFetchingNextPage} />
            ) : (
              <LoadMore
                loaded={cards.length}
                total={count}
                // Only a next-page fetch makes the button busy. `isFetching` is also true
                // during a background refetch of loaded pages, which is not "loading more".
                isLoading={isFetchingNextPage}
                onLoadMore={() => fetchNextPage()}
              />
            ))}
          </>
        )
      )}
    </div>
  )
}
