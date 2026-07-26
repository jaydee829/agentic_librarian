import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../auth/firebase', () => ({
  getIdToken: vi.fn(),
}))

import { getIdToken } from '../auth/firebase'
import { addBook, getAvailability, probeAccess, searchLibraries, streamChat } from './client'

function sseStream(chunks: string[]): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      const enc = new TextEncoder()
      for (const c of chunks) controller.enqueue(enc.encode(c))
      controller.close()
    },
  })
  return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
}

describe('api client', () => {
  beforeEach(() => {
    vi.mocked(getIdToken).mockResolvedValue('tok-123')
    vi.stubGlobal('fetch', vi.fn())
  })
  afterEach(() => vi.unstubAllGlobals())

  it('attaches the bearer token on requests', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response('ok', { status: 200 }))
    await probeAccess()
    const [, init] = vi.mocked(fetch).mock.calls[0]
    expect((init!.headers as Headers).get('Authorization')).toBe('Bearer tok-123')
  })

  it('probeAccess maps 200 → ready and 403 → notInvited', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(new Response('{}', { status: 200 }))
    expect(await probeAccess()).toBe('ready')
    vi.mocked(fetch).mockResolvedValueOnce(new Response('no', { status: 403 }))
    expect(await probeAccess()).toBe('notInvited')
  })

  it('streamChat parses activity, text, done across chunk boundaries', async () => {
    // The "text" event is split across two network chunks to prove buffering.
    vi.mocked(fetch).mockResolvedValue(
      sseStream([
        'event: activity\ndata: {"kind":"search","detail":"Explorer is searching"}\n\n',
        'event: text\ndata: {"text":"Hello',
        ' there"}\n\nevent: done\ndata: {}\n\n',
      ]),
    )
    const activity: string[] = []
    let text = ''
    let errored = false
    await streamChat('hi', {
      onActivity: (_k, d) => activity.push(d),
      onText: (t) => (text += t),
      onError: () => (errored = true),
    })
    expect(activity).toEqual(['Explorer is searching'])
    expect(text).toBe('Hello there')
    expect(errored).toBe(false)
  })

  it('streamChat reports a single error event', async () => {
    vi.mocked(fetch).mockResolvedValue(sseStream(['event: error\ndata: {"detail":"boom"}\n\n']))
    let detail = ''
    await streamChat('hi', { onActivity: () => {}, onText: () => {}, onError: (d) => (detail = d) })
    expect(detail).toBe('boom')
  })

  it('streamChat surfaces the 429 quota detail message', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: { code: 'chat_quota', message: 'Daily limit reached.' } }), {
        status: 429,
      }),
    )
    let detail = ''
    await streamChat('hi', { onActivity: () => {}, onText: () => {}, onError: (d) => (detail = d) })
    expect(detail).toBe('Daily limit reached.')
  })

  it('streamChat surfaces the 422 message-too-long detail message', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: { code: 'message_too_long', message: 'Message too long.' } }), {
        status: 422,
      }),
    )
    let detail = ''
    await streamChat('hi', { onActivity: () => {}, onText: () => {}, onError: (d) => (detail = d) })
    expect(detail).toBe('Message too long.')
  })

  it('streamChat falls back to the generic copy on a 500', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(new Response('boom', { status: 500 }))
    let detail = ''
    await streamChat('hi', { onActivity: () => {}, onText: () => {}, onError: (d) => (detail = d) })
    expect(detail).toBe('The Librarian hit a problem. Please try again.')
  })
})

describe('addBook', () => {
  beforeEach(() => {
    vi.mocked(getIdToken).mockResolvedValue('tok-123')
  })
  afterEach(() => vi.unstubAllGlobals())

  it('POSTs the form and returns the result', async () => {
    const body = {
      work_id: 'w1', title: 'Project Hail Mary',
      read_number: 1, already_logged: false, enrichment_enqueued: true,
    }
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const result = await addBook({ title: 'Project Hail Mary', author: 'Andy Weir', format: 'ebook', rating: 5 })

    expect(result).toEqual(body)
    const [path, init] = fetchMock.mock.calls[0]
    expect(path).toBe('/api/books')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toMatchObject({ title: 'Project Hail Mary', author: 'Andy Weir' })
  })

  it('throws on a 404 (book not found)', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('nope', { status: 404 })))
    await expect(addBook({ title: 'X', author: 'Y' })).rejects.toThrow()
  })
})

describe('availability client', () => {
  beforeEach(() => { vi.restoreAllMocks() })

  it('posts work_ids and returns the availability map', async () => {
    const map = { w1: { links: [{ kind: 'amazon', label: 'Amazon', url: 'u' }], libby: [] } }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      { ok: true, json: () => Promise.resolve(map) } as Response))
    const out = await getAvailability(['w1'])
    expect(out.w1.links[0].kind).toBe('amazon')
  })

  it('searchLibraries hits the directory endpoint', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      { ok: true, json: () => Promise.resolve([{ slug: 'kcls', name: 'KCLS' }]) } as Response))
    const out = await searchLibraries('king')
    expect(out[0].slug).toBe('kcls')
  })
})
