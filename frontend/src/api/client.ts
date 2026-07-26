import { getIdToken } from '../auth/firebase'
import type { ActivityStep } from './activityLabels'

// All user-facing API routes are served under /api/* (#151) so they can never collide
// with an SPA client route. This is the single place the prefix is applied — every call
// site keeps its bare literal path (e.g. '/history').
const API_PREFIX = '/api'

export class ApiError extends Error {
  // Explicit fields, not constructor parameter properties: the build runs tsc with
  // erasableSyntaxOnly, which rejects TS-only runtime syntax (TS1294).
  status: number
  detail: string

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

export interface HistoryItem {
  id: string
  title: string
  authors: string[]
  date_completed: string | null
  rating: number | null
  format: string | null
  notes?: string | null
  genre?: string | null
  tropes?: string[]
  enrichment_enqueued?: boolean
}

export interface Recommendation {
  id: string
  work_id: string
  title: string
  authors: string[]
  justification: string | null
  context: string | null
  suggested_at: string | null
  status: string
  read_status?: 'new' | 'reread'
  last_read?: string | null
  rating?: number | null
  genres?: string[]
}

export interface Ranked {
  name: string
  count: number
}

export type StyleAxis =
  | 'pace' | 'density' | 'depth' | 'inner_focus'
  | 'humor' | 'warmth' | 'lexicon' | 'world_building'

export type StyleRadar = Record<StyleAxis, number | null>

export interface Analysis {
  snapshot: {
    total_read: number
    read_this_year: number
    average_rating: number | null
    distinct_authors: number
    formats: Ranked[]
  }
  genres: Ranked[]
  moods: Ranked[]
  top_tropes: Ranked[]
  authors: Ranked[]
  narrators: Ranked[]
  style_radar?: StyleRadar
  style_cloud?: Ranked[]
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  steps?: ActivityStep[]
}

export interface Conversation {
  id: string
  messages: ChatMessage[]
}

export interface BookLink {
  kind: 'libby' | 'hoopla' | 'bookshop' | 'amazon'
  label: string
  url: string
}

export interface LibbyFormat {
  format: string
  available: boolean
  copies_owned: number | null
  copies_available: number | null
  holds_ratio: number | null
  wait_days: number | null
}

export interface LibbyAvailability {
  library: string
  slug: string
  formats: LibbyFormat[]
}

export interface BookAvailability {
  links: BookLink[]
  libby: LibbyAvailability[]
}

export interface SavedLibrary {
  slug: string
  name: string
}

export interface Account {
  email: string
  display_name: string | null
  tier: 'free' | 'supporter' | 'byok'
  subscriber_until: string | null
}

export function getAccount(): Promise<Account> {
  return getJson<Account>('/account')
}

/** fetch with the Firebase ID token attached. */
async function authedFetchRaw(path: string, init: RequestInit = {}): Promise<Response> {
  const token = await getIdToken()
  const headers = new Headers(init.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  return fetch(`${API_PREFIX}${path}`, { ...init, headers })
}

async function getJson<T>(path: string): Promise<T> {
  const res = await authedFetchRaw(path)
  if (!res.ok) throw new Error(`${path} → ${res.status}`)
  return res.json() as Promise<T>
}

/** One lightweight authed call to decide invited-or-not. 200 → ready, 403 → notInvited,
 *  anything else → error (treated as not-ready by the caller). */
export async function probeAccess(): Promise<'ready' | 'notInvited' | 'error'> {
  const res = await authedFetchRaw('/conversations/current')
  if (res.ok) return 'ready'
  if (res.status === 403) return 'notInvited'
  return 'error'
}

export function getHistory(limit = 50, offset = 0): Promise<HistoryItem[]> {
  return getJson<HistoryItem[]>(`/history?limit=${limit}&offset=${offset}`)
}

export async function deleteHistory(id: string): Promise<void> {
  const res = await authedFetchRaw(`/history/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`delete history → ${res.status}`)
}

export interface HistoryUpdate {
  date_completed?: string | null
  rating?: number | null
  notes?: string | null
  format?: string
}

export async function updateHistory(id: string, body: HistoryUpdate): Promise<HistoryItem> {
  const res = await authedFetchRaw(`/history/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    let detail = `update history → ${res.status}`
    try {
      const parsed = await res.json()
      if (typeof parsed?.detail === 'string') detail = parsed.detail
    } catch {
      // non-JSON error body — keep the generic detail
    }
    throw new ApiError(res.status, detail)
  }
  return res.json()
}

export function getRecommendations(): Promise<Recommendation[]> {
  return getJson<Recommendation[]>('/recommendations')
}

export async function setRecommendationStatus(id: string, status: 'Dismissed' | 'Read' | 'Removed'): Promise<void> {
  const res = await authedFetchRaw(`/recommendations/${id}/status`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status }),
  })
  if (!res.ok) throw new Error(`dismiss → ${res.status}`)
}

