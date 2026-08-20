import { useQuery } from '@tanstack/react-query'
import {
  Activity,
  Bot,
  Boxes,
  ChevronDown,
  GitBranch,
  ListChecks,
  LogOut,
  Menu,
  MessageSquareText,
  Settings,
  Workflow,
  X,
} from 'lucide-react'
import { useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'
import { PRODUCT_NAME } from '../config'
import { api } from '../lib/api'
import { initials } from '../lib/format'
import { useRealtime } from '../realtime/RealtimeProvider'
import { Button } from '../components/ui/Button'

const navigation = [
  { to: '/channels', label: 'Channels', icon: MessageSquareText },
  { to: '/agents', label: 'Agents', icon: Bot },
  { to: '/tasks', label: 'Tasks', icon: ListChecks },
  { to: '/traces', label: 'Traces', icon: GitBranch },
  { to: '/workflows', label: 'Workflows', icon: Workflow },
  { to: '/runners', label: 'Runners', icon: Boxes },
  { to: '/settings', label: 'Settings', icon: Settings },
]

const titles: Record<string, string> = {
  channels: 'Channels',
  agents: 'Agents',
  tasks: 'Tasks',
  traces: 'Traces',
  workflows: 'Workflows',
  runners: 'Runners',
  settings: 'Settings',
}

export function AppShell() {
  const { user, logout } = useAuth()
  const { status } = useRealtime()
  const location = useLocation()
  const [mobileOpen, setMobileOpen] = useState(false)
  const [profileOpen, setProfileOpen] = useState(false)
  const pageKey = location.pathname.split('/')[1] ?? 'channels'
  const permissionsQuery = useQuery({
    queryKey: ['permissions'],
    queryFn: () => api.permissions.list(),
    refetchInterval: 30_000,
  })
  const pendingPermissions = permissionsQuery.data?.filter((item) => item.status === 'pending').length ?? 0

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Aller au contenu</a>
      <aside className={`app-sidebar ${mobileOpen ? 'is-open' : ''}`}>
        <div className="sidebar-brand">
          <span className="brand__mark"><Bot aria-hidden="true" size={21} /></span>
          <div><strong>{PRODUCT_NAME}</strong><small>Control Plane</small></div>
          <button className="icon-button sidebar-close" aria-label="Fermer le menu" onClick={() => setMobileOpen(false)}>
            <X size={19} />
          </button>
        </div>
        <nav className="primary-nav" aria-label="Navigation principale">
          <span className="nav-label">Espace de travail</span>
          {navigation.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              onClick={() => setMobileOpen(false)}
              className={({ isActive }) => (isActive ? 'nav-link is-active' : 'nav-link')}
            >
              <Icon size={18} aria-hidden="true" />
              <span>{label}</span>
              {to === '/settings' && pendingPermissions > 0 ? (
                <span className="nav-count" aria-label={`${pendingPermissions} permissions en attente`}>
                  {pendingPermissions}
                </span>
              ) : null}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer">
          <div className={`connection-state connection-state--${status}`}>
            <span aria-hidden="true" />
            <div>
              <strong>{status === 'connected' ? 'Temps réel actif' : status === 'connecting' ? 'Connexion…' : 'Hors ligne'}</strong>
              <small>Événements du Control Plane</small>
            </div>
          </div>
        </div>
      </aside>
      {mobileOpen ? <button className="sidebar-scrim" aria-label="Fermer le menu" onClick={() => setMobileOpen(false)} /> : null}

      <div className="app-content">
        <header className="topbar">
          <div className="topbar__left">
            <button className="icon-button mobile-menu" aria-label="Ouvrir le menu" onClick={() => setMobileOpen(true)}>
              <Menu size={20} />
            </button>
            <div><span className="topbar__product">{PRODUCT_NAME}</span><strong>{titles[pageKey] ?? PRODUCT_NAME}</strong></div>
          </div>
          <div className="topbar__right">
            <span className="system-pulse"><Activity size={15} /> Système opérationnel</span>
            <div className="profile-menu">
              <button
                className="profile-trigger"
                aria-expanded={profileOpen}
                onClick={() => setProfileOpen((open) => !open)}
              >
                <span className="avatar avatar--human">{initials(user?.display_name ?? 'Utilisateur')}</span>
                <span><strong>{user?.display_name}</strong><small>Propriétaire</small></span>
                <ChevronDown size={15} />
              </button>
              {profileOpen ? (
                <div className="profile-popover">
                  <span>{user?.email}</span>
                  <Button variant="ghost" size="small" icon={LogOut} onClick={() => void logout()}>
                    Se déconnecter
                  </Button>
                </div>
              ) : null}
            </div>
          </div>
        </header>
        <main id="main-content" className="page-content" tabIndex={-1}>
          <Outlet />
        </main>
      </div>
    </div>
  )
}
