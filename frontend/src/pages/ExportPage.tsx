/**
 * ExportPage — two-step export flow.
 *
 * Step 1 (selection): Filter cards, select which ones to export.
 * Step 2 (destinations): Choose destinations, run export, view results.
 *
 * This is a full page — not a modal. Route: /export
 */
import { useState, useMemo, useRef, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { listCards, listMyCompanies, listOccasions } from '../api'
import { useLang } from '../LangContext'
import { ExportDestinationSelector } from '../components/ExportDestinationSelector'
import type { CardListItem, MyCompany, Occasion } from '../types'

type Step = 'select' | 'destinations'

const DEST_BADGE_LABELS: Record<string, string> = {
  odoo: 'Odoo',
  google_contacts: 'Google',
}

export function ExportPage() {
  const { t, lang } = useLang()
  const [step, setStep] = useState<Step>('select')
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())

  // Filters
  const [q, setQ] = useState('')
  const [yearFilter, setYearFilter] = useState<number | undefined>()
  const [monthFilter, setMonthFilter] = useState<string | undefined>()
  const [dateFilter, setDateFilter] = useState<string | undefined>()
  const [occasionFilter, setOccasionFilter] = useState<number | undefined>()
  const [myCompanyFilter, setMyCompanyFilter] = useState<number | undefined>()
  const [notExported, setNotExported] = useState(false)
  const autoSelectPending = useRef(false)

  const queryParams = useMemo(() => ({
    q: q || undefined,
    year: yearFilter,
    month: monthFilter,
    date: dateFilter,
    occasion_id: occasionFilter,
    my_company_id: myCompanyFilter,
    not_exported: notExported || undefined,
    limit: 500,
  }), [q, yearFilter, monthFilter, dateFilter, occasionFilter, myCompanyFilter, notExported])

  // Filter change → auto-select if any filter is active, otherwise clear selection
  useEffect(() => {
    const hasFilter = !!(queryParams.q || queryParams.year || queryParams.month ||
                         queryParams.date || queryParams.occasion_id || queryParams.my_company_id || queryParams.not_exported)
    if (hasFilter) {
      autoSelectPending.current = true
    } else {
      setSelectedIds(new Set())
    }
  }, [queryParams])

  const { data: cards = [], isLoading } = useQuery<CardListItem[]>({
    queryKey: ['export-cards', queryParams],
    queryFn: () => listCards(queryParams),
  })

  useEffect(() => {
    if (autoSelectPending.current && !isLoading) {
      setSelectedIds(new Set(cards.map(c => c.external_id)))
      autoSelectPending.current = false
    }
  }, [cards, isLoading])

  const { data: occasions = [] } = useQuery<Occasion[]>({
    queryKey: ['occasions'],
    queryFn: listOccasions,
  })

  const { data: myCompanies = [] } = useQuery<MyCompany[]>({
    queryKey: ['my-companies'],
    queryFn: listMyCompanies,
  })

  // Active filter chips
  type Chip = { label: string; clear: () => void }
  const chips = useMemo<Chip[]>(() => {
    const out: Chip[] = []
    if (yearFilter) out.push({ label: `${t.exportFilterYear}: ${yearFilter}`, clear: () => setYearFilter(undefined) })
    if (monthFilter) out.push({ label: `${t.exportFilterMonth}: ${monthFilter}`, clear: () => setMonthFilter(undefined) })
    if (dateFilter) out.push({ label: `${t.exportFilterDate}: ${dateFilter}`, clear: () => setDateFilter(undefined) })
    if (occasionFilter) {
      const occ = occasions.find(o => o.id === occasionFilter)
      out.push({ label: occ?.name ?? `Occasion #${occasionFilter}`, clear: () => setOccasionFilter(undefined) })
    }
    if (myCompanyFilter) {
      const mc = myCompanies.find(m => m.id === myCompanyFilter)
      out.push({ label: `${t.exportFilterMetAs}: ${mc?.name ?? myCompanyFilter}`, clear: () => setMyCompanyFilter(undefined) })
    }
    if (notExported) out.push({ label: t.exportFilterNotExported, clear: () => setNotExported(false) })
    return out
  }, [yearFilter, monthFilter, dateFilter, occasionFilter, myCompanyFilter, notExported, occasions, myCompanies, lang])

  const toggleCard = (extId: string) =>
    setSelectedIds(prev => {
      const next = new Set(prev)
      next.has(extId) ? next.delete(extId) : next.add(extId)
      return next
    })

  const selectAll = () => setSelectedIds(new Set(cards.map(c => c.external_id)))
  const deselectAll = () => setSelectedIds(new Set())

  // Only count/export cards that are both selected and currently visible
  const visibleSelectedIds = useMemo(
    () => cards.filter(c => selectedIds.has(c.external_id)).map(c => c.external_id),
    [cards, selectedIds],
  )

  const currentYear = new Date().getFullYear()
  const yearOptions = Array.from({ length: 5 }, (_, i) => currentYear - i)

  if (step === 'destinations') {
    return (
      <ExportDestinationSelector
        cardExternalIds={visibleSelectedIds}
        onBack={() => setStep('select')}
        onDone={() => { window.location.href = '/collection' }}
      />
    )
  }

  return (
    <div className="max-w-4xl mx-auto py-6 px-4 space-y-4">
      <h1 className="text-lg font-semibold text-gray-900">{t.exportTitle}</h1>

      {/* Search bar */}
      <input
        type="search"
        value={q}
        onChange={e => setQ(e.target.value)}
        placeholder={t.exportSearchPlaceholder}
        className="w-full border border-gray-300 rounded-lg px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
      />

      {/* Filter controls */}
      <div className="flex flex-wrap gap-2 items-center">
        {/* Year picker */}
        <select
          value={yearFilter ?? ''}
          onChange={e => setYearFilter(e.target.value ? Number(e.target.value) : undefined)}
          className="border border-gray-300 rounded px-2 py-1 text-xs"
        >
          <option value="">{t.exportFilterYear}</option>
          {yearOptions.map(y => <option key={y} value={y}>{y}</option>)}
        </select>

        {/* Month picker */}
        <input
          type="month"
          value={monthFilter ?? ''}
          onChange={e => setMonthFilter(e.target.value || undefined)}
          className="border border-gray-300 rounded px-2 py-1 text-xs"
          placeholder={t.exportFilterMonth}
        />

        {/* Date picker */}
        <input
          type="date"
          value={dateFilter ?? ''}
          onChange={e => setDateFilter(e.target.value || undefined)}
          className="border border-gray-300 rounded px-2 py-1 text-xs"
          placeholder={t.exportFilterDate}
        />

        {/* Occasion picker */}
        <select
          value={occasionFilter ?? ''}
          onChange={e => setOccasionFilter(e.target.value ? Number(e.target.value) : undefined)}
          className="border border-gray-300 rounded px-2 py-1 text-xs"
        >
          <option value="">{t.exportFilterOccasion}</option>
          {occasions.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}
        </select>

        {/* Met As picker */}
        {myCompanies.length > 0 && (
          <select
            value={myCompanyFilter ?? ''}
            onChange={e => setMyCompanyFilter(e.target.value ? Number(e.target.value) : undefined)}
            className="border border-gray-300 rounded px-2 py-1 text-xs"
          >
            <option value="">{t.exportFilterMetAs}</option>
            {myCompanies.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
          </select>
        )}

        {/* Not-exported toggle */}
        <label className="flex items-center gap-1.5 text-xs text-gray-600 cursor-pointer">
          <input
            type="checkbox"
            checked={notExported}
            onChange={e => setNotExported(e.target.checked)}
            className="rounded border-gray-300"
          />
          {t.exportFilterNotExported}
        </label>
      </div>

      {/* Active filter chips */}
      {chips.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {chips.map((chip) => (
            <span
              key={chip.label}
              className="inline-flex items-center gap-1 bg-blue-100 text-blue-700 text-xs px-2 py-0.5 rounded-full"
            >
              {chip.label}
              <button onClick={chip.clear} className="hover:text-blue-900">{t.exportClearFilter}</button>
            </span>
          ))}
        </div>
      )}

      {/* Bulk actions */}
      <div className="flex items-center gap-3 text-sm">
        <button onClick={selectAll} className="text-blue-600 hover:text-blue-800">
          {t.exportSelectAll(cards.length)}
        </button>
        <span className="text-gray-300">|</span>
        <button onClick={deselectAll} className="text-gray-500 hover:text-gray-700">
          {t.exportDeselectAll}
        </button>
        <span className="text-gray-400 ml-auto text-xs">
          {isLoading ? 'Loading…' : `${cards.length} cards`}
        </span>
      </div>

      {/* Card list */}
      <div className="rounded-xl border border-gray-200 bg-white divide-y divide-gray-100 overflow-hidden">
        {cards.map(card => (
          <label
            key={card.external_id}
            className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-gray-50"
          >
            <input
              type="checkbox"
              checked={selectedIds.has(card.external_id)}
              onChange={() => toggleCard(card.external_id)}
              className="rounded border-gray-300 shrink-0"
            />
            {card.front_image_path && (
              <img
                src={`/api/v2/images/${card.front_image_path}`}
                alt=""
                className="h-10 w-auto rounded border border-gray-100 object-contain bg-gray-50 shrink-0"
              />
            )}
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-gray-800 truncate">
                {card.person_name ?? '—'}
              </p>
              <p className="text-xs text-gray-400">
                {card.received_date ?? card.created_at.slice(0, 10)}
              </p>
            </div>
            {/* Sync badges */}
            <div className="flex gap-1 shrink-0">
              {(card.synced_destinations ?? []).map(dest => (
                <span
                  key={dest}
                  className="text-xs bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded"
                  title={t.exportAlreadySynced}
                >
                  {DEST_BADGE_LABELS[dest] ?? dest}
                </span>
              ))}
            </div>
          </label>
        ))}
        {!isLoading && cards.length === 0 && (
          <div className="px-4 py-8 text-center text-sm text-gray-400">No cards match the current filters.</div>
        )}
      </div>

      {/* Footer */}
      <div className="sticky bottom-4">
        <button
          disabled={visibleSelectedIds.length === 0}
          onClick={() => setStep('destinations')}
          className="btn-primary w-full py-3 text-sm shadow-lg disabled:opacity-40"
        >
          {t.exportNextBtn(visibleSelectedIds.length)}
        </button>
      </div>
    </div>
  )
}
