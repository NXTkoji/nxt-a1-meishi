/**
 * PersonDetailPage — view and edit a person record.
 *
 * URL: /persons/:external_id
 * Shows all person data + their associated cards.
 */
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getPerson, listCards, deletePerson } from '../api'
import { PersonEditor } from '../components/PersonEditor'
import { LoadError } from '../components/LoadMore'
import { useToast } from '../components/Toast'
import { useLang } from '../LangContext'
import type { Person, CardListItem } from '../types'

function usePersonExtId(): string {
  // URL is /persons/:external_id
  const parts = window.location.pathname.split('/')
  return parts[parts.length - 1] ?? ''
}

export function PersonDetailPage() {
  const { t } = useLang()
  const { showToast } = useToast()
  const qc = useQueryClient()
  const extId = usePersonExtId()

  // Rendered by query status in the order described on the facets query in
  // CollectionPage.tsx: loading error → no data yet → data. Neither `isLoading` nor
  // `isSuccess` is used, because a PAUSED query (retry due in a hidden tab, or offline)
  // is neither loading nor errored yet has no data.
  const {
    data: person,
    isLoadingError: personLoadError,
    error: personError,
    isFetching: personFetching,
    refetch: refetchPerson,
  } = useQuery<Person>({
    queryKey: ['person', extId],
    queryFn: () => getPerson(extId),
    enabled: !!extId,
  })

  // No `= []` default: `undefined` (not loaded yet) must stay distinct from `[]` (no cards).
  const {
    data: cards,
    isLoadingError: cardsLoadError,
    isFetching: cardsFetching,
    refetch: refetchCards,
  } = useQuery<CardListItem[]>({
    queryKey: ['cards', { person_id: person?.id }],
    // Explicit 500 (the endpoint's cap, as ExportPage uses): without it the backend
    // default of 50 silently truncates a person's card list with no indication.
    queryFn: () => listCards({ person_id: person!.id, limit: 500 }),
    enabled: !!person?.id,
  })

  const deletePersonMutation = useMutation({
    mutationFn: () => deletePerson(extId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['persons'] })
      showToast(t.personDeleted)
      window.location.href = '/collection'
    },
    onError: () => showToast(t.saveError, 'error'),
  })

  const notFound = (
    <div className="max-w-4xl mx-auto py-12 text-center text-red-400">{t.personNotFound}</div>
  )
  // No id in the URL: the query is disabled and would stay pending (loading) forever.
  if (!extId) return notFound
  if (personLoadError) {
    // Only a real 404 means the person does not exist. The API client throws
    // `Error("404 Not Found: …")`; any other failure is "unreachable", not "missing".
    if (personError?.message.startsWith('404')) return notFound
    return (
      <div className="max-w-4xl mx-auto py-12 px-4">
        <LoadError onRetry={() => refetchPerson()} isRetrying={personFetching} />
      </div>
    )
  }
  if (person === undefined) {
    // Pending — whether the request is in flight or paused.
    return <div className="max-w-4xl mx-auto py-12 text-center text-gray-400">{t.loading}</div>
  }

  const primaryName = person.names.find(n => n.is_current)?.full_name ?? t.noName
  const primaryOrg = person.positions[0]?.org_names[0]?.name

  return (
    <div className="max-w-4xl mx-auto py-6 px-4 space-y-6">
      {/* Header row */}
      <div className="flex items-center gap-3">
        <a href="/collection" className="text-sm text-blue-500 hover:text-blue-700">← {t.navCollection}</a>
        <div className="flex-1" />
        <button
          onClick={() => {
            if (window.confirm(t.confirmDeletePerson)) deletePersonMutation.mutate()
          }}
          disabled={deletePersonMutation.isPending}
          className="text-xs text-red-400 hover:text-red-600 disabled:opacity-50"
        >
          {t.deletePersonBtn}
        </button>
      </div>

      {/* Person avatar + name */}
      <div className="flex items-center gap-4">
        <div className="w-14 h-14 rounded-full bg-blue-100 text-blue-700 flex items-center justify-center text-2xl font-bold shrink-0">
          {primaryName.charAt(0)}
        </div>
        <div>
          <h1 className="text-xl font-semibold text-gray-900">{primaryName}</h1>
          {primaryOrg && <p className="text-sm text-gray-500">{primaryOrg}</p>}
        </div>
      </div>

      {/* Editable person data */}
      <PersonEditor
        person={person}
        onUpdated={() => qc.invalidateQueries({ queryKey: ['person', extId] })}
      />

      {/* Linked business cards — same status order as the person query above. A failed or
          still-pending request must not look like "this person has no cards". */}
      {cardsLoadError ? (
        <section>
          <h2 className="text-sm font-medium text-gray-700 mb-3">{t.linkedCards}</h2>
          <LoadError onRetry={() => refetchCards()} isRetrying={cardsFetching} />
        </section>
      ) : cards === undefined ? (
        // Pending — whether the request is in flight or paused.
        <section>
          <h2 className="text-sm font-medium text-gray-700 mb-3">{t.linkedCards}</h2>
          <p className="text-xs text-gray-400">{t.loading}</p>
        </section>
      ) : cards.length > 0 && (
        <section>
          <h2 className="text-sm font-medium text-gray-700 mb-3">{t.linkedCards} ({cards.length})</h2>
          <div className="flex flex-wrap gap-3">
            {cards.map(card => (
              <a
                key={card.id}
                href={`/cards/${card.external_id}`}
                className="block rounded-xl border border-gray-200 bg-white shadow-sm hover:shadow-md hover:border-blue-300 transition-all overflow-hidden group"
              >
                {card.front_image_path ? (
                  <img
                    src={`/api/v2/images/${card.front_image_path}`}
                    alt={card.person_name ?? t.noName}
                    className="h-24 w-auto object-cover group-hover:scale-105 transition-transform duration-200"
                  />
                ) : (
                  <div className="h-24 w-36 bg-gray-100 flex items-center justify-center">
                    <span className="text-3xl text-gray-300">🪪</span>
                  </div>
                )}
                <div className="px-2 py-1.5">
                  <p className="text-xs text-gray-400">
                    {card.received_date ?? new Date(card.created_at).toLocaleDateString()}
                  </p>
                </div>
              </a>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
