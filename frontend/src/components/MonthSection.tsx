import { useState } from 'react'
import type { ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { listCards } from '../api'
import type { CardListItem } from '../types'
import { LoadMore } from './LoadMore'
import { useLang } from '../LangContext'

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
  // How many PAGE-sized pages of this month to hold. Kept as a page *count* rather
  // than an accumulated array so the query key stays a plain value and TanStack owns
  // the cache: collapsing and re-expanding replays from cache instead of refetching.
  const [pages, setPages] = useState(1)
  const monthKey = `${year}-${String(month).padStart(2, '0')}`

  const { data: cards = [], isFetching } = useQuery<CardListItem[]>({
    queryKey: ['cards', 'month', monthKey, pages],
    queryFn: async () => {
      const batches = await Promise.all(
        Array.from({ length: pages }, (_, i) =>
          listCards({ month: monthKey, limit: PAGE, offset: i * PAGE }),
        ),
      )
      return batches.flat()
    },
    enabled: expanded,
  })

  return (
    <div className="ml-4 mb-2">
      <button
        className="flex items-center gap-2 text-xs font-medium text-gray-500 py-0.5 hover:text-blue-500"
        onClick={() => setExpanded(v => !v)}
      >
        <span className="text-gray-400">{expanded ? '▼' : '▶'}</span>
        <span>{year}/{String(month).padStart(2, '0')}</span>
        <span className="text-gray-400">({count})</span>
      </button>

      {expanded && (
        <>
          {isFetching && cards.length === 0 ? (
            <p className="text-xs text-gray-400 py-2">{t.loading}</p>
          ) : (
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3 mt-2">
              {cards.map(renderCard)}
            </div>
          )}
          <LoadMore
            loaded={cards.length}
            total={count}
            isLoading={isFetching}
            onLoadMore={() => setPages(p => p + 1)}
          />
        </>
      )}
    </div>
  )
}
