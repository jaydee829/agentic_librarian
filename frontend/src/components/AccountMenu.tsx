import { useEffect, useRef, useState } from 'react'
import { useAuth } from '../auth/AuthContext'
import { getAccount, type Account } from '../api/client'

const KOFI_URL = 'https://ko-fi.com/shelfwright'

/** Account dropdown off the top-bar avatar. Future home of username change and the
 *  BYOK entry (arc PR 3). Sign-out must never depend on the API: account fetch failure
 *  just hides the status line. */
export default function AccountMenu() {
  const { user, signOut } = useAuth()
  const [open, setOpen] = useState(false)
  const [account, setAccount] = useState<Account | null>(null)
  const rootRef = useRef<HTMLDivElement>(null)
  const initial = (user?.displayName || user?.email || '?').charAt(0).toUpperCase()

  useEffect(() => {
    if (!open) return
    if (account === null) {
      getAccount().then(setAccount).catch(() => setAccount(null))
    }
    function onDocClick(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false)
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDocClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [open, account])

  const status =
    account?.tier === 'supporter' && account.subscriber_until
      ? `Supporter until ${new Date(account.subscriber_until).toLocaleDateString()}`
      : account?.tier === 'free'
        ? 'Free plan'
        : null

  return (
    <div className="account-menu" ref={rootRef}>
      <button
        className="avatar avatar-button"
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label="Account menu"
        onClick={() => setOpen((v) => !v)}
      >
        {initial}
      </button>
      {open && (
        <div className="account-menu-panel" role="menu">
          <div className="account-menu-identity">
            <div className="account-menu-name">{user?.displayName || user?.email}</div>
            {user?.displayName && <div className="account-menu-email">{user?.email}</div>}
            {status && <div className="account-menu-status">{status}</div>}
          </div>
          <div className="account-menu-support">
            <div className="account-menu-heading">Support Shelfwright ♥</div>
            <a href={KOFI_URL} target="_blank" rel="noopener noreferrer" role="menuitem">$3 / month</a>
            <a href={KOFI_URL} target="_blank" rel="noopener noreferrer" role="menuitem">$25 / year</a>
            <a href={KOFI_URL} target="_blank" rel="noopener noreferrer" role="menuitem">Leave a tip</a>
            <div className="account-menu-nudge">
              Use your Shelfwright sign-in email on Ko-fi so your support links up automatically.
            </div>
          </div>
          <hr />
          <button role="menuitem" onClick={() => void signOut()}>Sign out</button>
        </div>
      )}
    </div>
  )
}
