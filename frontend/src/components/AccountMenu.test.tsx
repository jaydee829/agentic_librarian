import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

const signOut = vi.fn()

vi.mock('../auth/AuthContext', () => ({
  useAuth: () => ({ user: { email: 'a@b.com', displayName: 'Ada' }, signOut }),
}))

vi.mock('../api/client', () => ({
  getAccount: vi.fn(),
}))

import { getAccount } from '../api/client'
import AccountMenu from './AccountMenu'

afterEach(() => {
  vi.mocked(getAccount).mockReset()
  signOut.mockClear()
})

async function openMenu() {
  render(<AccountMenu />, { wrapper: MemoryRouter })
  const trigger = screen.getByRole('button', { name: /account menu/i })
  await userEvent.click(trigger)
  return trigger
}

describe('AccountMenu', () => {
  it('is closed by default, with aria-expanded false on the trigger', () => {
    render(<AccountMenu />, { wrapper: MemoryRouter })
    const trigger = screen.getByRole('button', { name: /account menu/i })
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(trigger).toHaveAttribute('aria-haspopup', 'menu')
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })

  it('opens the panel on avatar click and marks aria-expanded true', async () => {
    vi.mocked(getAccount).mockResolvedValueOnce({
      email: 'a@b.com', display_name: 'Ada', tier: 'free', subscriber_until: null,
    })
    const trigger = await openMenu()
    expect(trigger).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('menu')).toBeInTheDocument()
  })

  it('shows "Free plan" for a free-tier account', async () => {
    vi.mocked(getAccount).mockResolvedValueOnce({
      email: 'a@b.com', display_name: 'Ada', tier: 'free', subscriber_until: null,
    })
    await openMenu()
    await waitFor(() => expect(screen.getByText('Free plan')).toBeInTheDocument())
  })

  it('shows "Supporter until <date>" for a supporter-tier account', async () => {
    vi.mocked(getAccount).mockResolvedValueOnce({
      email: 'a@b.com', display_name: 'Ada', tier: 'supporter', subscriber_until: '2026-08-25T00:00:00+00:00',
    })
    await openMenu()
    const expected = `Supporter until ${new Date('2026-08-25T00:00:00+00:00').toLocaleDateString()}`
    await waitFor(() => expect(screen.getByText(expected)).toBeInTheDocument())
  })

  it('shows no status line when the account fetch fails', async () => {
    // ...Once variant (vitest#1692): a persistent mockRejectedValue leaks into other
    // tests' unrelated calls as an unhandled rejection.
    vi.mocked(getAccount).mockRejectedValueOnce(new Error('account → 500'))
    await openMenu()
    await waitFor(() => expect(vi.mocked(getAccount)).toHaveBeenCalled())
    expect(screen.queryByText('Free plan')).not.toBeInTheDocument()
    expect(screen.queryByText(/Supporter until/)).not.toBeInTheDocument()
  })

  it('renders three Ko-fi support links, all pointing at ko-fi.com/shelfwright and opening in a new tab safely', async () => {
    vi.mocked(getAccount).mockResolvedValueOnce({
      email: 'a@b.com', display_name: 'Ada', tier: 'free', subscriber_until: null,
    })
    await openMenu()
    const menuItems = await screen.findAllByRole('menuitem')
    const links = menuItems.filter((el) => el.getAttribute('href') === 'https://ko-fi.com/shelfwright')
    expect(links).toHaveLength(3)
    for (const link of links) {
      expect(link).toHaveAttribute('href', 'https://ko-fi.com/shelfwright')
      expect(link).toHaveAttribute('target', '_blank')
      expect(link.getAttribute('rel')).toContain('noopener')
    }
    expect(screen.getByText(/sign-in email on Ko-fi/i)).toBeInTheDocument()
  })

  it('closes the panel on Escape', async () => {
    vi.mocked(getAccount).mockResolvedValueOnce({
      email: 'a@b.com', display_name: 'Ada', tier: 'free', subscriber_until: null,
    })
    await openMenu()
    expect(screen.getByRole('menu')).toBeInTheDocument()
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })

  it('closes the panel on an outside click', async () => {
    vi.mocked(getAccount).mockResolvedValueOnce({
      email: 'a@b.com', display_name: 'Ada', tier: 'free', subscriber_until: null,
    })
    await openMenu()
    expect(screen.getByRole('menu')).toBeInTheDocument()
    await userEvent.click(document.body)
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })

  it('calls the auth context signOut when Sign out is clicked, even after getAccount rejected', async () => {
    vi.mocked(getAccount).mockRejectedValueOnce(new Error('account → 500'))
    await openMenu()
    await waitFor(() => expect(vi.mocked(getAccount)).toHaveBeenCalled())
    await userEvent.click(screen.getByRole('menuitem', { name: /sign out/i }))
    expect(signOut).toHaveBeenCalledTimes(1)
  })

  it('shows "Using your own key" for a byok-tier account', async () => {
    vi.mocked(getAccount).mockResolvedValueOnce({
      email: 'a@b.com', display_name: 'Ada', tier: 'byok', subscriber_until: null,
    })
    await openMenu()
    await waitFor(() => expect(screen.getByText('Using your own key')).toBeInTheDocument())
  })

  it('has an "API key settings" menuitem linking to /settings', async () => {
    vi.mocked(getAccount).mockResolvedValueOnce({
      email: 'a@b.com', display_name: 'Ada', tier: 'free', subscriber_until: null,
    })
    await openMenu()
    const link = await screen.findByRole('menuitem', { name: /api key settings/i })
    expect(link).toHaveAttribute('href', '/settings')
  })
})
