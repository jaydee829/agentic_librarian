import { useEffect, useState } from 'react'
import {
  ApiError,
  deleteCredentials,
  getCredentials,
  getMyLibraries,
  putCredentials,
  searchLibraries,
  saveMyLibraries,
  type CredentialsStatus,
  type SavedLibrary,
} from '../api/client'
import './SettingsView.css'

const GEMINI_KEY_URL = 'https://aistudio.google.com/apikey'
const BYOK_SAVE_FAILED = 'Could not save your key. Please try again.'
const BYOK_REMOVE_FAILED = 'Could not remove your key. Please try again.'

export default function SettingsView() {
  const [saved, setSaved] = useState<SavedLibrary[]>([])
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SavedLibrary[]>([])
  const [status, setStatus] = useState<string>('')

  const [credentials, setCredentials] = useState<CredentialsStatus | null>(null)
  const [apiKeyInput, setApiKeyInput] = useState('')
  const [byokStatus, setByokStatus] = useState<string>('')
  const [byokBusy, setByokBusy] = useState(false)

  useEffect(() => {
    void getMyLibraries().then(setSaved)
    void getCredentials().then(setCredentials)
  }, [])

  useEffect(() => {
    const q = query.trim()
    const t = setTimeout(() => {
      if (!q) { setResults([]); return }
      void searchLibraries(q).then(setResults).catch(() => setResults([]))
    }, 300)
    return () => clearTimeout(t)
  }, [query])

  function add(lib: SavedLibrary) {
    if (!saved.some((s) => s.slug === lib.slug)) setSaved([...saved, lib])
  }
  function remove(slug: string) { setSaved(saved.filter((s) => s.slug !== slug)) }
  function move(i: number, delta: number) {
    const j = i + delta
    if (j < 0 || j >= saved.length) return
    const next = [...saved]
    ;[next[i], next[j]] = [next[j], next[i]]
    setSaved(next)
  }
  async function save() {
    setStatus('Saving…')
    try { await saveMyLibraries(saved); setStatus('Saved') } catch { setStatus('Save failed') }
  }

  async function saveKey() {
    const apiKey = apiKeyInput.trim()
    if (!apiKey) return
    setByokBusy(true)
    setByokStatus('Saving…')
    try {
      await putCredentials(apiKey)
    } catch (err) {
      setByokStatus(err instanceof ApiError ? err.detail : BYOK_SAVE_FAILED)
      setByokBusy(false)
      return
    }
    // The key is stored and the tier already flipped server-side — treat this as
    // success regardless of the refetch below. Optimistic `updated_at` covers a
    // refetch failure; a follow-up getCredentials() (own try/catch) just refines it
    // with the server's real timestamp when available.
    setApiKeyInput('')
    setCredentials({ configured: true, updated_at: new Date().toISOString() })
    setByokStatus('Key saved')
    try {
      const next = await getCredentials()
      setCredentials(next)
    } catch {
      // Keep the optimistic configured state — the save itself already succeeded.
    } finally {
      setByokBusy(false)
    }
  }

  async function removeKey() {
    setByokBusy(true)
    setByokStatus('Removing…')
    try {
      await deleteCredentials()
      setCredentials({ configured: false, updated_at: null })
      setByokStatus('Key removed')
    } catch (err) {
      setByokStatus(err instanceof ApiError ? err.detail : BYOK_REMOVE_FAILED)
    } finally {
      setByokBusy(false)
    }
  }

  return (
    <>
      <div className="settings">
        <header className="view-head">
          <h2>Libraries</h2>
          <div className="settings__actions">
            <button className="btn" onClick={() => void save()}>Save</button>
            {status && <span className="settings__status" aria-live="polite">{status}</span>}
          </div>
        </header>
        <p className="settings__hint">Search for the library systems you have a Libby card for. We'll show live
          availability for these on your recommendations, in your priority order.</p>

        <ul className="settings__saved">
          {saved.map((lib, i) => (
            <li key={lib.slug} className="settings__saved-row">
              <span>{lib.name}</span>
              <span className="settings__controls">
                <button className="btn btn--ghost" onClick={() => move(i, -1)} aria-label="Move up">↑</button>
                <button className="btn btn--ghost" onClick={() => move(i, 1)} aria-label="Move down">↓</button>
                <button className="btn btn--ghost" onClick={() => remove(lib.slug)}>Remove</button>
              </span>
            </li>
          ))}
        </ul>

        <input className="settings__search" placeholder="Search for your library…"
               aria-label="Search for your library"
               value={query} onChange={(e) => setQuery(e.target.value)} />
        <ul className="settings__results">
          {results.map((lib) => (
            <li key={lib.slug} className="settings__result-row">
              <span>{lib.name}</span>
              <button className="btn" onClick={() => add(lib)}>Add</button>
            </li>
          ))}
        </ul>
      </div>

      <div className="settings settings__byok">
        <header className="view-head">
          <h2>Your API key</h2>
          <div className="settings__actions">
            {credentials?.configured ? (
              <button className="btn btn--ghost" onClick={() => void removeKey()} disabled={byokBusy}>
                Remove key
              </button>
            ) : (
              <button className="btn" onClick={() => void saveKey()} disabled={byokBusy || !apiKeyInput.trim()}>
                Save key
              </button>
            )}
            {byokStatus && <span className="settings__status" aria-live="polite">{byokStatus}</span>}
          </div>
        </header>

        {credentials?.configured ? (
          <p className="settings__hint">
            Using your own key since{' '}
            {credentials.updated_at ? new Date(credentials.updated_at).toLocaleDateString() : 'recently'}.
          </p>
        ) : (
          <>
            <ol className="settings__hint settings__byok-steps">
              <li>
                <a href={GEMINI_KEY_URL} target="_blank" rel="noopener noreferrer">
                  Create a free Gemini API key
                </a>
              </li>
              <li>Paste it below.</li>
              <li>Save — we verify it works before storing it.</li>
            </ol>
            <input
              className="settings__search"
              type="password"
              autoComplete="off"
              aria-label="Gemini API key"
              placeholder="Paste your Gemini API key"
              value={apiKeyInput}
              onChange={(e) => setApiKeyInput(e.target.value)}
            />
          </>
        )}
      </div>
    </>
  )
}
