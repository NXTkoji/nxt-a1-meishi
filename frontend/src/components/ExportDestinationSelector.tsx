/**
 * ExportDestinationSelector
 *
 * Second step of the export flow:
 *   - Multi-select destination checkboxes (Odoo, Google Contacts)
 *   - "Export N cards to …" button
 *   - Inline result list after export runs
 */
import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { runExport } from '../api'
import { useLang } from '../LangContext'
import type { ExportResultItem } from '../types'

interface Destination {
  key: string
  label: string
  configured: boolean
}

const DESTINATIONS: Destination[] = [
  { key: 'odoo', label: 'Odoo', configured: true },
  { key: 'google_contacts', label: 'Google Contacts', configured: true },
]

export function ExportDestinationSelector({
  cardExternalIds,
  onBack,
  onDone,
}: {
  cardExternalIds: string[]
  onBack: () => void
  onDone: () => void
}) {
  const { t } = useLang()
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [results, setResults] = useState<ExportResultItem[] | null>(null)

  const exportMutation = useMutation({
    mutationFn: () =>
      runExport({ card_external_ids: cardExternalIds, destinations: [...selected] }),
    onSuccess: (data) => setResults(data.results),
  })

  const toggle = (key: string) =>
    setSelected(prev => {
      const next = new Set(prev)
      next.has(key) ? next.delete(key) : next.add(key)
      return next
    })

  const destLabel = [...selected]
    .map(k => DESTINATIONS.find(d => d.key === k)?.label ?? k)
    .join(' + ')

  // If export ran, show results
  if (results) {
    const grouped: Record<string, ExportResultItem[]> = {}
    for (const r of results) {
      grouped[r.card_external_id] = grouped[r.card_external_id] ?? []
      grouped[r.card_external_id].push(r)
    }

    return (
      <div className="max-w-2xl mx-auto py-6 px-4 space-y-4">
        <h2 className="text-base font-semibold">{t.exportDestTitle}</h2>
        <div className="rounded-xl border border-gray-200 bg-white divide-y divide-gray-100 overflow-hidden">
          {Object.entries(grouped).map(([cardId, items]) => (
            <div key={cardId} className="px-4 py-3 flex items-center justify-between gap-4">
              <span className="text-sm text-gray-700 font-mono truncate max-w-[200px]">{cardId.slice(0, 8)}…</span>
              <div className="flex gap-2 flex-wrap justify-end">
                {items.map(item => (
                  <span
                    key={item.destination}
                    className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                      item.result === 'error'
                        ? 'bg-red-100 text-red-700'
                        : 'bg-green-100 text-green-700'
                    }`}
                    title={item.error_message ?? ''}
                  >
                    {item.destination}: {
                      item.result === 'created' ? t.exportResultCreated :
                      item.result === 'updated' ? t.exportResultUpdated :
                      t.exportResultError
                    }
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
        <button onClick={onDone} className="btn-secondary text-sm">
          {t.exportBackToList}
        </button>
      </div>
    )
  }

  return (
    <div className="max-w-2xl mx-auto py-6 px-4 space-y-4">
      <div className="flex items-center gap-3">
        <button onClick={onBack} className="text-sm text-blue-600 hover:text-blue-800">← Back</button>
        <h2 className="text-base font-semibold">{t.exportDestTitle}</h2>
      </div>

      <div className="rounded-xl border border-gray-200 bg-white divide-y divide-gray-100 overflow-hidden">
        {DESTINATIONS.map(dest => (
          <label
            key={dest.key}
            className={`flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-gray-50 ${!dest.configured ? 'opacity-50' : ''}`}
          >
            <input
              type="checkbox"
              checked={selected.has(dest.key)}
              disabled={!dest.configured}
              onChange={() => toggle(dest.key)}
              className="rounded border-gray-300"
            />
            <span className="text-sm font-medium text-gray-800">{dest.label}</span>
            {!dest.configured && (
              <span className="ml-auto text-xs text-gray-400">
                {t.exportDestNotConfigured}{' '}
                <a href="/settings" className="text-blue-500 underline">{t.exportDestSetup}</a>
              </span>
            )}
          </label>
        ))}
      </div>

      <div className="text-xs text-gray-400 text-center">
        {cardExternalIds.length} card{cardExternalIds.length !== 1 ? 's' : ''} selected
      </div>

      <button
        disabled={selected.size === 0 || exportMutation.isPending}
        onClick={() => exportMutation.mutate()}
        className="btn-primary w-full py-3 text-sm disabled:opacity-50"
      >
        {exportMutation.isPending
          ? 'Exporting…'
          : t.exportRunBtn(cardExternalIds.length, destLabel || '…')}
      </button>

      {exportMutation.isError && (
        <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded px-3 py-2">
          Export failed: {(exportMutation.error as Error).message}
        </div>
      )}
    </div>
  )
}
