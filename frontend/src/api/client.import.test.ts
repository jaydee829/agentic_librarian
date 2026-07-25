import { afterEach, describe, expect, it, vi } from 'vitest'
import { commitImport, getImportJob, previewImport, retryImport } from './client'

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
