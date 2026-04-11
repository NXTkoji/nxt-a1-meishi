// API client — all calls go through here so auth header is applied everywhere.
// Vite proxies /api → http://localhost:8000, so we never need CORS config.

const API_KEY = import.meta.env.VITE_API_KEY ?? ''

async function request<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${API_KEY}`,
      ...(init.headers ?? {}),
    },
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`${res.status} ${res.statusText}: ${text}`)
  }
  if (res.status === 204) return undefined as unknown as T
  return res.json() as Promise<T>
}

export async function get<T>(path: string): Promise<T> {
  return request<T>(path)
}

export async function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    body: body != null ? JSON.stringify(body) : undefined,
  })
}

export async function patch<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, { method: 'PATCH', body: JSON.stringify(body) })
}

export async function del(path: string): Promise<void> {
  return request<void>(path, { method: 'DELETE' })
}

/** Upload a file (multipart). No Content-Type header — browser sets boundary. */
export async function uploadFile<T>(path: string, formData: FormData): Promise<T> {
  const res = await fetch(path, {
    method: 'POST',
    headers: { Authorization: `Bearer ${API_KEY}` },
    body: formData,
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`${res.status} ${res.statusText}: ${text}`)
  }
  return res.json() as Promise<T>
}

/** Open an SSE connection. Returns an EventSource-like async generator. */
export function streamSSE(path: string): EventSource {
  // For SSE with auth header, we use a fetch-based stream.
  // Simple approach: pass api_key as query param since EventSource doesn't support headers.
  const url = `${path}${API_KEY ? `?api_key=${encodeURIComponent(API_KEY)}` : ''}`
  return new EventSource(url)
}
