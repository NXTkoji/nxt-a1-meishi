/**
 * ExportDestinationSelector
 *
 * Second step of the export flow:
 *   - Multi-select destination checkboxes (Odoo, Google Contacts, + 2 download types)
 *   - "Export N cards to …" button
 *   - Inline result list after export runs (push) or downloads complete
 */
import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { runExport } from '../api'
import { useLang } from '../LangContext'
import type { ExportResultItem } from '../types'

// API key from Vite env — same pattern as api/client.ts
const API_KEY = import.meta.env.VITE_API_KEY ?? ''

interface Destination {
  key: string
  label: string
  configured: boolean
  download: boolean
}

const DESTINATIONS: Destination[] = [
  { key: 'odoo',            label: 'Odoo',                       configured: true, download: false },
  { key: 'google_contacts', label: 'Google Contacts',            configured: true, download: false },
  { key: 'odoo_export',     label: 'Odoo Export (CSV + Images)', configured: true, download: true  },
  { key: 'google_csv',      label: 'Google Contacts CSV',        configured: true, download: true  },
]

/** Trigger a browser download from a Blob */
function triggerBlobDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

/** Fetch one URL and trigger download. Returns true on success (non-404 counts as success for optional images). */
async function fetchAndDownload(
  url: string,
  fallbackFilename: string,
): Promise<boolean> {
  const res = await fetch(url, {
    headers: { Authorization: `Bearer ${API_KEY}` },
  })
  if (res.status === 404) return true  // 404 = no image for that side — skip silently
  if (!res.ok) return false
  const blob = await res.blob()
  // Use server-provided filename from Content-Disposition when available
  const cd = res.headers.get('content-disposition') ?? ''
  const match = cd.match(/filename="?([^"]+)"?/)
  const filename = match?.[1] ?? fallbackFilename
  triggerBlobDownload(blob, filename)
  return true
}

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
  // Results for direct-push destinations
  const [pushResults, setPushResults] = useState<ExportResultItem[] | null>(null)
  // Status for download destinations: key → 'downloading' | 'done' | 'error'
  const [downloadStatus, setDownloadStatus] = useState<Record<string, string>>({})
  const [hasRun, setHasRun] = useState(false)

  const pushMutation = useMutation({
    mutationFn: (pushDests: string[]) =>
      runExport({ card_external_ids: cardExternalIds, destinations: pushDests }),
    onSuccess: (data) => setPushResults(data.results),
  })

  const toggle = (key: string) =>
    setSelected(prev => {
      const next = new Set(prev)
      next.has(key) ? next.delete(key) : next.add(key)
      return next
    })

  /** Run all selected download destinations sequentially */
  async function triggerDownloads(dlDests: string[]) {
    const baseUrl = '/api/v2/export'
    const idsParam = cardExternalIds.join(',')

    for (const dest of dlDests) {
      setDownloadStatus(prev => ({ ...prev, [dest]: 'downloading' }))
      try {
        if (dest === 'google_csv') {
          const ok = await fetchAndDownload(
            `${baseUrl}/csv?card_ids=${encodeURIComponent(idsParam)}&format=google_contacts`,
            'contacts_google.csv',
          )
          setDownloadStatus(prev => ({ ...prev, [dest]: ok ? 'done' : 'error' }))
        } else if (dest === 'odoo_export') {
          // 1. CSV first
          const csvOk = await fetchAndDownload(
            `${baseUrl}/csv?card_ids=${encodeURIComponent(idsParam)}&format=odoo`,
            'contacts_odoo.csv',
          )
          if (!csvOk) {
            setDownloadStatus(prev => ({ ...prev, [dest]: 'error' }))
            continue
          }
          // 2. Images — front then back, for each card, sequentially
          //    404 = no image for that side → skipped silently by fetchAndDownload
          for (const cardId of cardExternalIds) {
            for (const side of ['front', 'back'] as const) {
              await fetchAndDownload(
                `${baseUrl}/image/${cardId}/${side}`,
                `${cardId}_${side}.jpg`,  // server sends the real name in Content-Disposition
              )
            }
          }
          setDownloadStatus(prev => ({ ...prev, [dest]: 'done' }))
        }
      } catch {
        setDownloadStatus(prev => ({ ...prev, [dest]: 'error' }))
      }
    }
  }

  async function handleExport() {
    setHasRun(true)
    setDownloadStatus({})  // Clear previous results before starting new export
    const pushDests = [...selected].filter(k => {
      const d = DESTINATIONS.find(x => x.key === k)
      return d && !d.download
    })
    const dlDests = [...selected].filter(k => {
      const d = DESTINATIONS.find(x => x.key === k)
      return d && d.download
    })

    // Run push and downloads in parallel
    const promises: Promise<unknown>[] = []
    if (pushDests.length > 0) promises.push(pushMutation.mutateAsync(pushDests))
    if (dlDests.length > 0) promises.push(triggerDownloads(dlDests))
    await Promise.allSettled(promises)
  }

  const destLabel = [...selected]
    .map(k => DESTINATIONS.find(d => d.key === k)?.label ?? k)
    .join(' + ')

  const isPending = pushMutation.isPending ||
    Object.values(downloadStatus).includes('downloading')

  const allDownloadsDone = Object.keys(downloadStatus).length === 0 ||
    Object.values(downloadStatus).every(s => s === 'done' || s === 'error')

  const showResults = hasRun && !pushMutation.isPending && allDownloadsDone

  if (showResults && (pushResults !== null || Object.keys(downloadStatus).length > 0)) {
    // Group push results by card
    const grouped: Record<string, ExportResultItem[]> = {}
    for (const r of pushResults ?? []) {
      grouped[r.card_external_id] = grouped[r.card_external_id] ?? []
      grouped[r.card_external_id].push(r)
    }

    return (
      <div className="max-w-2xl mx-auto py-6 px-4 space-y-4">
        <h2 className="text-base font-semibold">{t.exportDestTitle}</h2>

        {/* Download destination results */}
        {Object.keys(downloadStatus).length > 0 && (
          <div className="rounded-xl border border-gray-200 bg-white divide-y divide-gray-100 overflow-hidden">
            {Object.entries(downloadStatus).map(([dest, status]) => {
              const label = DESTINATIONS.find(d => d.key === dest)?.label ?? dest
              return (
                <div key={dest} className="px-4 py-3 flex items-center justify-between gap-4">
                  <span className="text-sm text-gray-700 font-medium">{label}</span>
                  <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                    status === 'done'  ? 'bg-green-100 text-green-700' :
                    status === 'error' ? 'bg-red-100 text-red-700' :
                    'bg-gray-100 text-gray-500'
                  }`}>
                    {status === 'done' ? '✓ Downloaded' :
                     status === 'error' ? '✗ Failed' : 'Downloading…'}
                  </span>
                </div>
              )
            })}
          </div>
        )}

        {/* Push destination results — per card */}
        {Object.keys(grouped).length > 0 && (
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
        )}

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
            {dest.download && (
              <span className="ml-auto text-xs text-gray-400">↓ Download</span>
            )}
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
        disabled={selected.size === 0 || isPending}
        onClick={handleExport}
        className="btn-primary w-full py-3 text-sm disabled:opacity-50"
      >
        {isPending
          ? 'Exporting…'
          : t.exportRunBtn(cardExternalIds.length, destLabel || '…')}
      </button>

      {pushMutation.isError && (
        <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded px-3 py-2">
          Export failed: {pushMutation.error instanceof Error ? pushMutation.error.message : 'Unknown error'}
        </div>
      )}
    </div>
  )
}