export function getAnalysis(): Promise<Analysis> {
  return getJson<Analysis>('/analysis')
}

export function getCurrentConversation(): Promise<Conversation> {
  return getJson<Conversation>('/conversations/current')
}

export async function newConversation(): Promise<Conversation> {
  const res = await authedFetchRaw('/conversations', { method: 'POST' })
  if (!res.ok) throw new Error(`new conversation → ${res.status}`)
  return res.json() as Promise<Conversation>
}

export interface AddBookInput {
  title: string
  author: string
  format?: string
  rating?: number | null
  notes?: string | null
  date_completed?: string | null
}

export interface AddBookResult {
  work_id: string
  title: string
  read_number: number
  already_logged: boolean
  enrichment_enqueued: boolean
  pick_resolved: boolean
}

export async function addBook(input: AddBookInput): Promise<AddBookResult> {
  const res = await authedFetchRaw('/books', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  if (!res.ok) throw new Error(`addBook → ${res.status}`)
  return res.json() as Promise<AddBookResult>
}

export interface ChatHandlers {
  onActivity: (kind: string, detail: string) => void
  onText: (text: string) => void
  onError: (detail: string) => void
  signal?: AbortSignal
}

const GENERIC_CHAT_ERROR = 'The Librarian hit a problem. Please try again.'

/** POST a chat turn and consume the SSE stream (EventSource cannot POST or set headers,
 *  so we use fetch + a streaming reader). Parses event frames split on a blank line and
 *  buffers across network chunk boundaries. Events: activity, text, error, done. */
export async function streamChat(message: string, handlers: ChatHandlers): Promise<void> {
  let res: Response
  try {
    res = await authedFetchRaw('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
      signal: handlers.signal,
    })
  } catch {
    handlers.onError(GENERIC_CHAT_ERROR)
    return
  }
  if (!res.ok || !res.body) {
    if (res.status === 429 || res.status === 422 || res.status === 409) {
      let message = ''
      try {
        const body = await res.json()
        message = body?.detail?.message ?? ''
      } catch {
        // fall through to the generic copy
      }
      handlers.onError(message || GENERIC_CHAT_ERROR)
      return
    }
    handlers.onError(GENERIC_CHAT_ERROR)
    return
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let sep: number
      while ((sep = buffer.indexOf('\n\n')) !== -1) {
        const frame = buffer.slice(0, sep)
        buffer = buffer.slice(sep + 2)
        dispatchFrame(frame, handlers)
      }
    }
  } catch {
    // A mid-stream read failure (e.g. the connection dropped) ends the turn as one error.
    handlers.onError(GENERIC_CHAT_ERROR)
  } finally {
    reader.releaseLock()
  }
}

export type ColumnMapping = Partial<Record<
  'title' | 'author' | 'format' | 'date_completed' | 'rating' | 'notes' | 'shelf',
  string | null
>>

export interface ImportPreview {
  source: 'goodreads' | 'generic'
  headers: string[]
  suggested_mapping: ColumnMapping
  preview_rows: Array<{
    title: string; author: string; format: string
    date_completed: string | null; rating: number | null; shelf: string
  }>
  counts: {
    read_dated: number; read_undated: number; bad_date: number
    to_read: number; currently_reading: number; total: number
  }
  bad_date_example: string | null
}

export interface ImportCommitResult {
  import_job_id: string
  total_rows: number
  enqueued: number
}

export interface ImportStatus {
  import_job_id: string
  source: string
  total_rows: number
  counts: Record<string, number>
  outcomes: Record<string, number>
  complete: boolean
  stalled: number
  report: Array<{
    title: string | null; author: string | null; status: string
    outcome: string | null; skip_reason: string | null; error: string | null
  }>
}

export async function previewImport(file: File, mapping?: ColumnMapping): Promise<ImportPreview> {
  const form = new FormData()
  form.set('file', file)
  if (mapping) form.set('mapping', JSON.stringify(mapping))
  const res = await authedFetchRaw('/import/preview', { method: 'POST', body: form })
  if (!res.ok) throw new Error(`preview import → ${res.status}`)
  return res.json() as Promise<ImportPreview>
}

