import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

// Prevent Firebase from throwing auth/invalid-api-key under test (no env keys).
vi.mock('../auth/firebase', () => ({ getIdToken: vi.fn().mockResolvedValue(null) }))

import ImportView from './ImportView'
import * as client from '../api/client'
import { ApiError } from '../api/client'

afterEach(() => vi.restoreAllMocks())

const PREVIEW: client.ImportPreview = {
  source: 'goodreads',
  headers: ['Title', 'Author'],
  suggested_mapping: { title: 'Title', author: 'Author', date_completed: 'Date Read' },
  preview_rows: [{ title: 'Dune', author: 'Frank Herbert', format: 'ebook', date_completed: '2024-03-05', rating: 5, shelf: 'read' }],
  counts: { read_dated: 1, read_undated: 0, bad_date: 0, to_read: 1, currently_reading: 0, total: 2 },
  bad_date_example: null,
}

const BAD_DATE_PREVIEW: client.ImportPreview = {
  ...PREVIEW,
  counts: { read_dated: 0, read_undated: 0, bad_date: 2, to_read: 0, currently_reading: 0, total: 2 },
  bad_date_example: 'October 14, 2017 0:34',
}

function uploadFile() {
  const input = screen.getByTestId('import-file') as HTMLInputElement
  const file = new File(['Title,Author\nDune,Frank Herbert'], 'export.csv', { type: 'text/csv' })
  fireEvent.change(input, { target: { files: [file] } })
}

