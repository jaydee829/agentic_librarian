import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import SettingsView from './SettingsView'
import { ApiError } from '../api/client'
import * as client from '../api/client'

vi.mock('../auth/firebase', () => ({ getIdToken: vi.fn().mockResolvedValue(null) }))
// Preserve the real ApiError class (automocking a class export replaces its constructor
// body, so `new ApiError(status, detail)` in tests would lose `.status`/`.detail`).
vi.mock('../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/client')>()),
  getMyLibraries: vi.fn(),
  searchLibraries: vi.fn(),
  saveMyLibraries: vi.fn(),
  getCredentials: vi.fn(),
  putCredentials: vi.fn(),
  deleteCredentials: vi.fn(),
}))

describe('SettingsView', () => {
  beforeEach(() => {
    vi.mocked(client.getMyLibraries).mockResolvedValue([{ slug: 'kcls', name: 'KCLS' }])
    vi.mocked(client.searchLibraries).mockResolvedValue([{ slug: 'spl', name: 'Seattle PL' }])
    vi.mocked(client.saveMyLibraries).mockResolvedValue(undefined)
    vi.mocked(client.getCredentials).mockResolvedValue({ configured: false, updated_at: null })
    vi.mocked(client.putCredentials).mockResolvedValue(undefined)
    vi.mocked(client.deleteCredentials).mockResolvedValue(undefined)
  })

  it('loads and shows saved libraries', async () => {
    render(<SettingsView />)
    expect(await screen.findByText('KCLS')).toBeInTheDocument()
  })

  it('searches, adds, and saves a library', async () => {
    render(<SettingsView />)
    fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: 'seattle' } })
    fireEvent.click(await screen.findByText(/add/i))
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(client.saveMyLibraries).toHaveBeenCalled())
    const saved = vi.mocked(client.saveMyLibraries).mock.calls[0][0]
    expect(saved.map((l) => l.slug)).toContain('spl')
  })
})

describe('SettingsView — Your API key', () => {
  beforeEach(() => {
    vi.mocked(client.getMyLibraries).mockResolvedValue([])
    vi.mocked(client.searchLibraries).mockResolvedValue([])
    vi.mocked(client.saveMyLibraries).mockResolvedValue(undefined)
  })

  it('links "Create a free Gemini API key" to aistudio.google.com/apikey, opening safely in a new tab', async () => {
    vi.mocked(client.getCredentials).mockResolvedValue({ configured: false, updated_at: null })
    render(<SettingsView />)
    const link = await screen.findByRole('link', { name: /create a free gemini api key/i })
    expect(link).toHaveAttribute('href', 'https://aistudio.google.com/apikey')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link.getAttribute('rel')).toContain('noopener')
    expect(link.getAttribute('rel')).toContain('noreferrer')
  })

  it('renders the key paste field as a password input with autocomplete off', async () => {
    vi.mocked(client.getCredentials).mockResolvedValue({ configured: false, updated_at: null })
    render(<SettingsView />)
    const field = await screen.findByLabelText(/gemini api key/i)
    expect(field).toHaveAttribute('type', 'password')
    expect(field).toHaveAttribute('autoComplete', 'off')
  })

  it('saves a new key and shows the configured state from a fresh getCredentials call', async () => {
    vi.mocked(client.getCredentials)
      .mockResolvedValueOnce({ configured: false, updated_at: null })
      .mockResolvedValueOnce({ configured: true, updated_at: '2026-07-20T00:00:00Z' })
    vi.mocked(client.putCredentials).mockResolvedValueOnce(undefined)
    render(<SettingsView />)

    fireEvent.change(await screen.findByLabelText(/gemini api key/i), { target: { value: 'AIza-fake-key' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save key' }))

    await waitFor(() => expect(client.putCredentials).toHaveBeenCalledWith('AIza-fake-key'))
    expect(await screen.findByText(/using your own key since/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Remove key' })).toBeInTheDocument()
  })

  it('shows the configured state and clears the input even when the post-save refetch fails', async () => {
    vi.mocked(client.getCredentials)
      .mockResolvedValueOnce({ configured: false, updated_at: null })
      .mockRejectedValueOnce(new Error('getCredentials → 500'))
    vi.mocked(client.putCredentials).mockResolvedValueOnce(undefined)
    render(<SettingsView />)

    const field = await screen.findByLabelText(/gemini api key/i)
    fireEvent.change(field, { target: { value: 'AIza-fake-key' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save key' }))

    await waitFor(() => expect(client.putCredentials).toHaveBeenCalledWith('AIza-fake-key'))
    expect(await screen.findByText(/using your own key since/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Remove key' })).toBeInTheDocument()
    expect(screen.queryByLabelText(/gemini api key/i)).not.toBeInTheDocument()
    expect(screen.queryByText('Save failed')).not.toBeInTheDocument()
    expect(screen.queryByText('Could not save your key. Please try again.')).not.toBeInTheDocument()
  })

  it('shows the server message on an invalid key (422)', async () => {
    vi.mocked(client.getCredentials).mockResolvedValue({ configured: false, updated_at: null })
    vi.mocked(client.putCredentials).mockRejectedValueOnce(new ApiError(422, 'That key looks invalid.'))
    render(<SettingsView />)

    fireEvent.change(await screen.findByLabelText(/gemini api key/i), { target: { value: 'bad-key' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save key' }))

    expect(await screen.findByText('That key looks invalid.')).toBeInTheDocument()
  })

  it('shows the server message when BYOK is unavailable (503)', async () => {
    vi.mocked(client.getCredentials).mockResolvedValue({ configured: false, updated_at: null })
    vi.mocked(client.putCredentials).mockRejectedValueOnce(new ApiError(503, 'BYOK is not available right now.'))
    render(<SettingsView />)

    fireEvent.change(await screen.findByLabelText(/gemini api key/i), { target: { value: 'AIza-fake-key' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save key' }))

    expect(await screen.findByText('BYOK is not available right now.')).toBeInTheDocument()
  })

  it('falls back to a generic message on a non-ApiError save failure', async () => {
    vi.mocked(client.getCredentials).mockResolvedValue({ configured: false, updated_at: null })
    vi.mocked(client.putCredentials).mockRejectedValueOnce(new Error('network down'))
    render(<SettingsView />)

    fireEvent.change(await screen.findByLabelText(/gemini api key/i), { target: { value: 'AIza-fake-key' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save key' }))

    expect(await screen.findByText('Could not save your key. Please try again.')).toBeInTheDocument()
  })

  it('removes a configured key and returns to the walkthrough', async () => {
    vi.mocked(client.getCredentials).mockResolvedValueOnce({ configured: true, updated_at: '2026-07-20T00:00:00Z' })
    vi.mocked(client.deleteCredentials).mockResolvedValueOnce(undefined)
    render(<SettingsView />)

    fireEvent.click(await screen.findByRole('button', { name: 'Remove key' }))

    await waitFor(() => expect(client.deleteCredentials).toHaveBeenCalled())
    expect(await screen.findByRole('link', { name: /create a free gemini api key/i })).toBeInTheDocument()
  })
})
