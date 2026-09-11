import { useState } from 'react'
import { useInfiniteQuery } from '@tanstack/react-query'
import { listPersons } from '../api'
import type { PersonListItem } from '../types'
import { LoadError, LoadMore } from './LoadMore'
import { useLang } from '../LangContext'
import { formatDate } from '../lib/dates'

/** The API's maximum limit for GET /api/v2/persons. A group holding more than this
 *  needs more than one request, which the pager below handles. Same size as a month. */
const PAGE = 500

interface Props {
  /** Two-letter country code, or null for the bucket of persons with no country. */
  code: string | null
  /** Display name for the header, already localised by the page. */
  label: string
  /** Authoritative count from the facets endpoint — correct before any fetch, because
   *  the backend buckets and filters by the same derived-country expression. */
  count: number
  defaultExpanded: boolean
  /** Debounced, trimmed search term; '' when not searching. */
  q: string
  selectMode: boolean
  /** Keyed by external_id. A map rather than a set of ids, see CollectionPage. */
  selected: ReadonlyMap<string, PersonListItem>
  onToggleSelect: (p: PersonListItem) => void
}

/**
 * One country group of the Persons tab — the persons counterpart of MonthSection.
 *
 * Why each group loads its own people instead of the page fetching one list and
 * grouping it: paginating a single mixed list would drop newly loaded people into
 * groups already on screen, reshuffling what the user is looking at, and a capped
 * list silently loses everyone past the cap. Per-group queries keep every person
 * reachable (the facet count sizes the pager) and fetch only groups that are open.
 */
export function CountrySection({
  code,
  label,
  count,
  defaultExpanded,
  q,
  selectMode,
  selected,
  onToggleSelect,
}: Props) {
  const { t, lang } = useLang()
  const [expanded, setExpanded] = useState(defaultExpanded)

  // One stable cache entry per (group, search term); each "Load more" appends a page
  // to it, so loaded rows stay put while the next page fetches, and collapsing then
  // re-expanding replays from cache.
  // The search term is in the key so each term owns its own pages: page 2 of an old
  // search cannot leak into a new one, and there is no paging state to reset.
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
    queryKey: ['persons', 'country', code ?? 'none', q],
    // 'none' is the backend's name for the null bucket; a real code is sent as-is.
    queryFn: ({ pageParam }) => listPersons(q || undefined, PAGE, pageParam, code ?? 'none'),
    initialPageParam: 0,
    getNextPageParam: (last, all) => {
      // An empty page means the group ran out, whatever the facet said. Without this a
      // stale count (persons merged or deleted since the facets loaded) would keep
      // `hasNextPage` true forever behind a button that fetches nothing.
      if (last.length === 0) return undefined
      const loaded = all.reduce((n, page) => n + page.length, 0)
      return loaded < count ? loaded : undefined
    },
    enabled: expanded,
  })
  // No `?? []`: `undefined` means "no page yet" (in flight or paused), which the render
  // must keep distinct from "this group is empty".
  const persons = data?.pages.flat()

  return (
    <div>
      <button
        className="w-full flex items-center gap-2 text-sm font-semibold text-gray-700 py-1 hover:text-blue-600 text-left"
        onClick={() => setExpanded(v => !v)}
        aria-expanded={expanded}
      >
        {/* The glyph is purely visual; aria-expanded carries the state for screen readers. */}
        <span className="text-xs text-gray-400" aria-hidden="true">{expanded ? '▼' : '▶'}</span>
        <span>{label}</span>
        <span className="text-xs text-gray-400 font-normal">({count})</span>
      </button>

      {/* Status order, as on every list in the Collection page: isLoadingError →
          data === undefined → empty → rows → pager. A paused first page is pending but
          neither loading nor errored, so an isLoading guard would show an empty group. */}
      {expanded && (
        isLoadingError ? (
          // The first page failed and nothing is cached: show the failure, not an empty
          // group that reads as "nobody here". Retrying re-runs the whole query.
          <LoadError onRetry={() => refetch()} isRetrying={isFetching} />
        ) : persons === undefined ? (
          // No page has arrived yet, whether the request is in flight or paused.
          <p className="ml-4 text-xs text-gray-400 py-2">{t.loading}</p>
        ) : persons.length === 0 ? (
          // Only reachable when the facet count went stale (e.g. a merge emptied the
          // group between the two requests); the facets refetch then removes the group.
          <p className="ml-4 text-xs text-gray-400 py-2">{q ? t.noPersonsMatch(q) : '—'}</p>
        ) : (
          <>
            {/* Rows render in server order with no client re-sort: the backend already
                returns display order, and re-sorting in the browser would move people
                that are already on screen each time another page is appended. */}
            <div className="ml-4 divide-y divide-gray-100 rounded-xl border border-gray-200 bg-white">
              {persons.map(p => {
                const isSelected = selected.has(p.external_id)
                if (selectMode) {
                  return (
                    <div
                      key={p.id}
                      onClick={() => onToggleSelect(p)}
                      className={`flex items-center gap-3 px-4 py-3 cursor-pointer transition-colors ${isSelected ? 'bg-blue-50' : 'hover:bg-gray-50'}`}
                    >
                      <input
                        type="checkbox"
                        checked={isSelected}
                        onChange={() => onToggleSelect(p)}
                        onClick={e => e.stopPropagation()}
                        className="accent-blue-600 w-4 h-4 shrink-0"
                      />
                      <div className="w-8 h-8 rounded-full bg-blue-100 text-blue-700 flex items-center justify-center text-sm font-medium shrink-0">
                        {(p.primary_name ?? '?').charAt(0)}
                      </div>
                      <div>
                        <p className="text-sm font-medium text-gray-900">{p.primary_name ?? t.noName}</p>
                        <p className="text-xs text-gray-400">{formatDate(p.created_at, lang)}</p>
                      </div>
                    </div>
                  )
                }
                return (
                  <a
                    key={p.id}
                    href={`/persons/${p.external_id}`}
                    className="flex items-center gap-3 px-4 py-3 hover:bg-gray-50 transition-colors"
                  >
                    <div className="w-8 h-8 rounded-full bg-blue-100 text-blue-700 flex items-center justify-center text-sm font-medium shrink-0">
                      {(p.primary_name ?? '?').charAt(0)}
                    </div>
                    <div>
                      <p className="text-sm font-medium text-gray-900">{p.primary_name ?? t.noName}</p>
                      <p className="text-xs text-gray-400">{formatDate(p.created_at, lang)}</p>
                    </div>
                  </a>
                )
              })}
            </div>
            {/* Gated on hasNextPage, which getNextPageParam derives from the facet count
                and an empty last page. A failed next page leaves hasNextPage true, so the
                error branch is reachable: it keeps the rows and retries just that page. */}
            {hasNextPage && (isFetchNextPageError ? (
              <LoadError onRetry={() => fetchNextPage()} isRetrying={isFetchingNextPage} />
            ) : (
              <LoadMore
                loaded={persons.length}
                total={count}
                // Only a next-page fetch makes the button busy; isFetching is also true
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