describe('ImportView', () => {
  it('previews after upload and advances to mapping', async () => {
    vi.spyOn(client, 'previewImport').mockResolvedValue(PREVIEW)
    render(<ImportView />)
    uploadFile()
    await waitFor(() => expect(screen.getByText(/Detected: goodreads/i)).toBeInTheDocument())
    expect(screen.getByText(/1 read/i)).toBeInTheDocument()
  })

  it('re-previews with the edited mapping and warns about unreadable dates', async () => {
    const spy = vi.spyOn(client, 'previewImport')
      .mockResolvedValueOnce(PREVIEW)
      .mockResolvedValueOnce(BAD_DATE_PREVIEW)
    render(<ImportView />)
    uploadFile()
    await screen.findByText(/Detected: goodreads/i)
    expect(screen.queryByText(/couldn't be read/i)).not.toBeInTheDocument()
    // FIELDS order: title, author, format, date_completed, ...
    const dateSelect = screen.getAllByRole('combobox')[3]
    fireEvent.change(dateSelect, { target: { value: 'Author' } })
    await screen.findByText(/2 of 2 rows have dates that couldn't be read/i)
    expect(screen.getByText(/October 14, 2017 0:34/)).toBeInTheDocument()
    expect(spy).toHaveBeenLastCalledWith(
      expect.any(File),
      expect.objectContaining({ date_completed: 'Author' }),
    )
  })

  it('review step notes how many rows will be skipped for unreadable dates', async () => {
    vi.spyOn(client, 'previewImport').mockResolvedValue(BAD_DATE_PREVIEW)
    render(<ImportView />)
    uploadFile()
    await screen.findByText(/Detected: goodreads/i)
    fireEvent.click(screen.getByRole('button', { name: /continue/i }))
    expect(screen.getByText(/2 rows will be skipped \(unreadable dates\)/i)).toBeInTheDocument()
  })

  it('disables Continue while a mapping re-preview is in flight', async () => {
    let resolveSecond!: (p: client.ImportPreview) => void
    vi.spyOn(client, 'previewImport')
      .mockResolvedValueOnce(PREVIEW)
      .mockImplementationOnce(() => new Promise((res) => { resolveSecond = res }))
    render(<ImportView />)
    uploadFile()
    await screen.findByText(/Detected: goodreads/i)
    fireEvent.change(screen.getAllByRole('combobox')[3], { target: { value: 'Author' } })
    expect(screen.getByRole('button', { name: /continue/i })).toBeDisabled()
    resolveSecond(BAD_DATE_PREVIEW)
    await screen.findByText(/2 of 2 rows have dates that couldn't be read/i)
    expect(screen.getByRole('button', { name: /continue/i })).toBeEnabled()
  })

  it('blocks Continue and shows an error when the re-preview fails, until one succeeds', async () => {
    vi.spyOn(client, 'previewImport')
      .mockResolvedValueOnce(PREVIEW)
      .mockRejectedValueOnce(new Error('boom'))
      .mockResolvedValueOnce(BAD_DATE_PREVIEW)
    render(<ImportView />)
    uploadFile()
    await screen.findByText(/Detected: goodreads/i)
    fireEvent.change(screen.getAllByRole('combobox')[3], { target: { value: 'Author' } })
    await screen.findByText(/couldn't refresh the preview/i)
    expect(screen.getByRole('button', { name: /continue/i })).toBeDisabled()
    // re-selecting a column retries; a successful preview clears the block
    fireEvent.change(screen.getAllByRole('combobox')[3], { target: { value: 'Title' } })
    await screen.findByText(/2 of 2 rows have dates that couldn't be read/i)
    expect(screen.getByRole('button', { name: /continue/i })).toBeEnabled()
  })

  it('commits and then polls status to completion', async () => {
    vi.spyOn(client, 'previewImport').mockResolvedValue(PREVIEW)
    vi.spyOn(client, 'commitImport').mockResolvedValue({ import_job_id: 'j1', total_rows: 2, enqueued: 2 })
    vi.spyOn(client, 'getImportJob').mockResolvedValue({
      import_job_id: 'j1', source: 'goodreads', total_rows: 2,
      counts: { done: 2 }, outcomes: { linked: 2 }, complete: true, stalled: 0, report: [],
    })
    render(<ImportView />)
    uploadFile()
    await screen.findByText(/Detected: goodreads/i)
    fireEvent.click(screen.getByRole('button', { name: /continue/i }))     // map → review
    fireEvent.click(screen.getByRole('button', { name: /start import/i })) // review → progress
    await waitFor(() => expect(screen.getByText(/2 \/ 2/)).toBeInTheDocument())
  })

  it('surfaces the server detail.message when commit is rejected over the row cap (#100)', async () => {
    vi.spyOn(client, 'previewImport').mockResolvedValue(PREVIEW)
    // #100: a persistent mockRejectedValue overrides EVERY call (vitest#1692) and would fail
    // as an "unhandled error" outside the awaited assertion — use ...Once (frontend-test-pitfalls).
    vi.spyOn(client, 'commitImport').mockRejectedValueOnce(
      new ApiError(413, 'This import has 301 rows; your current limit is 300. Split the file.'),
    )
    render(<ImportView />)
    uploadFile()
    await screen.findByText(/Detected: goodreads/i)
    fireEvent.click(screen.getByRole('button', { name: /continue/i }))     // map → review
    fireEvent.click(screen.getByRole('button', { name: /start import/i })) // review → progress
    await screen.findByText(/your current limit is 300/i)
  })

  it('offers retry when rows are stalled (not yet complete)', async () => {
    vi.spyOn(client, 'previewImport').mockResolvedValue(PREVIEW)
    vi.spyOn(client, 'commitImport').mockResolvedValue({ import_job_id: 'j1', total_rows: 2, enqueued: 2 })
    vi.spyOn(client, 'getImportJob').mockResolvedValue({
      import_job_id: 'j1', source: 'goodreads', total_rows: 2,
      counts: { processing: 1, done: 1 }, outcomes: {}, complete: false, stalled: 1, report: [],
    })
    vi.spyOn(client, 'retryImport').mockResolvedValue({ retried: 1 })
    render(<ImportView />)
    uploadFile()
    await screen.findByText(/Detected: goodreads/i)
    fireEvent.click(screen.getByRole('button', { name: /continue/i }))
    fireEvent.click(screen.getByRole('button', { name: /start import/i }))
    await waitFor(() => expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument())
  })
})
