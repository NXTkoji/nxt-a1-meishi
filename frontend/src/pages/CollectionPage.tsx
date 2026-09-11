import { useState, useMemo } from 'react'
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { countCardsTotal, listCardFacets, listCards, listPersonFacets, listCountries } from '../api'
import { useLang } from '../LangContext'
import type { CardFacet, CardListItem, Country, PersonFacet, PersonListItem } from '../types'
import { MergeModal } from '../components/MergeModal'
import { MonthSection } from '../components/MonthSection'
import { CountrySection } from '../components/CountrySection'
import { LoadError, LoadMore } from '../components/LoadMore'
import { useDebounced } from '../hooks/useDebounced'
import { monthKey } from '../lib/monthKey'
import { formatFilingDate } from '../lib/dates'

// Fallback country names must follow the UI language, so the DisplayNames instance
// is built per-locale by the caller rather than pinned at module load.
function countryLabel(
  code: string | undefined,
  countries: Country[],
  intlNames: Intl.DisplayNames,
): string {
  if (!code) return '—'
  const registered = countries.find(c => c.code === code)
  if (registered) return registered.name
  try { return intlNames.of(code) ?? code } catch { return code }
}

/** Search results per request. Smaller than the month pages (500): search is interactive,
 *  so the first screenful should come back fast, and most searches narrow well below it. */
const SEARCH_PAGE = 50

