import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, commitImport, getImportJob, previewImport, retryImport } from './client'

vi.mock('../auth/firebase', () => ({ getIdToken: async () => 'tok' }))

afterEach(() => vi.restoreAllMocks())

function mockFetch(body: unknown, ok = true) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValue({
    ok,
    status: ok ? 200 : 422,
    json: async () => body,
  } as Response)
}

describe('import client', () => {
  it('previewImport posts the file as multipart', async () => {
    const f = mockFetch({ source: 'goodreads', counts: { total: 1 } })
    const file = new File(['Title\nDune'], 'export.csv', { type: 'text/csv' })
    const res = await previewImport(file)
    expect(res.source).toBe('goodreads')
    const [path, init] = f.mock.calls[0]
    expect(path).toBe('/api/import/preview')
    expect((init as RequestInit).method).toBe('POST')
    expect((init as RequestInit).body).toBeInstanceOf(FormData)
  })

  it('commitImport sends mapping + opt-ins', async () => {
    const f = mockFetch({ import_job_id: 'j1', total_rows: 3, enqueued: 2 })
    const file = new File(['x'], 'export.csv', { type: 'text/csv' })
    const res = await commitImport(file, { title: 'Title' }, { importToRead: true, importCurrentlyReading: false })
    expect(res.import_job_id).toBe('j1')
    const body = (f.mock.calls[0][1] as RequestInit).body as FormData
    expect(body.get('import_to_read')).toBe('true')
    expect(body.get('import_currently_reading')).toBe('false')
  })

  it('commitImport surfaces the server detail.message on 413 as an ApiError', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: false,
      status: 413,
      json: async () => ({
        detail: {
          code: 'import_rows_limit',
          message: 'This import has 301 rows; your current limit is 300. Split the file.',
        },
      }),
    } as Response)
    const file = new File(['x'], 'export.csv', { type: 'text/csv' })
    let caught: unknown
    try {
      await commitImport(file, { title: 'Title' }, { importToRead: true, importCurrentlyReading: false })
    } catch (e) {
      caught = e
    }
    expect(caught).toBeInstanceOf(ApiError)
    expect((caught as InstanceType<typeof ApiError>).status).toBe(413)
    expect((caught as InstanceType<typeof ApiError>).detail).toContain('current limit is 300')
  })

  it('commitImport surfaces the server detail.message on 409 as an ApiError', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({
        detail: { code: 'import_in_flight', message: 'Your previous import is still running.' },
      }),
    } as Response)
    const file = new File(['x'], 'export.csv', { type: 'text/csv' })
    let caught: unknown
    try {
      await commitImport(file, { title: 'Title' }, { importToRead: true, importCurrentlyReading: false })
    } catch (e) {
      caught = e
    }
    expect(caught).toBeInstanceOf(ApiError)
    expect((caught as InstanceType<typeof ApiError>).status).toBe(409)
    expect((caught as InstanceType<typeof ApiError>).detail).toBe('Your previous import is still running.')
  })

  it('getImportJob fetches status', async () => {
    mockFetch({ import_job_id: 'j1', complete: true, counts: { done: 3 } })
    const res = await getImportJob('j1')
    expect(res.complete).toBe(true)
  })

  it('retryImport posts to the retry route', async () => {
    const f = mockFetch({ retried: 2 })
    await retryImport('j1')
    expect(f.mock.calls[0][0]).toBe('/api/import/j1/retry')
  })
})
