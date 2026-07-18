import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { listCards, listPersons, listCountries } from '../api'
import { useLang } from '../LangContext'
import type { CardListItem, Country, PersonListItem } from '../types'
import { MergeModal } from '../components/MergeModal'

const _intlNames = new Intl.DisplayNames(['ja', 'en'], { type: 'region' })

function countryLabel(code: string | undefined, countries: Country[]): string {
  if (!code) return '—'
  const registered = countries.find(c => c.code === code)
  if (registered) return registered.name
  try { return _intlNames.of(code) ?? code } catch { return code }
}

export function CollectionPage() {
  const { t } = useLang()
  const [q, setQ] = useState('')
  const [view, setView] = useState<'cards' | 'persons'>('cards')
  const [collapsedYears, setCollapsedYears] = useState<Set<number>>(new Set())
  const [collapsedMonths, setCollapsedMonths] = useState<Set<string>>(new Set())
  const [collapsedCountries, setCollapsedCountries] = useState<Set<string>>(new Set())

  const { data: cards = [], isLoading: cardsLoading } = useQuery<CardListItem[]>({
    queryKey: ['cards'],
    queryFn: () => listCards({ limit: 200 }),
    enabled: view === 'cards',
  })

  const { data: persons = [], isLoading: personsLoading } = useQuery<PersonListItem[]>({
    queryKey: ['persons', q],
    queryFn: () => listPersons(q || undefined),
    enabled: view === 'persons',
  })

  const { data: countries = [] } = useQuery<Country[]>({
    queryKey: ['countries'],
    queryFn: listCountries,
    enabled: view === 'persons',
  })

  // Cross-search: when searching cards, also fetch persons matching q to find cards by person
  const { data: searchPersons = [] } = useQuery<PersonListItem[]>({
    queryKey: ['persons-search', q],
    queryFn: () => listPersons(q),
    enabled: view === 'cards' && q.length > 0,
  })

  const matchingPersonIds = useMemo(
    () => new Set(searchPersons.map(p => p.id)),
    [searchPersons],
  )

  const filteredCards = useMemo(() => {
    if (!q) return cards
    return cards.filter(
      c =>
        c.person_name?.toLowerCase().includes(q.toLowerCase()) ||
        matchingPersonIds.has(c.person_id),
    )
  }, [cards, q, matchingPersonIds])

  // Group cards by year (desc) → month (desc) → cards sorted by date desc
  const cardsByYearMonth = useMemo(() => {
    const getDate = (c: CardListItem) => new Date(c.received_date ?? c.created_at)
    const sorted = [...filteredCards].sort((a, b) => getDate(b).getTime() - getDate(a).getTime())

    const yearMap = new Map<number, Map<number, CardListItem[]>>()
    for (const card of sorted) {
      const d = getDate(card)
      const y = d.getFullYear()
      const m = d.getMonth() + 1
      if (!yearMap.has(y)) yearMap.set(y, new Map())
      const monthMap = yearMap.get(y)!
      if (!monthMap.has(m)) monthMap.set(m, [])
      monthMap.get(m)!.push(card)
    }

    return [...yearMap.entries()]
      .sort(([a], [b]) => b - a)
      .map(([year, monthMap]) => ({
        year,
        months: [...monthMap.entries()]
          .sort(([a], [b]) => b - a)
          .map(([month, cards]) => ({ year, month, cards })),
      }))
  }, [filteredCards])

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

  const toggleMonth = (key: string) =>
    setCollapsedMonths(prev => {
      const next = new Set(prev)
      next.has(key) ? next.delete(key) : next.add(key)
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

      {/* Cards grouped by year/month */}
      {view === 'cards' && (
        cardsLoading ? (
          <div className="text-center text-gray-400 py-12">{t.loading}</div>
        ) : filteredCards.length === 0 ? (
          <EmptyState />
        ) : (
          <div className="space-y-2">
            {cardsByYearMonth.map(({ year, months }) => {
              const yearCollapsed = collapsedYears.has(year)
              return (
                <div key={year}>
                  <button
                    className="w-full flex items-center gap-2 text-sm font-semibold text-gray-700 py-1 hover:text-blue-600 text-left"
                    onClick={() => toggleYear(year)}
                  >
                    <span className="text-xs text-gray-400">{yearCollapsed ? '▶' : '▼'}</span>
                    <span>{year}</span>
                  </button>

                  {!yearCollapsed && months.map(({ month, cards: monthCards }) => {
                    const monthKey = `${year}-${month}`
                    const monthCollapsed = collapsedMonths.has(monthKey)
                    return (
                      <div key={monthKey} className="ml-4 mb-2">
                        <button
                          className="flex items-center gap-2 text-xs font-medium text-gray-500 py-0.5 hover:text-blue-500"
                          onClick={() => toggleMonth(monthKey)}
                        >
                          <span className="text-gray-400">{monthCollapsed ? '▶' : '▼'}</span>
                          <span>{year}/{String(month).padStart(2, '0')}</span>
                          <span className="text-gray-400">({monthCards.length})</span>
                        </button>

                        {!monthCollapsed && (
                          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3 mt-2">
                            {monthCards.map(card => (
                              <CardThumbnail key={card.id} card={card} />
                            ))}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              )
            })}
          </div>
        )
      )}

      {/* Persons grouped by country */}
      {view === 'persons' && (
        personsLoading ? (
          <div className="text-center text-gray-400 py-12">{t.loading}</div>
        ) : persons.length === 0 ? (
          <EmptyState />
        ) : (
          <div className="space-y-2">
            {personsByCountry.map(({ code, persons: group }) => {
              const collapsed = collapsedCountries.has(code)
              const label = code ? countryLabel(code, countries) : t.unknownCountry ?? 'Unknown'
              return (
                <div key={code || '__none__'}>
                  <button
                    className="w-full flex items-center gap-2 text-sm font-semibold text-gray-700 py-1 hover:text-blue-600 text-left"
                    onClick={() => toggleCountry(code)}
                  >
                    <span className="text-xs text-gray-400">{collapsed ? '▶' : '▼'}</span>
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