export function CollectionPage() {
  const { t, lang } = useLang()
  const intlNames = useMemo(() => new Intl.DisplayNames([lang], { type: 'region' }), [lang])
  const [q, setQ] = useState('')
  // Every query key and request reads this, never raw `q` (only the input's own `value`
  // does), so typing fires one request after the user pauses rather than one per
  // keystroke. 300 ms is long enough to cover the gap between keystrokes of a normal
  // typist, and short enough that results still feel immediate after the last one.
  // Trimmed before debouncing: a box holding only spaces is not a search, and "Rotary "
  // reuses the cached "Rotary" results instead of issuing a second request.
  const debouncedQ = useDebounced(q.trim(), 300)
  const [view, setView] = useState<'cards' | 'persons'>('cards')
  const [collapsedYears, setCollapsedYears] = useState<Set<number>>(new Set())

  // Browse mode: one small request describing every year/month bucket in the whole
  // collection. The page no longer fetches a fixed slab of cards and groups it in the
  // browser — that slab silently dropped everything past its limit.
  //
  // Status order — every list on this page renders in this order: `isLoadingError`
  // (failed AND no data) → `data === undefined` (still loading) → empty → data.
  // A retry that comes due in a hidden tab, or a dropped connection, PAUSES the query:
  // it stays pending with no data while `isLoading` and `isError` are both false, so an
  // `isLoading` guard falls through to "scan your first card" for a full collection.
  // `isSuccess` fails the other way: `status` stays 'error' after a failed background
  // refetch even though the cached data is still there, so gating on it would swap a
  // loaded tree for a spinner and unmount every expanded month.
  // `data` is taken without an `= []` default so "no data yet" stays distinguishable
  // from "empty".
  const {
    data: facets,
    isLoadingError: facetsLoadError,
    isFetching: facetsFetching,
    refetch: refetchFacets,
  } = useQuery<CardFacet[]>({
    queryKey: ['cards', 'facets'],
    queryFn: () => listCardFacets(),
    enabled: view === 'cards' && !debouncedQ,
  })

  // Search mode: the server matches the term (name, company, phone, …) across the WHOLE
  // collection and pages the results, so no card is unreachable because it fell outside
  // a client-side slab. Both queries run only while a search term is active.
  //
  // The total is declared FIRST, on purpose. getNextPageParam below reads `searchTotal`,
  // and TanStack calls getNextPageParam synchronously inside useInfiniteQuery (to compute
  // `hasNextPage`) on every render once a page is cached. Declared after the infinite
  // query, `searchTotal` would still be in its temporal dead zone at that moment and the
  // render would throw a ReferenceError as soon as the first page arrived.
  const { data: searchTotal } = useQuery<{ total: number }>({
    queryKey: ['cards', 'search-count', debouncedQ],
    // countCardsTotal, NOT countCards: `countCards` from '../api' is the Claude Vision
    // card-count call re-exported from ./sessions — see the note on countCardsTotal.
    queryFn: () => countCardsTotal({ q: debouncedQ }),
    enabled: view === 'cards' && debouncedQ.length > 0,
  })

  // useInfiniteQuery, NOT a useQuery whose key contains the page count. Keying on the
  // page count makes every "Load more" a fresh cache entry: it re-requests every earlier
  // page (n(n+1)/2 requests to reach page n), keeps overlapping copies, and blanks the
  // grid while the new entry is empty. The page count belongs in the cache, not in state.
  // `debouncedQ` is in the key, so each search term owns its pages: page 2 of an old term
  // cannot leak into a new one, and there is no paging state to reset.
  const {
    data: searchData,
    isLoadingError: searchLoadError,
    isFetching: searchFetching,
    isFetchingNextPage: searchFetchingMore,
    isFetchNextPageError: searchMoreError,
    hasNextPage: searchHasMore,
    fetchNextPage: fetchMoreResults,
    refetch: refetchSearch,
  } = useInfiniteQuery({
    queryKey: ['cards', 'search', debouncedQ],
    queryFn: ({ pageParam }) =>
      listCards({ q: debouncedQ, limit: SEARCH_PAGE, offset: pageParam }),
    initialPageParam: 0,
    // A known /count total decides whether more remain; an empty page, or a short page
    // while the total is unknown, ends the list. Each case is explained below.
    getNextPageParam: (last, all) => {
      // An empty page means the list ran out, whatever the total says. Without this, a
      // total that went stale (cards deleted between the two requests) would keep
      // `hasNextPage` true forever and leave a button that fetches nothing.
      if (last.length === 0) return undefined
      const loaded = all.reduce((n, page) => n + page.length, 0)
      // Known total: stop exactly at it. Unknown total (the count query is still in
      // flight, or failed): a full last page means more may exist, so keep offering the
      // next offset. Defaulting the total to 0 here would disable Load more entirely.
      const total = searchTotal?.total
      if (total !== undefined) return loaded < total ? loaded : undefined
      return last.length === SEARCH_PAGE ? loaded : undefined
    },
    enabled: view === 'cards' && debouncedQ.length > 0,
  })
  // No `?? []`: `undefined` means "no page yet" (in flight or paused), which the render
  // must keep distinct from "zero results". Typed `CardListItem[] | undefined`.
  const searchResults = searchData?.pages.flat()

  // Persons tab: one small request describing every country group and its size. The
  // people themselves are fetched per group by CountrySection, only once a group is
  // open — see the note there on why one shared list could not be paginated.
  // `debouncedQ` narrows the groups on the server, so a search shows only the countries
  // that hold a match. Status order as described on the card facets query above.
  const {
    data: personFacets,
    isLoadingError: personFacetsLoadError,
    isFetching: personFacetsFetching,
    refetch: refetchPersonFacets,
  } = useQuery<PersonFacet[]>({
    queryKey: ['persons', 'facets', debouncedQ],
    queryFn: () => listPersonFacets(debouncedQ || undefined),
    enabled: view === 'persons',
  })

  const { data: countries = [] } = useQuery<Country[]>({
    queryKey: ['countries'],
    queryFn: listCountries,
    enabled: view === 'persons',
  })

  // The three most recent months open on load. Facets arrive newest-first, so those are
  // simply the first three entries — no date arithmetic, and no assumption that "recent"
  // means the current calendar month.
  // Keys go through the shared monthKey() so they cannot drift from the format the
  // MonthSection keys below are built with.
  const eagerMonths = useMemo(
    () => new Set((facets ?? []).slice(0, 3).map(f => monthKey(f.year, f.month))),
    [facets],
  )

  // Facets → year groups for the tree. Both levels stay newest-first.
  const facetsByYear = useMemo(() => {
    const years = new Map<number, CardFacet[]>()
    for (const f of facets ?? []) {
      if (!years.has(f.year)) years.set(f.year, [])
      years.get(f.year)!.push(f)
    }
    return [...years.entries()].sort((a, b) => b[0] - a[0])
  }, [facets])

  // Which country group opens on load. Not searching: only the biggest group (the
  // first one on a tie), so the page costs one group's request, not all of them.
  // Searching: every group with a match, since a narrowed search is what the user
  // wants to see at once. The sections are keyed on the search term below, so these
  // defaults re-apply to each new search instead of sticking from the previous one.
  const largestFacet = useMemo(() => {
    let best: PersonFacet | undefined
    for (const f of personFacets ?? []) if (!best || f.count > best.count) best = f
    return best
  }, [personFacets])

  const [selectMode, setSelectMode] = useState(false)
  // A map from external_id to the person, not a set of ids. People now live inside
  // per-group queries, so the page has no single list to look selected ids up in; a
  // selected person must stay mergeable after their group collapses (its rows unmount)
  // or a different search hides them. Storing the row itself keeps MergeModal fed.
  const [selected, setSelected] = useState<Map<string, PersonListItem>>(new Map())
  const [showMergeModal, setShowMergeModal] = useState(false)

  const toggleSelect = (p: PersonListItem) =>
    setSelected(prev => {
      const next = new Map(prev)
      if (next.has(p.external_id)) next.delete(p.external_id)
      else next.set(p.external_id, p)
      return next
    })

  const exitSelectMode = () => {
    setSelectMode(false)
    setSelected(new Map())
    setShowMergeModal(false)
  }

  const selectedPersons = useMemo(() => [...selected.values()], [selected])

  const toggleYear = (year: number) =>
    setCollapsedYears(prev => {
      const next = new Set(prev)
      if (next.has(year)) next.delete(year)
      else next.add(year)
      return next
    })

  return (
    <div className="max-w-4xl mx-auto py-6 px-4 space-y-4">
      <div className="flex items-center gap-3">
        <h1 className="text-lg font-semibold text-gray-900 flex-1">{t.collectionTitle}</h1>
        <a href="/scan?manual=1" className="btn-secondary text-sm">{t.enterManuallyBtn}</a>
        <a href="/scan" className="btn-primary text-sm">{t.newScanBtn}</a>
      </div>

      {/* Tabs + search */}
      <div className="flex items-center gap-2">
        <div className="flex rounded-lg border border-gray-200 overflow-hidden text-sm">
          <button
            onClick={() => { setView('cards'); exitSelectMode() }}
            className={`px-3 py-1.5 ${view === 'cards' ? 'bg-blue-600 text-white' : 'text-gray-600 hover:bg-gray-50'}`}
          >
            {t.tabCards}
          </button>
          <button
            onClick={() => setView('persons')}
            className={`px-3 py-1.5 ${view === 'persons' ? 'bg-blue-600 text-white' : 'text-gray-600 hover:bg-gray-50'}`}
          >
            {t.tabPersons}
          </button>
        </div>
        <input
          type="search"
          // Persons search matches names/organisations only (never occasions), so its
          // placeholder is narrower than the Cards tab's — see CollectionPage's `view` state.
          placeholder={view === 'persons' ? t.searchPlaceholderPersons : t.searchPlaceholder}
          value={q}
          onChange={e => setQ(e.target.value)}
          className="flex-1 border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        {view === 'persons' && !selectMode && (
          <button
            onClick={() => setSelectMode(true)}
            className="text-sm px-3 py-1 rounded border border-gray-300 text-gray-600 hover:bg-gray-50 transition-colors"
          >
            {t.selectPersonsBtn}
          </button>
        )}
        {view === 'persons' && selectMode && (
          <button
            onClick={exitSelectMode}
            className="text-sm px-3 py-1 rounded border border-gray-300 text-gray-600 hover:bg-gray-50 transition-colors"
          >
            {t.cancelSelectBtn}
          </button>
        )}
        <a
          href="/export"
          className="text-sm px-3 py-1 rounded border border-gray-300 text-gray-600 hover:bg-gray-50 transition-colors"
        >
          {t.exportBtn}
        </a>
      </div>

      {/* Cards — search results while a query is active, otherwise the browse tree */}
      {view === 'cards' && (
        debouncedQ ? (
          // Search mode: a flat grid of server-side results, no year/month tree.
          // Status order as described on the facets query: a failed request is not
          // "no results", and neither is one that is still pending or paused.
          searchLoadError ? (
            // The first page failed and nothing is cached. Retrying re-runs the query.
            <LoadError onRetry={() => refetchSearch()} isRetrying={searchFetching} />
          ) : searchResults === undefined ? (
            // No page has arrived yet — whether the request is in flight or paused.
            <div className="text-center text-gray-400 py-12">{t.loading}</div>
          ) : searchResults.length === 0 ? (
            <p className="text-center text-sm text-gray-400 py-12">{t.noResults(debouncedQ)}</p>
          ) : (
            <div className="space-y-2">
              {/* Only a known total is printed. Falling back to the loaded count would
                  read "50 results" while more exist, whenever /count is slow or failed. */}
              {searchTotal?.total !== undefined && (
                <p className="text-xs text-gray-400">{t.resultsN(searchTotal.total)}</p>
              )}
              <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
                {searchResults.map(card => (
                  <CardThumbnail key={card.id} card={card} />
                ))}
              </div>
              {/* Gated on `hasNextPage`, which getNextPageParam derives from both cases:
                  a known total, or (unknown total) whether the last page was full.
                  LoadMore alone keeps its button whenever the total is unknown, so after
                  a short last page it would offer a button that loads nothing.
                  A failed next page leaves `hasNextPage` true, so the error branch is
                  reachable: it keeps the rows and retries just the missing page. */}
              {searchHasMore && (searchMoreError ? (
                <LoadError onRetry={() => fetchMoreResults()} isRetrying={searchFetchingMore} />
              ) : (
                <LoadMore
                  loaded={searchResults.length}
                  total={searchTotal?.total}
                  // Only a next-page fetch makes the button busy. `isFetching` is also
                  // true during a background refetch of page one, which is not "loading more".
                  isLoading={searchFetchingMore}
                  onLoadMore={() => fetchMoreResults()}
                />
              ))}
            </div>
          )
        ) : (
          // Browse mode: the whole collection as a year → month tree. Counts come from
          // the facets response, so every card in the database is reachable even though
          // only the expanded months are fetched.
          // Status order — see the note on the facets query above.
          facetsLoadError ? (
            <LoadError onRetry={() => refetchFacets()} isRetrying={facetsFetching} />
          ) : facets === undefined ? (
            <div className="text-center text-gray-400 py-12">{t.loading}</div>
          ) : facets.length === 0 ? (
            <EmptyState />
          ) : (
            <div className="space-y-2">
              {facetsByYear.map(([year, months]) => {
                const yearCollapsed = collapsedYears.has(year)
                const yearCount = months.reduce((n, f) => n + f.count, 0)
                return (
                  <div key={year}>
                    <button
                      className="w-full flex items-center gap-2 text-sm font-semibold text-gray-700 py-1 hover:text-blue-600 text-left"
                      onClick={() => toggleYear(year)}
                      aria-expanded={!yearCollapsed}
                    >
                      <span className="text-xs text-gray-400" aria-hidden="true">{yearCollapsed ? '▶' : '▼'}</span>
                      <span>{year}</span>
                      <span className="text-xs text-gray-400">({yearCount})</span>
                    </button>

                    {/* Hidden, not unmounted: each MonthSection owns its own `expanded`
                        state, which unmounting would throw away. Collapsing a year and
                        reopening it must bring every month back as the user left it. */}
                    <div className={yearCollapsed ? 'hidden' : ''}>
                      {months.map(f => (
                        <MonthSection
                          key={monthKey(f.year, f.month)}
                          year={f.year}
                          month={f.month}
                          count={f.count}
                          defaultExpanded={eagerMonths.has(monthKey(f.year, f.month))}
                          renderCard={card => <CardThumbnail key={card.id} card={card} />}
                        />
                      ))}
                    </div>
                  </div>
                )
              })}
            </div>
          )
        )
      )}

      {/* Persons grouped by country — one lazily loaded CountrySection per facet */}
      {view === 'persons' && (
        // Status order — see the note on the card facets query: a failed, pending or
        // paused request must not render EmptyState.
        personFacetsLoadError ? (
          <LoadError onRetry={() => refetchPersonFacets()} isRetrying={personFacetsFetching} />
        ) : personFacets === undefined ? (
          <div className="text-center text-gray-400 py-12">{t.loading}</div>
        ) : personFacets.length === 0 ? (
          debouncedQ ? (
            <p className="text-center text-sm text-gray-400 py-12">{t.noPersonsMatch(debouncedQ)}</p>
          ) : (
            <EmptyState />
          )
        ) : (
          <div className="space-y-2">
            {/* Facet order is the display order (codes ascending, no country last); the
                server already sorts it, so there is nothing to re-sort here. */}
            {personFacets.map(f => (
              <CountrySection
                // The search term is part of the key so a new search remounts every
                // section: each one's `expanded` state is re-initialised from
                // defaultExpanded, instead of carrying over from the previous search.
                key={`${f.country_code ?? 'none'}|${debouncedQ}`}
                code={f.country_code}
                label={f.country_code ? countryLabel(f.country_code, countries, intlNames) : t.unknownCountry}
                count={f.count}
                defaultExpanded={debouncedQ ? true : f === largestFacet}
                q={debouncedQ}
                selectMode={selectMode}
                selected={selected}
                onToggleSelect={toggleSelect}
              />
            ))}
          </div>
        )
      )}
      {/* Floating action bar — visible in select mode with ≥2 selected */}
      {selectMode && selected.size >= 2 && (
        <div className="fixed bottom-[max(1.5rem,env(safe-area-inset-bottom))] left-1/2 -translate-x-1/2 flex items-center gap-3 bg-white border border-gray-200 rounded-2xl shadow-xl px-5 py-3 z-40">
          <span className="text-sm text-gray-600">{t.selectedN(selected.size)}</span>
          <button
            onClick={() => setShowMergeModal(true)}
            className="btn-primary text-sm"
          >
            {t.mergeSelectedBtn(selected.size)}
          </button>
        </div>
      )}

      {/* Merge modal */}
      {showMergeModal && selectedPersons.length >= 2 && (
        <MergeModal
          selected={selectedPersons}
          onClose={() => {
            setShowMergeModal(false)
            exitSelectMode()
          }}
        />
      )}
    </div>
  )
}

