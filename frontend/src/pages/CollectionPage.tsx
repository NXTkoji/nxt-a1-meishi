import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { listCardFacets, listCards, listPersons, listCountries } from '../api'
import { useLang } from '../LangContext'
import type { CardFacet, CardListItem, Country, PersonListItem } from '../types'
import { MergeModal } from '../components/MergeModal'
import { MonthSection } from '../components/MonthSection'
import { LoadError } from '../components/LoadMore'
import { monthKey } from '../lib/monthKey'

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

export function CollectionPage() {
  const { t, lang } = useLang()
  const intlNames = useMemo(() => new Intl.DisplayNames([lang], { type: 'region' }), [lang])
  const [q, setQ] = useState('')
  // Stand-in for the debounced search term that Task 7 introduces. Defined here so the
  // browse/search switch below is already written against the value it will use.
  const debouncedQ = q
  const [view, setView] = useState<'cards' | 'persons'>('cards')
  const [collapsedYears, setCollapsedYears] = useState<Set<number>>(new Set())
  const [collapsedCountries, setCollapsedCountries] = useState<Set<string>>(new Set())

  // Browse mode: one small request describing every year/month bucket in the whole
  // collection. The page no longer fetches a fixed slab of cards and groups it in the
  // browser — that slab silently dropped everything past its limit.
  // `isError` matters as much as `data`: a failed request leaves `facets` at its `[]`
  // default, which would otherwise fall through to the "scan your first card" empty
  // state and tell a user with hundreds of cards that their collection is empty.
  const {
    data: facets = [],
    isLoading: facetsLoading,
    isError: facetsError,
    isFetching: facetsFetching,
    refetch: refetchFacets,
  } = useQuery<CardFacet[]>({
    queryKey: ['cards', 'facets'],
    queryFn: () => listCardFacets(),
    enabled: view === 'cards' && !debouncedQ,
  })

  // Search mode: still the old client-side filter over one page of cards. Task 7
  // replaces this with a server-side search; until then it stays working so the app is
  // never broken between commits. Only fetched while a search is active.
  const {
    data: cards = [],
    isLoading: cardsLoading,
    isError: cardsError,
    isFetching: cardsFetching,
    refetch: refetchCards,
  } = useQuery<CardListItem[]>({
    queryKey: ['cards'],
    queryFn: () => listCards({ limit: 200 }),
    enabled: view === 'cards' && !!debouncedQ,
  })

  // Every query key and request below reads `debouncedQ`, never raw `q`, so that when
  // Task 7 makes the debounce real, no request fires per keystroke. (Only the search
  // input's own `value` uses `q`.)
  const {
    data: persons = [],
    isLoading: personsLoading,
    isError: personsError,
    isFetching: personsFetching,
    refetch: refetchPersons,
  } = useQuery<PersonListItem[]>({
    queryKey: ['persons', debouncedQ],
    // 500 is the endpoint's cap; passing it explicitly is load-bearing, since
    // listPersons now defaults to the API's own default of 50. Task 8 gives this list
    // a real pager.
    queryFn: () => listPersons(debouncedQ || undefined, 500),
    enabled: view === 'persons',
  })

  const { data: countries = [] } = useQuery<Country[]>({
    queryKey: ['countries'],
    queryFn: listCountries,
    enabled: view === 'persons',
  })

  // Cross-search: when searching cards, also fetch persons matching q to find cards by person
  // It feeds matchingPersonIds → filteredCards, so it must follow the same debounced
  // term as the card list; reading raw `q` here would defeat the debounce for the
  // expensive half of the search.
  const {
    data: searchPersons = [],
    isError: searchPersonsError,
    isFetching: searchPersonsFetching,
    refetch: refetchSearchPersons,
  } = useQuery<PersonListItem[]>({
    queryKey: ['persons-search', debouncedQ],
    // Explicit 500 for the same reason as above: a person matched only by organisation
    // name must not fall off the Cards tab because the default page size hid them.
    queryFn: () => listPersons(debouncedQ, 500),
    enabled: view === 'cards' && debouncedQ.length > 0,
  })

  // Search results are built from BOTH the card list and the cross-search, so either
  // one failing makes the results incomplete. Treat that as an error, not as
  // "no results" (or as a silently shorter list).
  const searchError = cardsError || searchPersonsError
  const retrySearch = () => {
    if (cardsError) refetchCards()
    if (searchPersonsError) refetchSearchPersons()
  }

  const matchingPersonIds = useMemo(
    () => new Set(searchPersons.map(p => p.id)),
    [searchPersons],
  )

  const filteredCards = useMemo(() => {
    if (!debouncedQ) return cards
    return cards.filter(
      c =>
        c.person_name?.toLowerCase().includes(debouncedQ.toLowerCase()) ||
        matchingPersonIds.has(c.person_id),
    )
  }, [cards, debouncedQ, matchingPersonIds])

  // The three most recent months open on load. Facets arrive newest-first, so those are
  // simply the first three entries — no date arithmetic, and no assumption that "recent"
  // means the current calendar month.
  // Keys go through the shared monthKey() so they cannot drift from the format the
  // MonthSection keys below are built with.
  const eagerMonths = useMemo(
    () => new Set(facets.slice(0, 3).map(f => monthKey(f.year, f.month))),
    [facets],
  )

  // Facets → year groups for the tree. Both levels stay newest-first.
  const facetsByYear = useMemo(() => {
    const years = new Map<number, CardFacet[]>()
    for (const f of facets) {
      if (!years.has(f.year)) years.set(f.year, [])
      years.get(f.year)!.push(f)
    }
    return [...years.entries()].sort((a, b) => b[0] - a[0])
  }, [facets])

  // Group persons by country_code (home first, then work), sorted by family name within
  const personsByCountry = useMemo(() => {
    const sorted = [...persons].sort((a, b) => {
      const fa = (a.family_name ?? a.primary_name ?? '').toLowerCase()
      const fb = (b.family_name ?? b.primary_name ?? '').toLowerCase()
      return fa.localeCompare(fb)
    })
    const countryMap = new Map<string, PersonListItem[]>()
    for (const p of sorted) {
      const key = p.country_code ?? ''
      if (!countryMap.has(key)) countryMap.set(key, [])
      countryMap.get(key)!.push(p)
    }
    return [...countryMap.entries()]
      .sort(([a], [b]) => {
        if (!a) return 1   // unknown last
        if (!b) return -1
        return a.localeCompare(b)
      })
      .map(([code, persons]) => ({ code, persons }))
  }, [persons])

  const [selectMode, setSelectMode] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [showMergeModal, setShowMergeModal] = useState(false)

  const toggleSelect = (extId: string) =>
    setSelectedIds(prev => {
      const next = new Set(prev)
      next.has(extId) ? next.delete(extId) : next.add(extId)
      return next
    })

  const exitSelectMode = () => {
    setSelectMode(false)
    setSelectedIds(new Set())
    setShowMergeModal(false)
  }

  const selectedPersons = useMemo(
    () => persons.filter(p => selectedIds.has(p.external_id)),
    [persons, selectedIds],
  )

  const toggleYear = (year: number) =>
    setCollapsedYears(prev => {
      const next = new Set(prev)
      next.has(year) ? next.delete(year) : next.add(year)
      return next
    })

  const toggleCountry = (key: string) =>
    setCollapsedCountries(prev => {
      const next = new Set(prev)
      next.has(key) ? next.delete(key) : next.add(key)
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
          placeholder={t.searchPlaceholder}
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
          // Search mode (temporary shape; Task 7 makes this a server-side search).
          cardsLoading ? (
            <div className="text-center text-gray-400 py-12">{t.loading}</div>
          ) : searchError ? (
            // Checked before the empty check: a failed request is not "no results".
            <LoadError
              onRetry={retrySearch}
              isRetrying={cardsFetching || searchPersonsFetching}
            />
          ) : filteredCards.length === 0 ? (
            <p className="text-center text-sm text-gray-400 py-12">{t.noResults(debouncedQ)}</p>
          ) : (
            <div className="space-y-2">
              <p className="text-xs text-gray-400">{t.resultsN(filteredCards.length)}</p>
              <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
                {filteredCards.map(card => (
                  <CardThumbnail key={card.id} card={card} />
                ))}
              </div>
            </div>
          )
        ) : (
          // Browse mode: the whole collection as a year → month tree. Counts come from
          // the facets response, so every card in the database is reachable even though
          // only the expanded months are fetched.
          facetsLoading ? (
            <div className="text-center text-gray-400 py-12">{t.loading}</div>
          ) : facetsError ? (
            // Must come before the `facets.length === 0` check — see the query above.
            <LoadError onRetry={() => refetchFacets()} isRetrying={facetsFetching} />
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

      {/* Persons grouped by country */}
      {view === 'persons' && (
        personsLoading ? (
          <div className="text-center text-gray-400 py-12">{t.loading}</div>
        ) : personsError ? (
          // Same rule as the facets tree: a failed request must not render EmptyState.
          <LoadError onRetry={() => refetchPersons()} isRetrying={personsFetching} />
        ) : persons.length === 0 ? (
          <EmptyState />
        ) : (
          <div className="space-y-2">
            {personsByCountry.map(({ code, persons: group }) => {
              const collapsed = collapsedCountries.has(code)
              const label = code ? countryLabel(code, countries, intlNames) : t.unknownCountry ?? 'Unknown'
              return (
                <div key={code || '__none__'}>
                  <button
                    className="w-full flex items-center gap-2 text-sm font-semibold text-gray-700 py-1 hover:text-blue-600 text-left"
                    onClick={() => toggleCountry(code)}
                    aria-expanded={!collapsed}
                  >
                    <span className="text-xs text-gray-400" aria-hidden="true">{collapsed ? '▶' : '▼'}</span>
                    <span>{label}</span>
                    <span className="text-xs text-gray-400 font-normal">({group.length})</span>
                  </button>
                  {!collapsed && (
                    <div className="ml-4 divide-y divide-gray-100 rounded-xl border border-gray-200 bg-white">
                      {group.map(p => {
                        const isSelected = selectedIds.has(p.external_id)
                        if (selectMode) {
                          return (
                            <div
                              key={p.id}
                              onClick={() => toggleSelect(p.external_id)}
                              className={`flex items-center gap-3 px-4 py-3 cursor-pointer transition-colors ${isSelected ? 'bg-blue-50' : 'hover:bg-gray-50'}`}
                            >
                              <input
                                type="checkbox"
                                checked={isSelected}
                                onChange={() => toggleSelect(p.external_id)}
                                onClick={e => e.stopPropagation()}
                                className="accent-blue-600 w-4 h-4 shrink-0"
                              />
                              <div className="w-8 h-8 rounded-full bg-blue-100 text-blue-700 flex items-center justify-center text-sm font-medium shrink-0">
                                {(p.primary_name ?? '?').charAt(0)}
                              </div>
                              <div>
                                <p className="text-sm font-medium text-gray-900">{p.primary_name ?? t.noName}</p>
                                <p className="text-xs text-gray-400">{new Date(p.created_at).toLocaleDateString()}</p>
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
                              <p className="text-xs text-gray-400">{new Date(p.created_at).toLocaleDateString()}</p>
                            </div>
                          </a>
                        )
                      })}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )
      )}
      {/* Floating action bar — visible in select mode with ≥2 selected */}
      {selectMode && selectedIds.size >= 2 && (
        <div className="fixed bottom-[max(1.5rem,env(safe-area-inset-bottom))] left-1/2 -translate-x-1/2 flex items-center gap-3 bg-white border border-gray-200 rounded-2xl shadow-xl px-5 py-3 z-40">
          <span className="text-sm text-gray-600">{t.selectedN(selectedIds.size)}</span>
          <button
            onClick={() => setShowMergeModal(true)}
            className="btn-primary text-sm"
          >
            {t.mergeSelectedBtn(selectedIds.size)}
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
  const { t } = useLang()
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
        <p className="text-xs text-gray-400">{card.received_date ?? new Date(card.created_at).toLocaleDateString()}</p>
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
