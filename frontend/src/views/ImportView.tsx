import { useEffect, useRef, useState } from 'react'
import {
  ApiError, commitImport, getImportJob, previewImport, retryImport,
  type ColumnMapping, type ImportPreview, type ImportStatus,
} from '../api/client'
import './ImportView.css'

type Step = 'upload' | 'map' | 'review' | 'progress'
const FIELDS: Array<keyof ColumnMapping> = ['title', 'author', 'format', 'date_completed', 'rating', 'notes', 'shelf']
const REQUIRED: Array<keyof ColumnMapping> = ['title', 'author', 'date_completed']

export default function ImportView() {
  const [step, setStep] = useState<Step>('upload')
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<ImportPreview | null>(null)
  const [mapping, setMapping] = useState<ColumnMapping>({})
  const [toRead, setToRead] = useState(true)
  const [currently, setCurrently] = useState(true)
  const [jobId, setJobId] = useState<string | null>(null)
  const [status, setStatus] = useState<ImportStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [pollKey, setPollKey] = useState(0)

  async function onFile(f: File) {
    setFile(f)
    setBusy(true)
    setError(null)
    try {
      const p = await previewImport(f)
      setPreview(p)
      setMapping(p.suggested_mapping)
      setStep('map')
    } catch {
      setError('Could not read that file. Make sure it is a CSV with a header row.')
    } finally {
      setBusy(false)
    }
  }

  const missing = REQUIRED.filter((f) => !mapping[f])

  // Re-preview on every mapping edit so counts and the bad-date warning reflect what
  // commit will actually do. Last-request-wins guards out-of-order responses; Continue is
  // blocked while a preview is in flight or stale (a failed refresh would otherwise let
  // the user proceed on counts computed from a previous mapping).
  const previewSeq = useRef(0)
  const [previewStale, setPreviewStale] = useState(false)
  async function onMappingChange(field: keyof ColumnMapping, value: string | null) {
    const next = { ...mapping, [field]: value }
    setMapping(next)
    if (!file) return
    const seq = ++previewSeq.current
    setBusy(true)
    setError(null)
    try {
      const p = await previewImport(file, next)
      if (seq === previewSeq.current) {
        setPreview(p)
        setPreviewStale(false)
        setBusy(false)
      }
    } catch {
      if (seq === previewSeq.current) {
        setPreviewStale(true)
        setBusy(false)
        setError("Couldn't refresh the preview for that mapping — re-select a column to retry.")
      }
    }
  }

  async function onCommit() {
    if (!file) return
    setBusy(true)
    setError(null)
    try {
      const res = await commitImport(file, mapping, { importToRead: toRead, importCurrentlyReading: currently })
      setJobId(res.import_job_id)
      setStep('progress')
    } catch (e) {
      // #100: 413 import_rows_limit / 409 import_in_flight carry a human message worth
      // showing verbatim; anything else falls back to the generic copy.
      setError(e instanceof ApiError ? e.detail : 'Import could not start. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  async function onRetry() {
    if (!jobId || busy) return
    setBusy(true)
    setError(null)
    try {
      await retryImport(jobId)
      setStatus(null)
      setPollKey((k) => k + 1) // restart polling so progress reappears after retry
    } catch {
      setError('Retry could not start. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  const timer = useRef<number | null>(null)
  useEffect(() => {
    if (step !== 'progress' || !jobId) return
    let active = true
    async function tick() {
      try {
        const s = await getImportJob(jobId!)
        if (!active) return
        setStatus(s)
        if (!s.complete) timer.current = window.setTimeout(tick, 2000)
      } catch {
        if (active) timer.current = window.setTimeout(tick, 4000)
      }
    }
    tick()
    return () => {
      active = false
      if (timer.current) window.clearTimeout(timer.current)
    }
  }, [step, jobId, pollKey])

  function downloadReport() {
    if (!status) return
    const header = 'title,author,status,outcome,skip_reason,error\n'
    const body = status.report
      .map((r) => [r.title, r.author, r.status, r.outcome, r.skip_reason, r.error]
        .map((v) => `"${(v ?? '').toString().replace(/"/g, '""')}"`).join(','))
      .join('\n')
    const url = URL.createObjectURL(new Blob([header + body], { type: 'text/csv' }))
    const a = document.createElement('a')
    a.href = url
    a.download = 'import-report.csv'
    a.click()
    URL.revokeObjectURL(url)
  }

  const done = (status?.counts.done ?? 0) + (status?.counts.failed ?? 0) + (status?.counts.skipped ?? 0)

  return (
    <div className="import">
      <h2>Import reading history</h2>
      {error && <p className="import-error">{error}</p>}

      {step === 'upload' && (
        <div className="import-step">
          <p>Upload a CSV — a Goodreads export, or your own with title, author and date columns.</p>
          <label className="import-file-label">
            Choose CSV file
            <input
              data-testid="import-file"
              type="file"
              accept=".csv,text/csv"
              onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])}
            />
          </label>
        </div>
      )}

      {step === 'map' && preview && (
        <div className="import-step">
          <p>Detected: {preview.source}</p>
          <div className="import-counts">
            <span>{preview.counts.read_dated} read</span>
            <span>{preview.counts.to_read} to-read</span>
            <span>{preview.counts.currently_reading} currently-reading</span>
          </div>
          {preview.counts.bad_date > 0 && (
            <p className="import-warning">
              ⚠ {preview.counts.bad_date} of {preview.counts.total} rows have dates that couldn't be read
              {preview.bad_date_example ? <> (e.g. "{preview.bad_date_example}")</> : null}.
              These rows will be skipped — pick a different column or fix the dates in your file.
            </p>
          )}
          <div className="import-map-fields">
            {FIELDS.map((field) => (
              <label key={field}>
                {field}
                <select
                  value={mapping[field] ?? ''}
                  onChange={(e) => onMappingChange(field, e.target.value || null)}
                >
                  <option value="">—</option>
                  {preview.headers.map((h) => <option key={h} value={h}>{h}</option>)}
                </select>
              </label>
            ))}
          </div>
          {missing.length > 0 && <p className="import-error">Map required columns: {missing.join(', ')}</p>}
          <div className="import-actions">
            <button className="btn" disabled={missing.length > 0 || busy || previewStale} onClick={() => setStep('review')}>Continue</button>
          </div>
        </div>
      )}

      {step === 'review' && preview && (
        <div className="import-step">
          <p>{preview.counts.read_dated} books will be added to your history.</p>
          {preview.counts.bad_date > 0 && (
            <p className="import-warning">
              ⚠ {preview.counts.bad_date} rows will be skipped (unreadable dates). Go back to adjust
              the date column, or continue without them.
            </p>
          )}
          <label className="import-check-label">
            <input type="checkbox" checked={toRead} onChange={(e) => setToRead(e.target.checked)} />
            Import {preview.counts.to_read} to-read books as wishlist
          </label>
          <label className="import-check-label">
            <input type="checkbox" checked={currently} onChange={(e) => setCurrently(e.target.checked)} />
            Import {preview.counts.currently_reading} currently-reading as wishlist
          </label>
          <div className="import-actions">
            <button className="btn btn--ghost" onClick={() => setStep('map')}>Back</button>
            <button className="btn" disabled={busy} onClick={onCommit}>Start import</button>
          </div>
        </div>
      )}

      {step === 'progress' && (
        <div className="import-step">
          <div className="import-progress-bar">
            <span style={{ width: `${status && status.total_rows ? (done / status.total_rows) * 100 : 0}%` }} />
          </div>
          <p className="import-progress-count">{done} / {status?.total_rows ?? '…'}</p>
          {status && (
            <ul className="import-status-list">
              <li className="import-status-done">✓ {status.counts.done ?? 0} imported</li>
              <li className="import-status-failed">⚠ {status.counts.failed ?? 0} failed</li>
              <li className="import-status-skipped">⏭ {status.counts.skipped ?? 0} skipped</li>
            </ul>
          )}
          {status && (status.stalled ?? 0) > 0 && (
            <p className="import-error">{status.stalled} row(s) appear stuck. You can retry them.</p>
          )}
          <div className="import-actions">
            {status && ((status.counts.failed ?? 0) > 0 || (status.stalled ?? 0) > 0) && (
              <button className="btn btn--danger" disabled={busy} onClick={onRetry}>Retry failed/stalled</button>
            )}
            {status?.complete && status.report.length > 0 && (
              <button className="btn btn--ghost" onClick={downloadReport}>Download report</button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
