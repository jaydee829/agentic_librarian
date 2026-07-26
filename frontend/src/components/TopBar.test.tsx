import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('../auth/AuthContext', () => ({
  useAuth: () => ({ user: { email: 'a@b.com', displayName: 'A' }, signOut: vi.fn() }),
}))

// AccountMenu fetches lazily on first open — TopBar rendering alone must not need a
// real client, but AccountMenu.tsx imports it at module scope.
vi.mock('../api/client', () => ({
  getAccount: vi.fn(),
}))

import TopBar from './TopBar'

afterEach(() => {
  localStorage.clear()
  document.documentElement.removeAttribute('data-theme')
})

describe('TopBar theme toggle', () => {
  it('flips data-theme and the label on click', async () => {
    document.documentElement.dataset.theme = 'light'
    render(<TopBar />, { wrapper: MemoryRouter })
    await userEvent.click(screen.getByRole('button', { name: /switch to dark mode/i }))
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(screen.getByRole('button', { name: /switch to light mode/i })).toBeInTheDocument()
  })
})

describe('TopBar branding', () => {
  it('shows the Shelfwright product name', () => {
    render(<TopBar />, { wrapper: MemoryRouter })
    expect(screen.getByText('Shelfwright')).toBeInTheDocument()
  })
})

describe('TopBar account menu', () => {
  it('renders the avatar as the account menu trigger, with no standalone sign-out button', () => {
    render(<TopBar />, { wrapper: MemoryRouter })
    const trigger = screen.getByRole('button', { name: /account menu/i })
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('button', { name: /^sign out$/i })).not.toBeInTheDocument()
  })
})
