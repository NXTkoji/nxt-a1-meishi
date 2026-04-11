/**
 * ScanPage — the main scan flow.
 *
 * Stages:
 *  upload   → Drop images, they upload immediately
 *  group    → Assign images to card groups (front/back/etc.)
 *  analyze  → Stream Claude Vision results per card group
 *  review   → User reviews/edits each parsed card
 *  confirm  → Write permanent records
 *  done     → Show summary with links
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  analyzeSession,
  confirmSession,
  countCards,
  createOccasion,
  createSession,
  getSession,
  listMyCompanies,
  listOccasions,
  logCorrection,
  manualSplitImage,
  rotateImage,
  updateImageGroup,
  uploadImage,
} from '../api'
import { DropZone } from '../components/DropZone'
import { ParsedCardEditor } from '../components/ParsedCardEditor'
import { ConfidenceBadge } from '../components/ConfidenceBadge'
import { LightboxImage } from '../components/ImageLightbox'
import { CropModal } from '../components/CropModal'
import CardOutlineSelector from '../components/CardOutlineSelector'
import type { Point } from '../api/sessions'
import { useLang } from '../LangContext'
import type {
  AnalysisEvent,
  CardDraft,
  MyCompany,
  Occasion,
  ParsedCard,
  Session,
  SessionImage,
} from '../types'

// ─── Types ────────────────────────────────────────────────────────────────────

interface CardGroup {
  tempCardId: string
  images: SessionImage[]
  // set after analysis
  parsed?: ParsedCard
  matchPersonId?: number
  matchName?: string
  matchConfidence?: number
  // user selections
  myCompanyIds: number[]
  occasionId?: number
  receivedDate?: string
  notes?: string
  // analysis state
  status: 'pending' | 'analyzing' | 'done' | 'error'
  progress?: string
  error?: string
}

type Stage = 'idle' | 'uploading' | 'grouping' | 'analyzing' | 'review' | 'confirming' | 'done'

// ─── Helpers ──────────────────────────────────────────────────────────────────

function newGroup(tempCardId: string): CardGroup {
  return { tempCardId, images: [], myCompanyIds: [], status: 'pending' }
}

type SavedGroupState = Pick<
  CardGroup,
  'tempCardId' | 'parsed' | 'status' | 'matchPersonId' | 'matchName' | 'matchConfidence' |
  'myCompanyIds' | 'occasionId' | 'receivedDate' | 'notes' | 'error'
>

function saveGroupsState(sessionExtId: string, groups: CardGroup[]) {
  const state: Record<string, SavedGroupState> = {}
  for (const g of groups) {
    state[g.tempCardId] = {
      tempCardId: g.tempCardId,
      parsed: g.parsed,
      status: g.status,
      matchPersonId: g.matchPersonId,
      matchName: g.matchName,
      matchConfidence: g.matchConfidence,
      myCompanyIds: g.myCompanyIds,
      occasionId: g.occasionId,
      receivedDate: g.receivedDate,
      notes: g.notes,
      error: g.error,
    }
  }
  sessionStorage.setItem(`scan_groups_${sessionExtId}`, JSON.stringify(state))
}

function loadGroupsState(sessionExtId: string): Record<string, SavedGroupState> {
  try {
    const raw = sessionStorage.getItem(`scan_groups_${sessionExtId}`)
    return raw ? JSON.parse(raw) : {}
  } catch {
    return {}
  }
}

// ─── Component ────────────────────────────────────────────────────────────────

export function ScanPage() {
  const { t } = useLang()
  const [session, setSession] = useState<Session | null>(null)
  const [stage, setStage] = useState<Stage>('idle')
  const [ungrouped, setUngrouped] = useState<SessionImage[]>([])
  const [groups, setGroups] = useState<CardGroup[]>([])
  const [confirmed, setConfirmed] = useState<{ count: number } | null>(null)
  const [confirmError, setConfirmError] = useState<string | null>(null)
  const [splittingIds, setSplittingIds] = useState<Set<number>>(new Set())
  const [splitFeedback, setSplitFeedback] = useState<Record<number, string>>({})
  const [outlineTarget, setOutlineTarget] = useState<{ img: SessionImage; count: number; fromGroup?: string } | null>(null)
  const [imgCacheBust, setImgCacheBust] = useState<Record<number, number>>({})
  const abortRef = useRef<AbortController | null>(null)

  const { data: companies = [] } = useQuery<MyCompany[]>({
    queryKey: ['my-companies'],
    queryFn: listMyCompanies,
  })
  const { data: occasions = [] } = useQuery<Occasion[]>({
    queryKey: ['occasions'],
    queryFn: listOccasions,
  })

  // Resume or create session on mount
  useEffect(() => {
    const stored = sessionStorage.getItem('scan_session_id')
    if (stored) {
      getSession(stored)
        .then(s => {
          setSession(s)
          // Reconstruct grouping state from stored images
          const ungroupedImgs = s.images.filter(i => !i.temp_card_id)
          const grouped: Record<string, typeof s.images> = {}
          for (const img of s.images.filter(i => i.temp_card_id)) {
            const key = img.temp_card_id!
            grouped[key] = grouped[key] ?? []
            grouped[key].push(img)
          }
          setUngrouped(ungroupedImgs)

          // Restore analysis results from sessionStorage if available
          const savedState = loadGroupsState(s.external_id)
          const restoredGroups = Object.entries(grouped).map(([tempCardId, images]) => {
            const saved = savedState[tempCardId]
            return saved
              ? { ...newGroup(tempCardId), images, ...saved }
              : { ...newGroup(tempCardId), images }
          })
          setGroups(restoredGroups)

          // Determine stage: if any group has parsed data, resume at review
          const hasResults = restoredGroups.some(g => g.parsed)
          setStage(s.images.length === 0 ? 'uploading' : hasResults ? 'review' : 'grouping')
        })
        .catch(() => {
          // Session expired or gone — start fresh
          sessionStorage.removeItem('scan_session_id')
          createSession().then(s => {
            sessionStorage.setItem('scan_session_id', s.external_id)
            setSession(s)
            setStage('uploading')
          })
        })
    } else {
      createSession().then(s => {
        sessionStorage.setItem('scan_session_id', s.external_id)
        setSession(s)
        setStage('uploading')
      })
    }
  }, [])

  // Persist groups state whenever it changes (so navigation away doesn't lose results)
  useEffect(() => {
    if (session && groups.some(g => g.parsed || g.status === 'error')) {
      saveGroupsState(session.external_id, groups)
    }
  }, [groups, session])

  // ── Upload ──

  const handleFiles = useCallback(
    async (files: File[]) => {
      if (!session) return
      const results = await Promise.all(files.map(f => uploadImage(session.external_id, f)))
      setUngrouped(prev => [
        ...prev,
        ...results.map(r => ({
          id: r.id,
          image_filename: r.image_filename,
          uploaded_at: new Date().toISOString(),
        } as SessionImage)),
      ])
      setStage('grouping')
    },
    [session],
  )

  // ── Grouping ──

  const addGroup = () => {
    const id = crypto.randomUUID()
    setGroups(prev => [...prev, newGroup(id)])
  }

  const assignToGroup = async (img: SessionImage, groupId: string, sideOrder: number) => {
    if (!session) return
    await updateImageGroup(session.external_id, img.id, groupId, sideOrder)
    setUngrouped(prev => prev.filter(i => i.id !== img.id))
    setGroups(prev =>
      prev.map(g => {
        if (g.tempCardId !== groupId) return g
        return { ...g, images: [...g.images, { ...img, temp_card_id: groupId, side_order: sideOrder }] }
      }),
    )
  }

  const autoGroup1 = async () => {
    // Each image becomes its own single-sided card
    // Remove any existing empty groups first
    const keepGroups = groups.filter(g => g.images.length > 0)
    setGroups(keepGroups)
    const imgs = [...ungrouped]
    for (const img of imgs) {
      const id = crypto.randomUUID()
      setGroups(prev => [...prev, newGroup(id)])
      await assignToGroup(img, id, 0)
    }
  }

  const autoGroup2 = async () => {
    // Pair every 2 ungrouped images as front+back of one card
    const keepGroups = groups.filter(g => g.images.length > 0)
    setGroups(keepGroups)
    const imgs = [...ungrouped]
    for (let i = 0; i < imgs.length; i += 2) {
      const id = crypto.randomUUID()
      setGroups(prev => [...prev, newGroup(id)])
      await assignToGroup(imgs[i], id, 0)
      if (imgs[i + 1]) await assignToGroup(imgs[i + 1], id, 1)
    }
  }

  // Extract the _cardN position index from a split filename (e.g. "photo_card2.jpg" → 2)
  const getCardPos = (filename: string): number | null => {
    const m = filename.match(/_card(\d+)\./i)
    return m ? parseInt(m[1], 10) : null
  }

  // "Pair by position": group images by their _cardN suffix.
  // Images with the same position number (from different source photos) become
  // front/back sides of the same card group.
  // Only shown when ≥2 ungrouped images share at least one _cardN suffix.
  const canAutoPairByPos = (() => {
    const positions = ungrouped.map(i => getCardPos(i.image_filename)).filter(p => p !== null)
    return positions.length >= 2 && new Set(positions).size < positions.length
  })()

  const autoPairByPosition = async () => {
    const keepGroups = groups.filter(g => g.images.length > 0)
    setGroups(keepGroups)
    const imgs = [...ungrouped]
    // Build a map from position index → images (preserving upload order as side order)
    const byPos = new Map<number, SessionImage[]>()
    const noPos: SessionImage[] = []
    for (const img of imgs) {
      const pos = getCardPos(img.image_filename)
      if (pos !== null) {
        if (!byPos.has(pos)) byPos.set(pos, [])
        byPos.get(pos)!.push(img)
      } else {
        noPos.push(img)
      }
    }
    // Sort positions so Card #1 = smallest _cardN
    const sortedPositions = [...byPos.keys()].sort((a, b) => a - b)
    for (const pos of sortedPositions) {
      const group = byPos.get(pos)!
      const id = crypto.randomUUID()
      setGroups(prev => [...prev, newGroup(id)])
      for (let si = 0; si < group.length; si++) {
        await assignToGroup(group[si], id, si)
      }
    }
    // Images without a _cardN suffix each become their own single-sided card
    for (const img of noPos) {
      const id = crypto.randomUUID()
      setGroups(prev => [...prev, newGroup(id)])
      await assignToGroup(img, id, 0)
    }
  }

  const handleSplit = async (img: SessionImage) => {
    if (!session) return
    setSplittingIds(prev => new Set(prev).add(img.id))
    try {
      const { count } = await countCards(session.external_id, img.id)
      setOutlineTarget({ img, count })
    } catch {
      setSplitFeedback(prev => ({ ...prev, [img.id]: 'Error' }))
      setTimeout(() => setSplitFeedback(prev => { const n = { ...prev }; delete n[img.id]; return n }), 3000)
    } finally {
      setSplittingIds(prev => { const n = new Set(prev); n.delete(img.id); return n })
    }
  }

  const handleOutlineComplete = async (polygons: Point[][]) => {
    if (!session || !outlineTarget) return
    const { img, fromGroup } = outlineTarget
    setOutlineTarget(null)
    setSplittingIds(prev => new Set(prev).add(img.id))
    try {
      const result = await manualSplitImage(session.external_id, img.id, polygons)
      const newImgs = result.images.map(i => ({
        id: i.id,
        image_filename: i.image_filename,
        uploaded_at: new Date().toISOString(),
      } as SessionImage))
      if (!result.split) {
        if (fromGroup) {
          // Single card in a group — keep original, no change
          setSplitFeedback(prev => ({ ...prev, [img.id]: t.splitNone }))
        } else {
          setSplitFeedback(prev => ({ ...prev, [img.id]: t.splitNone }))
        }
        setTimeout(() => setSplitFeedback(prev => { const n = { ...prev }; delete n[img.id]; return n }), 3000)
      } else if (fromGroup) {
        setGroups(prev =>
          prev.map(g =>
            g.tempCardId === fromGroup
              ? { ...g, images: g.images.filter(i => i.id !== img.id) }
              : g
          )
        )
        setUngrouped(prev => [...prev, ...newImgs])
      } else {
        setUngrouped(prev => [
          ...prev.filter(u => u.id !== img.id),
          ...newImgs,
        ])
        setSplitFeedback(prev => ({ ...prev, [img.id]: t.splitDone(result.images.length) }))
      }
    } catch {
      setSplitFeedback(prev => ({ ...prev, [img.id]: 'Error' }))
      setTimeout(() => setSplitFeedback(prev => { const n = { ...prev }; delete n[img.id]; return n }), 3000)
    } finally {
      setSplittingIds(prev => { const n = new Set(prev); n.delete(img.id); return n })
    }
  }

  const handleSplitGrouped = async (img: SessionImage, groupId: string) => {
    if (!session) return
    setSplittingIds(prev => new Set(prev).add(img.id))
    try {
      const { count } = await countCards(session.external_id, img.id)
      setOutlineTarget({ img, count, fromGroup: groupId })
    } catch {
      setSplitFeedback(prev => ({ ...prev, [img.id]: 'Error' }))
      setTimeout(() => setSplitFeedback(prev => { const n = { ...prev }; delete n[img.id]; return n }), 3000)
    } finally {
      setSplittingIds(prev => { const n = new Set(prev); n.delete(img.id); return n })
    }
  }

  const handleRotate = async (img: SessionImage) => {
    if (!session) return
    await rotateImage(session.external_id, img.id)
    setImgCacheBust(prev => ({ ...prev, [img.id]: Date.now() }))
  }

  const handleAddImage = useCallback(
    async (groupId: string, file: File) => {
      if (!session) return
      const result = await uploadImage(session.external_id, file)
      const newImg: SessionImage = {
        id: result.id,
        image_filename: result.image_filename,
        uploaded_at: new Date().toISOString(),
      }
      const grp = groups.find(g => g.tempCardId === groupId)
      const nextOrder = grp ? grp.images.length : 0
      await updateImageGroup(session.external_id, result.id, groupId, nextOrder)
      setGroups(prev =>
        prev.map(g =>
          g.tempCardId === groupId
            ? { ...g, images: [...g.images, { ...newImg, temp_card_id: groupId, side_order: nextOrder }] }
            : g,
        ),
      )
    },
    [session, groups],
  )

  const moveImageBetweenGroups = useCallback(
    async (imgId: number, fromGroupId: string, toGroupId: string) => {
      if (!session) return
      const fromGroup = groups.find(g => g.tempCardId === fromGroupId)
      if (!fromGroup) return
      const img = fromGroup.images.find(i => i.id === imgId)
      if (!img) return
      const toGroup = groups.find(g => g.tempCardId === toGroupId)
      if (!toGroup) return
      const newSideOrder = toGroup.images.length
      await updateImageGroup(session.external_id, imgId, toGroupId, newSideOrder)
      setGroups(prev =>
        prev.map(g => {
          if (g.tempCardId === fromGroupId) {
            const remaining = g.images.filter(i => i.id !== imgId)
            return { ...g, images: remaining }
          }
          if (g.tempCardId === toGroupId) {
            return { ...g, images: [...g.images, { ...img, temp_card_id: toGroupId, side_order: newSideOrder }] }
          }
          return g
        }),
      )
    },
    [session, groups],
  )

  const swapImagesInGroup = useCallback(
    async (groupId: string) => {
      if (!session) return
      const group = groups.find(g => g.tempCardId === groupId)
      if (!group || group.images.length < 2) return
      const sorted = [...group.images].sort((a, b) => (a.side_order ?? 0) - (b.side_order ?? 0))
      const [img0, img1] = sorted
      await Promise.all([
        updateImageGroup(session.external_id, img0.id, groupId, 1),
        updateImageGroup(session.external_id, img1.id, groupId, 0),
      ])
      setGroups(prev =>
        prev.map(g => {
          if (g.tempCardId !== groupId) return g
          return {
            ...g,
            images: g.images.map(img => {
              if (img.id === img0.id) return { ...img, side_order: 1 }
              if (img.id === img1.id) return { ...img, side_order: 0 }
              return img
            }),
          }
        }),
      )
    },
    [session, groups],
  )

  // ── Analysis ──

  const startAnalysis = async () => {
    if (!session) return
    setStage('analyzing')
    setGroups(prev => prev.map(g => ({ ...g, status: 'analyzing' as const })))

    abortRef.current = new AbortController()

    await analyzeSession(
      session.external_id,
      (event: AnalysisEvent) => {
        if (event.type === 'progress' && event.temp_card_id) {
          setGroups(prev =>
            prev.map(g =>
              g.tempCardId === event.temp_card_id
                ? { ...g, progress: event.message }
                : g,
            ),
          )
        } else if (event.type === 'result' && event.temp_card_id && event.parsed) {
          setGroups(prev =>
            prev.map(g =>
              g.tempCardId === event.temp_card_id
                ? {
                    ...g,
                    status: 'done' as const,
                    parsed: event.parsed,
                    matchPersonId: event.match?.person_id,
                    matchName: event.match?.matched_name,
                    matchConfidence: event.match?.match_confidence,
                    progress: undefined,
                  }
                : g,
            ),
          )
        } else if (event.type === 'error' && event.temp_card_id) {
          setGroups(prev =>
            prev.map(g =>
              g.tempCardId === event.temp_card_id
                ? { ...g, status: 'error' as const, error: event.error }
                : g,
            ),
          )
        } else if (event.type === 'done') {
          setStage('review')
        }
      },
      abortRef.current.signal,
    ).catch(() => setStage('review'))
  }

  // ── Confirm ──

  const handleConfirm = async () => {
    if (!session) return
    setStage('confirming')
    setConfirmError(null)
    const cards: CardDraft[] = groups
      .filter(g => g.parsed)
      .map(g => ({
        temp_card_id: g.tempCardId,
        parsed: g.parsed!,
        match_person_id: g.matchPersonId,
        my_company_ids: g.myCompanyIds,
        occasion_id: g.occasionId,
        received_date: g.receivedDate,
        notes: g.notes,
      }))
    try {
      const result = await confirmSession(session.external_id, cards)
      sessionStorage.removeItem('scan_session_id')
      sessionStorage.removeItem(`scan_groups_${session.external_id}`)
      setConfirmed({ count: result.confirmed.length })
      setStage('done')
    } catch (err) {
      setConfirmError(err instanceof Error ? err.message : String(err))
      setStage('review')
    }
  }

  const resetToNew = () => {
    if (session) sessionStorage.removeItem(`scan_groups_${session.external_id}`)
    sessionStorage.removeItem('scan_session_id')
    setSession(null)
    setStage('idle')
    setUngrouped([])
    setGroups([])
    setConfirmed(null)
    createSession().then(s => {
      sessionStorage.setItem('scan_session_id', s.external_id)
      setSession(s)
      setStage('uploading')
    })
  }

  // ─── Render ──────────────────────────────────────────────────────────────────

  if (stage === 'done' && confirmed) {
    return (
      <div className="max-w-lg mx-auto py-20 text-center space-y-4">
        <div className="text-5xl">✅</div>
        <h2 className="text-xl font-semibold">{t.savedN(confirmed.count)}</h2>
        <div className="flex justify-center gap-3">
          <button onClick={resetToNew} className="btn-primary">{t.newScan}</button>
          <a href="/collection" className="btn-secondary">{t.viewCollection}</a>
        </div>
      </div>
    )
  }

  return (
    <>
    {outlineTarget && session && (
      <CardOutlineSelector
        imageUrl={`/api/v2/sessions/${session.external_id}/temp/${outlineTarget.img.image_filename}${imgCacheBust[outlineTarget.img.id] ? `?t=${imgCacheBust[outlineTarget.img.id]}` : ''}`}
        cardCount={outlineTarget.count}
        onComplete={handleOutlineComplete}
        onCancel={() => setOutlineTarget(null)}
      />
    )}
    <div className="max-w-4xl mx-auto py-6 px-4 space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-lg font-semibold text-gray-900">{t.scanTitle}</h1>
          {stage !== 'uploading' && stage !== 'idle' && stage !== 'done' && (
            <button
              onClick={resetToNew}
              className="text-xs text-gray-400 hover:text-gray-600 underline underline-offset-2"
            >
              {t.startOver}
            </button>
          )}
        </div>
        <StageIndicator stage={stage} />
      </div>

      {/* Upload */}
      {(stage === 'uploading' || stage === 'grouping') && (
        <DropZone onFiles={handleFiles} />
      )}

      {/* Ungrouped images */}
      {ungrouped.length > 0 && (
        <section className="space-y-2">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium text-gray-700">{t.ungroupedN(ungrouped.length)}</h2>
            <div className="flex gap-1">
              <button onClick={autoGroup1} className="btn-sm">{t.autoGroup1}</button>
              <button onClick={autoGroup2} className="btn-sm">{t.autoGroup2}</button>
              {stage === 'grouping' && groups.length === 0 && (
                <button
                  onClick={async () => { await autoGroup1(); startAnalysis() }}
                  className="btn-primary text-sm"
                >
                  {t.startAnalysis}
                </button>
              )}
              {canAutoPairByPos && (
                <button onClick={autoPairByPosition} className="btn-sm bg-amber-50 border-amber-300 text-amber-700 hover:bg-amber-100" title="Group images from different photos by matching card position">
                  {t.autoPairByPos}
                </button>
              )}
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {ungrouped.map(img => (
              <div key={img.id} className="relative">
                <LightboxImage
                  src={`/api/v2/sessions/${session?.external_id}/temp/${img.image_filename}${imgCacheBust[img.id] ? `?t=${imgCacheBust[img.id]}` : ''}`}
                  alt={img.image_filename}
                  className="h-24 w-auto rounded border border-gray-200 object-cover"
                />
                {/* Action buttons always visible below the image */}
                <div className="flex flex-wrap gap-0.5 mt-1 max-w-[96px]">
                  {groups.map((g, gi) => (
                    <button
                      key={g.tempCardId}
                      className="bg-blue-100 text-xs px-1.5 py-0.5 rounded text-blue-700 hover:bg-blue-600 hover:text-white"
                      onClick={() => assignToGroup(img, g.tempCardId, g.images.length)}
                      title={`${t.cardN(gi + 1)}`}
                    >
                      #{gi + 1}
                    </button>
                  ))}
                  <button
                    className="bg-yellow-100 text-xs px-1.5 py-0.5 rounded text-yellow-700 hover:bg-yellow-400 hover:text-gray-900 disabled:opacity-50"
                    disabled={splittingIds.has(img.id)}
                    onClick={() => handleSplit(img)}
                    title={t.splitCards}
                  >
                    {splittingIds.has(img.id) ? '…' : '✂️'}
                  </button>
                  <button
                    className="bg-gray-100 text-xs px-1.5 py-0.5 rounded text-gray-600 hover:bg-gray-300"
                    onClick={() => handleRotate(img)}
                    title="Rotate 90° clockwise"
                  >
                    ↻
                  </button>
                </div>
                {splitFeedback[img.id] && (
                  <div className="absolute top-0 left-0 right-0 bg-black/70 text-white text-xs text-center py-0.5 rounded-t">
                    {splitFeedback[img.id]}
                  </div>
                )}
                <p className="text-xs text-gray-500 mt-1 truncate max-w-[96px]">{img.image_filename}</p>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Card groups */}
      {groups.length > 0 && (
        <section className="space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium text-gray-700">{t.cardGroupsN(groups.length)}</h2>
            {stage === 'grouping' && (
              <div className="flex gap-2">
                <button onClick={addGroup} className="btn-sm">{t.addGroup}</button>
                <button
                  disabled={groups.every(g => g.images.length === 0)}
                  onClick={startAnalysis}
                  className="btn-primary text-sm"
                >
                  {t.startAnalysis}
                </button>
              </div>
            )}
          </div>

          {groups.map((group, gi) => (
            <CardGroupCard
              key={group.tempCardId}
              group={group}
              index={gi}
              sessionId={session?.external_id ?? ''}
              companies={companies}
              occasions={occasions}
              stage={stage}
              splittingIds={splittingIds}
              splitFeedback={splitFeedback}
              onSplitImage={handleSplitGrouped}
              onParsedChange={parsed =>
                setGroups(prev =>
                  prev.map(g => g.tempCardId === group.tempCardId ? { ...g, parsed } : g),
                )
              }
              onMetaChange={meta =>
                setGroups(prev =>
                  prev.map(g => g.tempCardId === group.tempCardId ? { ...g, ...meta } : g),
                )
              }
              onCorrection={c => {
                logCorrection({ ...c, card_id: null }).catch(() => {/* fire-and-forget */})
              }}
              onAddImage={handleAddImage}
              onMoveImage={moveImageBetweenGroups}
              onSwapImages={() => swapImagesInGroup(group.tempCardId)}
            />
          ))}
        </section>
      )}

      {/* Cancel analysis */}
      {stage === 'analyzing' && (
        <div className="flex justify-end">
          <button
            onClick={() => { abortRef.current?.abort(); setStage('review') }}
            className="btn-secondary text-sm"
          >
            {t.cancelAnalysis}
          </button>
        </div>
      )}

      {/* Save error */}
      {stage === 'review' && confirmError && (
        <div className="rounded border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700">
          {t.saveError}: {confirmError}
        </div>
      )}

      {/* Review confirm bar */}
      {stage === 'review' && (
        <div className="sticky bottom-4 flex justify-between gap-3">
          <button
            onClick={() => {
              setGroups(prev => prev.map(g => ({ ...g, parsed: undefined, status: 'pending' as const, progress: undefined, error: undefined })))
              setStage('grouping')
            }}
            className="btn-secondary shadow-lg px-5 py-3 text-base"
          >
            {t.backToGrouping}
          </button>
          <div className="flex gap-3">
            {groups.some(g => g.status === 'error') && (
              <button
                onClick={startAnalysis}
                className="btn-secondary shadow-lg px-5 py-3 text-base"
              >
                {t.retryAnalysis}
              </button>
            )}
            {groups.some(g => g.parsed) && (
              <button
                onClick={handleConfirm}
                className="btn-primary shadow-lg px-6 py-3 text-base"
              >
                {t.saveN(groups.filter(g => g.parsed).length)}
              </button>
            )}
          </div>
        </div>
      )}

      {stage === 'confirming' && (
        <div className="text-center py-8 text-gray-500">{t.saving}</div>
      )}
    </div>
    </>
  )
}

// ─── Sub-components ────────────────────────────────────────────────────────────

function StageIndicator({ stage }: { stage: Stage }) {
  const { t } = useLang()
  const steps: [Stage, string][] = [
    ['uploading', t.stageUpload],
    ['grouping', t.stageGroup],
    ['analyzing', t.stageAnalyze],
    ['review', t.stageReview],
    ['done', t.stageDone],
  ]
  const idx = steps.findIndex(([s]) => s === stage)
  return (
    <div className="flex items-center gap-1 text-xs">
      {steps.map(([s, label], i) => (
        <span key={s} className={`px-2 py-0.5 rounded-full ${i === idx ? 'bg-blue-600 text-white' : i < idx ? 'text-green-600' : 'text-gray-400'}`}>
          {label}
        </span>
      ))}
    </div>
  )
}

function OccasionPicker({
  occasions,
  value,
  onChange,
}: {
  occasions: Occasion[]
  value?: number
  onChange: (id?: number) => void
}) {
  const { t } = useLang()
  const qc = useQueryClient()
  const [adding, setAdding] = useState(false)
  const [newName, setNewName] = useState('')

  const addMutation = useMutation({
    mutationFn: (name: string) => createOccasion({ name }),
    onSuccess: (occ) => {
      qc.invalidateQueries({ queryKey: ['occasions'] })
      onChange(occ.id)
      setAdding(false)
      setNewName('')
    },
  })

  // Sort by created_at desc; take first 3 as "recent", rest as "all"
  const sorted = [...occasions].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  )
  const recent = sorted.slice(0, 3)
  const recentIds = new Set(recent.map(o => o.id))
  const older = sorted.filter(o => !recentIds.has(o.id))

  return (
    <div>
      <label className="text-gray-500 block mb-1">{t.occasionLabel}</label>
      <select
        value={value ?? ''}
        onChange={e => onChange(e.target.value ? Number(e.target.value) : undefined)}
        className="w-full border border-gray-300 rounded px-2 py-0.5 text-xs"
      >
        <option value="">{t.noneOption}</option>
        {recent.length > 0 && (
          <optgroup label="Recent">
            {recent.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}
          </optgroup>
        )}
        {older.length > 0 && (
          <optgroup label="All">
            {older.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}
          </optgroup>
        )}
      </select>

      {adding ? (
        <div className="flex gap-1 mt-1">
          <input
            type="text"
            value={newName}
            onChange={e => setNewName(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter' && newName.trim()) addMutation.mutate(newName.trim())
              if (e.key === 'Escape') { setAdding(false); setNewName('') }
            }}
            placeholder={t.occasionNewPlaceholder}
            className="flex-1 border border-gray-300 rounded px-2 py-0.5 text-xs focus:outline-none focus:ring-1 focus:ring-blue-400"
            autoFocus
          />
          <button
            className="text-xs text-blue-600 font-medium disabled:opacity-50"
            disabled={!newName.trim() || addMutation.isPending}
            onClick={() => newName.trim() && addMutation.mutate(newName.trim())}
          >{t.saveBtn}</button>
          <button
            className="text-xs text-gray-400"
            onClick={() => { setAdding(false); setNewName('') }}
          >{t.cancelBtn}</button>
        </div>
      ) : (
        <button
          className="text-xs text-blue-500 hover:text-blue-700 mt-0.5"
          onClick={() => setAdding(true)}
        >{t.occasionAddNew}</button>
      )}
    </div>
  )
}

function CardGroupCard({
  group, index, sessionId, companies, occasions, stage,
  splittingIds, splitFeedback, onSplitImage, onParsedChange, onMetaChange, onCorrection, onAddImage, onMoveImage, onSwapImages,
}: {
  group: CardGroup
  index: number
  sessionId: string
  companies: MyCompany[]
  occasions: Occasion[]
  stage: Stage
  splittingIds: Set<number>
  splitFeedback: Record<number, string>
  onSplitImage: (img: SessionImage, groupId: string) => void
  onParsedChange: (p: ParsedCard) => void
  onMetaChange: (meta: Partial<CardGroup>) => void
  onCorrection: (c: import('../components/ParsedCardEditor').CorrectionPayload) => void
  onAddImage: (groupId: string, file: File) => Promise<void>
  onMoveImage: (imgId: number, fromGroupId: string, toGroupId: string) => void
  onSwapImages: () => void
}) {
  const { t } = useLang()
  const [cropImg, setCropImg] = useState<SessionImage | null>(null)
  const [isDragOver, setIsDragOver] = useState(false)
  const canDragDrop = stage === 'grouping' || stage === 'review'

  const sideLabel = (order: number) => {
    if (order === 0) return t.sideLabels[0]
    if (order === 1) return t.sideLabels[1]
    return t.sideN(order)
  }

  return (
    <>
    <div className="rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-2 bg-gray-50 border-b border-gray-100">
        <span className="text-sm font-medium text-gray-700">{t.cardN(index + 1)}</span>
        {group.status === 'analyzing' && (
          <span className="text-xs text-blue-600 animate-pulse">{group.progress ?? t.analyzing}</span>
        )}
        {group.status === 'done' && group.parsed && (
          <ConfidenceBadge confidence={group.parsed.overall_confidence} />
        )}
        {group.matchName && (
          <span className="text-xs text-amber-600">
            {t.existingMatch(group.matchName, Math.round((group.matchConfidence ?? 0) * 100))}
          </span>
        )}
        {group.status === 'error' && (
          <span className="text-xs text-red-600">{group.error}</span>
        )}
      </div>

      <div className="p-4 flex gap-4">
        {/* Images */}
        <div
          className={`flex flex-col gap-2 shrink-0 rounded-lg transition-colors p-1 ${isDragOver ? 'bg-blue-50 ring-2 ring-blue-300' : ''}`}
          onDragOver={canDragDrop ? e => { e.preventDefault(); setIsDragOver(true) } : undefined}
          onDragLeave={canDragDrop ? e => {
            // Only clear highlight when leaving the container itself, not a child element
            if (!e.currentTarget.contains(e.relatedTarget as Node)) setIsDragOver(false)
          } : undefined}
          onDrop={canDragDrop ? e => {
            e.preventDefault()
            setIsDragOver(false)
            const imgId = parseInt(e.dataTransfer.getData('imgId'))
            const fromGroupId = e.dataTransfer.getData('fromGroupId')
            if (fromGroupId && fromGroupId !== group.tempCardId && !isNaN(imgId)) {
              onMoveImage(imgId, fromGroupId, group.tempCardId)
            }
          } : undefined}
        >
          {group.images
            .slice()
            .sort((a, b) => (a.side_order ?? 0) - (b.side_order ?? 0))
            .map(img => (
              <div
                key={img.id}
                className={`text-center relative ${canDragDrop ? 'cursor-grab active:cursor-grabbing' : ''}`}
                draggable={canDragDrop}
                onDragStart={canDragDrop ? e => {
                  e.dataTransfer.setData('imgId', String(img.id))
                  e.dataTransfer.setData('fromGroupId', group.tempCardId)
                  e.dataTransfer.effectAllowed = 'move'
                } : undefined}
              >
                <LightboxImage
                  src={`/api/v2/sessions/${sessionId}/temp/${img.image_filename}`}
                  alt={`side ${img.side_order}`}
                  className="h-28 w-auto rounded border border-gray-200 object-contain bg-gray-50"
                />
                <p className="text-xs text-gray-400 mt-1">{sideLabel(img.side_order ?? 0)}</p>
                {stage === 'grouping' && (
                  <div className="flex gap-0.5 justify-center mt-0.5">
                    <button
                      className="bg-yellow-100 text-xs px-1.5 py-0.5 rounded text-yellow-700 hover:bg-yellow-400 hover:text-gray-900 disabled:opacity-50"
                      disabled={splittingIds.has(img.id)}
                      onClick={() => onSplitImage(img, group.tempCardId)}
                      title={t.splitCards}
                    >
                      {splittingIds.has(img.id) ? '…' : '✂️'}
                    </button>
                    <button
                      className="bg-blue-100 text-xs px-1.5 py-0.5 rounded text-blue-700 hover:bg-blue-500 hover:text-white"
                      onClick={() => setCropImg(img)}
                      title="Crop image"
                    >
                      ⬚
                    </button>
                  </div>
                )}
                {splitFeedback[img.id] && (
                  <div className="absolute top-0 left-0 right-0 bg-black/70 text-white text-xs text-center py-0.5 rounded-t">
                    {splitFeedback[img.id]}
                  </div>
                )}
              </div>
            ))}
          {group.images.length === 0 && (
            <div className="h-28 w-20 rounded border-2 border-dashed border-gray-200 flex items-center justify-center">
              <span className="text-xs text-gray-400">{t.emptySlot}</span>
            </div>
          )}
          {canDragDrop && group.images.length >= 2 && (
            <button
              className="text-xs text-gray-400 hover:text-gray-600 text-center py-0.5"
              onClick={onSwapImages}
              title="Swap front and back"
            >
              ⇅ Swap
            </button>
          )}
          {/* Add Photo button — visible during grouping and review */}
          {(stage === 'grouping' || stage === 'review') && (
            <>
              <input
                type="file"
                accept="image/*"
                className="hidden"
                id={`add-photo-${group.tempCardId}`}
                onChange={async e => {
                  const file = e.target.files?.[0]
                  if (file) {
                    await onAddImage(group.tempCardId, file)
                    e.target.value = ''
                  }
                }}
              />
              <label
                htmlFor={`add-photo-${group.tempCardId}`}
                className="flex items-center justify-center gap-1 text-xs text-blue-500 hover:text-blue-700 cursor-pointer border border-dashed border-blue-300 rounded px-2 py-1 w-20"
              >
                + {t.addPhotoLabel}
              </label>
            </>
          )}
        </div>

        {/* Parsed data */}
        <div className="flex-1 min-w-0">
          {group.parsed && stage === 'review' && group.parsed.names.length === 0 && group.parsed.positions.length === 0 && (
            <div className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2 mb-2">
              {t.noCardDataHint}
            </div>
          )}
          {group.parsed && stage === 'review' && (
            <ParsedCardEditor parsed={group.parsed} onChange={onParsedChange} onCorrection={onCorrection} />
          )}
          {group.parsed && stage !== 'review' && (
            <div className="text-sm text-gray-700 space-y-1">
              <p className="font-medium">{group.parsed.names[0]?.full_name.value}</p>
              <p className="text-gray-500">{group.parsed.positions[0]?.org_names[0]?.name.value}</p>
            </div>
          )}
          {stage === 'review' && group.parsed && (
            <div className="mt-3 grid grid-cols-2 gap-2 text-xs border-t border-gray-100 pt-3">
              {/* My companies */}
              <div>
                <label className="text-gray-500 block mb-1">{t.myCompanyLabel}</label>
                <div className="flex flex-wrap gap-1">
                  {companies.map(c => (
                    <button
                      key={c.id}
                      onClick={() => onMetaChange({
                        myCompanyIds: group.myCompanyIds.includes(c.id)
                          ? group.myCompanyIds.filter(id => id !== c.id)
                          : [...group.myCompanyIds, c.id],
                      })}
                      className={`px-2 py-0.5 rounded border text-xs ${group.myCompanyIds.includes(c.id) ? 'bg-blue-600 text-white border-blue-600' : 'border-gray-300 text-gray-600'}`}
                    >
                      {c.name}
                    </button>
                  ))}
                </div>
              </div>
              {/* Occasion */}
              <OccasionPicker
                occasions={occasions}
                value={group.occasionId}
                onChange={id => onMetaChange({ occasionId: id })}
              />
              {/* Received date */}
              <div>
                <label className="text-gray-500 block mb-1">{t.receivedDateLabel}</label>
                <input
                  type="date"
                  value={group.receivedDate ?? new Date().toISOString().slice(0, 10)}
                  onChange={e => onMetaChange({ receivedDate: e.target.value })}
                  className="border border-gray-300 rounded px-2 py-0.5 text-xs"
                />
              </div>
              {/* Match override */}
              {group.matchPersonId && (
                <div>
                  <label className="text-gray-500 block mb-1">{t.existingPersonLabel}</label>
                  <div className="flex gap-1">
                    <span className="text-amber-600">{group.matchName}</span>
                    <button
                      className="text-red-400 hover:text-red-600"
                      onClick={() => onMetaChange({ matchPersonId: undefined, matchName: undefined })}
                    >{t.createNew}</button>
                  </div>
                </div>
              )}
              {/* Notes — full width */}
              <div className="col-span-2">
                <label className="text-gray-500 block mb-1">{t.notesLabel}</label>
                <textarea
                  value={group.notes ?? ''}
                  onChange={e => onMetaChange({ notes: e.target.value })}
                  placeholder={t.notesPlaceholder}
                  rows={2}
                  className="w-full border border-gray-300 rounded px-2 py-1 text-xs resize-none focus:outline-none focus:ring-1 focus:ring-blue-400"
                />
              </div>
            </div>
          )}
        </div>
      </div>
    </div>

    {cropImg && (
      <CropModal
        sessionId={sessionId}
        imgId={cropImg.id}
        imageUrl={`/api/v2/sessions/${sessionId}/temp/${cropImg.image_filename}`}
        onDone={() => {
          setCropImg(null)
          // Bust image cache so thumbnails reload with the cropped version
          const ts = Date.now()
          document.querySelectorAll<HTMLImageElement>('img').forEach(el => {
            if (el.src.includes(cropImg.image_filename)) {
              el.src = el.src.split('?')[0] + `?t=${ts}`
            }
          })
        }}
        onClose={() => setCropImg(null)}
      />
    )}
  </>
  )
}