function CardThumbnail({ card }: { card: CardListItem }) {
  const { t, lang } = useLang()
  return (
    <a
      href={`/cards/${card.external_id}`}
      className="block rounded-xl border border-gray-200 bg-white shadow-sm hover:shadow-md hover:border-blue-300 transition-all overflow-hidden group"
    >
      {card.front_image_path ? (
        <img
          src={`/api/v2/images/${card.front_image_path}`}
          alt={card.person_name ?? t.noName}
          className="w-full h-28 object-cover object-center group-hover:scale-105 transition-transform duration-200"
        />
      ) : (
        <div className="w-full h-28 bg-gray-100 flex items-center justify-center">
          <span className="text-3xl text-gray-300">🪪</span>
        </div>
      )}
      <div className="p-2">
        <p className="text-xs font-medium text-gray-800 truncate">{card.person_name ?? t.noName}</p>
        {/* Received date, falling back to scan date — one formatter for both branches. */}
        <p className="text-xs text-gray-400">{formatFilingDate(card, lang)}</p>
      </div>
    </a>
  )
}

function EmptyState() {
  const { t } = useLang()
  return (
    <div className="text-center py-16 text-gray-400 space-y-3">
      <div className="text-5xl">🪪</div>
      <p className="text-sm">{t.emptyMessage}</p>
      <a href="/scan" className="btn-primary text-sm inline-block">{t.emptyCta}</a>
    </div>
  )
}
