import { del, get, patch, post, uploadFile } from './client'
import type {
  AnalysisEvent,
  CardDraft,
  ConfirmedCard,
  Session,
  SessionImage,
} from '../types'

const BASE = '/api/v2/sessions'

export const createSession = (notes?: string) =>
  post<Session>(BASE, { notes })

export const getSession = (sid: string) =>
  get<Session>(`${BASE}/${sid}`)

export const uploadImage = (sid: string, file: File): Promise<SessionImage & { sha256: string }> => {
  const fd = new FormData()
  fd.append('file', file)
  return uploadFile(`${BASE}/${sid}/images`, fd)
}

export const updateImageGroup = (
  sid: string,
  imgId: number,
  tempCardId: string,
  sideOrder: number,
) => patch(`${BASE}/${sid}/images/${imgId}`, { temp_card_id: tempCardId, side_order: sideOrder })

export const abandonSession = (sid: string) =>
  del(`${BASE}/${sid}`)

export const splitImage = (
  sid: string,
  imgId: number,
): Promise<{ split: boolean; images: (SessionImage & { temp_card_id: string | null; side_order: number | null })[] }> =>
  post(`${BASE}/${sid}/images/${imgId}/split`)

export type SplitResult = { split: boolean; images: (SessionImage & { temp_card_id: string | null; side_order: number | null })[] }

export const countCards = (
  sid: string,
  imgId: number,
): Promise<{ count: number }> =>
  post(`${BASE}/${sid}/images/${imgId}/count-cards`)

export interface Point { x: number; y: number }

export const manualSplitImage = (
  sid: string,
  imgId: number,
  polygons: Point[][],
): Promise<SplitResult> =>
  post(`${BASE}/${sid}/images/${imgId}/manual-split`, { polygons })

export interface DetectCornersResult {
  corners: Point[]   // 4 points: TL, TR, BR, BL, normalized [0,1]
  confidence: number // 0.0 = fallback rectangle, 1.0 = high-confidence quad
}

export const detectCorners = (
  sid: string,
  imgId: number,
  seed: Point,
): Promise<DetectCornersResult> =>
  post(`${BASE}/${sid}/images/${imgId}/detect-corners`, { x: seed.x, y: seed.y })

export const rotateImage = (
  sid: string,
  imgId: number,
): Promise<{ id: number; image_filename: string }> =>
  post(`${BASE}/${sid}/images/${imgId}/rotate`)

export const cropImage = (
  sid: string,
  imgId: number,
  crop: { x: number; y: number; width: number; height: number; natural_width: number; natural_height: number },
): Promise<{ id: number; image_filename: string }> =>
  post(`${BASE}/${sid}/images/${imgId}/crop`, crop)

export const confirmSession = (
  sid: string,
  cards: CardDraft[],
): Promise<{ confirmed: ConfirmedCard[] }> =>
  post(`${BASE}/${sid}/confirm`, { cards })

export const logCorrection = (body: {
  card_id?: number | null
  field_path: string
  claude_value: string | null
  user_value: string
  correction_type: string
}): Promise<{ id: number }> =>
  post('/api/v2/corrections', body)

/**
 * Stream analysis events via SSE.
 * Calls onEvent for each parsed SSE message until type === 'done' or error.
 */
export async function analyzeSession(
  sid: string,
  onEvent: (e: AnalysisEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  // Use fetch + ReadableStream so we can send the Authorization header
  const apiKey = import.meta.env.VITE_API_KEY ?? ''
  const res = await fetch(`/api/v2/sessions/${sid}/analyze`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${apiKey}` },
    signal,
  })
  if (!res.ok) throw new Error(`Analyze failed: ${res.status}`)

  const reader = res.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try {
          const event: AnalysisEvent = JSON.parse(line.slice(6))
          onEvent(event)
          if (event.type === 'done') return
        } catch {
          // malformed line, skip
        }
      }
    }
  }
}
