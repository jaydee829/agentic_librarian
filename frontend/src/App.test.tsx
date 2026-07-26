import { render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import type { AuthStatus } from './auth/AuthContext'

const state = vi.hoisted(() => ({ status: 'loading' as AuthStatus }))

vi.mock('./auth/AuthContext', () => ({
  AuthProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
  useAuth: () => ({
    status: state.status,
    user: { email: 'friend@example.com', displayName: 'Friend' },
    signIn: vi.fn(),
    signOut: vi.fn(),
  }),
}))

// Views render nothing meaningful here; we only assert the gate + shell.
vi.mock('./views/ChatView', () => ({ default: () => <div>chat-view</div> }))
vi.mock('./views/HistoryView', () => ({ default: () => <div>history-view</div> }))
vi.mock('./views/RecommendationsView', () => ({ default: () => <div>recs-view</div> }))
vi.mock('./views/AnalysisView', () => ({ default: () => <div>analysis-view</div> }))
vi.mock('./views/AddBookView', () => ({ default: () => <div>add-view</div> }))
vi.mock('./views/HistoryEditView', () => ({ default: () => <div>history-edit-view</div> }))
vi.mock('./views/ImportView', () => ({ default: () => <div>ImportView</div> }))
vi.mock('./views/SettingsView', () => ({ default: () => <div>settings-view</div> }))
// AccountMenu transitively imports api/client -> auth/firebase, whose module-level init
// throws auth/invalid-api-key in CI (no VITE_FIREBASE_* env there — passes locally only
// because the dev .env exists). Mock it like the views; its own tests cover it.
vi.mock('./components/AccountMenu', () => ({ default: () => <div>account-menu</div> }))

import App from './App'

describe('App gate', () => {
  it('shows the sign-in screen when signed out', () => {
    state.status = 'signedOut'
    render(<App />)
    expect(screen.getByRole('button', { name: /sign in with google/i })).toBeInTheDocument()
  })

  it('shows the not-invited screen for a verified stranger', () => {
    state.status = 'notInvited'
    render(<App />)
    expect(screen.getByText(/invite/i)).toBeInTheDocument()
  })

  it('renders the shell with the chat view when ready', () => {
    state.status = 'ready'
    render(<App />)
    expect(screen.getByText('chat-view')).toBeInTheDocument()
    expect(screen.getByRole('navigation')).toBeInTheDocument()
  })

  it('renders the import route', () => {
    state.status = 'ready'
    window.history.pushState({}, '', '/import')
    render(<App />)
    expect(screen.getByText('ImportView')).toBeInTheDocument()
  })
})