export async function commitImport(
  file: File,
  mapping: ColumnMapping,
  opts: { importToRead: boolean; importCurrentlyReading: boolean },
): Promise<ImportCommitResult> {
  const form = new FormData()
  form.set('file', file)
  form.set('mapping', JSON.stringify(mapping))
  form.set('import_to_read', String(opts.importToRead))
  form.set('import_currently_reading', String(opts.importCurrentlyReading))
  form.set('original_filename', file.name)
  const res = await authedFetchRaw('/import/commit', { method: 'POST', body: form })
  if (!res.ok) {
    // #100: 413 import_rows_limit / 409 import_in_flight carry a human message in
    // detail.message (updateHistory's ApiError pattern, adapted for the object-shaped
    // detail the chat 429 path also uses — see streamChat above).
    let detail = `commit import → ${res.status}`
    try {
      const parsed = await res.json()
      if (typeof parsed?.detail === 'string') detail = parsed.detail
      else if (typeof parsed?.detail?.message === 'string') detail = parsed.detail.message
    } catch {
      // non-JSON error body — keep the generic detail
    }
    throw new ApiError(res.status, detail)
  }
  return res.json() as Promise<ImportCommitResult>
}

export function getImportJob(jobId: string): Promise<ImportStatus> {
  return getJson<ImportStatus>(`/import/${jobId}`)
}

export async function retryImport(jobId: string): Promise<{ retried: number }> {
  const res = await authedFetchRaw(`/import/${jobId}/retry`, { method: 'POST' })
  if (!res.ok) throw new Error(`retry import → ${res.status}`)
  return res.json() as Promise<{ retried: number }>
}

export async function getAvailability(workIds: string[]): Promise<Record<string, BookAvailability>> {
  const res = await authedFetchRaw('/availability', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ work_ids: workIds }),
  })
  if (!res.ok) throw new Error(`availability → ${res.status}`)
  return res.json() as Promise<Record<string, BookAvailability>>
}

export function searchLibraries(q: string): Promise<SavedLibrary[]> {
  return getJson<SavedLibrary[]>(`/libraries/search?q=${encodeURIComponent(q)}`)
}

export async function getMyLibraries(): Promise<SavedLibrary[]> {
  const data = await getJson<{ libraries: SavedLibrary[] }>('/me/libraries')
  return data.libraries
}

export async function saveMyLibraries(libraries: SavedLibrary[]): Promise<void> {
  const res = await authedFetchRaw('/me/libraries', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ libraries }),
  })
  if (!res.ok) throw new Error(`save libraries → ${res.status}`)
}

export interface CredentialsStatus {
  configured: boolean
  updated_at: string | null
}

export function getCredentials(): Promise<CredentialsStatus> {
  return getJson<CredentialsStatus>('/me/credentials')
}

export async function putCredentials(apiKey: string): Promise<void> {
  const res = await authedFetchRaw('/me/credentials', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ api_key: apiKey }),
  })
  if (!res.ok) {
    // Error bodies here are {"detail": {"code": ..., "message": ...}} — the same
    // object-shaped detail as the chat 429/422/409 path and commitImport's 413/409.
    let detail = `/me/credentials → ${res.status}`
    try {
      const parsed = await res.json()
      if (typeof parsed?.detail?.message === 'string') detail = parsed.detail.message
    } catch {
      // non-JSON error body — keep the generic detail
    }
    throw new ApiError(res.status, detail)
  }
}

export async function deleteCredentials(): Promise<void> {
  const res = await authedFetchRaw('/me/credentials', { method: 'DELETE' })
  if (!res.ok) throw new Error(`/me/credentials → ${res.status}`)
}

function dispatchFrame(frame: string, handlers: ChatHandlers): void {
  let event = 'message'
  let data = ''
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) {
      event = line.slice(6).trim()
    } else if (line.startsWith('data:')) {
      // SSE joins multiple data: lines within one event with a newline.
      const content = line.slice(5).trim()
      data = data ? `${data}\n${content}` : content
    }
  }
  let payload: Record<string, string> = {}
  if (data) {
    try {
      payload = JSON.parse(data)
    } catch {
      return // ignore an unparseable frame rather than crash the stream
    }
  }
  if (event === 'activity') handlers.onActivity(payload.kind ?? '', payload.detail ?? '')
  else if (event === 'text') handlers.onText(payload.text ?? '')
  else if (event === 'error') handlers.onError(payload.detail ?? GENERIC_CHAT_ERROR)
  // 'done' → stream end, no callback
}
