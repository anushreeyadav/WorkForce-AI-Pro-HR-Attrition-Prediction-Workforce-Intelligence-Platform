import { useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { BrainCircuit, LogOut, Menu, PanelLeftClose, PanelLeftOpen, Search, X } from 'lucide-react'
import { useAuth } from '../auth/context'
import { NAV_GROUPS, NAV_ITEMS } from '../lib/navigation'
import { humanize } from '../lib/format'

function SidebarNav({ user, collapsed, onNavigate }) {
  return (
    <nav aria-label="Main" className="flex-1 overflow-y-auto px-2 py-4">
      {NAV_GROUPS.map((group) => {
        const items = group.items.filter((item) => item.roles.includes(user.role))
        if (!items.length) return null
        return (
          <div key={group.label} className="mb-4">
            {!collapsed && <p className="px-3 pb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400">{group.label}</p>}
            <ul className="space-y-0.5">
              {items.map(({ to, label, icon: Icon }) => (
                <li key={to}>
                  <NavLink to={to} end={to === '/'} onClick={onNavigate} title={collapsed ? label : undefined}
                    className={({ isActive }) =>
                      `flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors focus-visible:outline-2 focus-visible:outline-blue-400 ${
                        isActive ? 'bg-slate-800 font-medium text-white' : 'text-slate-300 hover:bg-slate-800/60 hover:text-white'}`}>
                    <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
                    {/* Kept for screen readers when collapsed (a title tooltip alone is not a reliable accessible name) */}
                    <span className={collapsed ? 'sr-only' : undefined}>{label}</span>
                  </NavLink>
                </li>
              ))}
            </ul>
          </div>
        )
      })}
    </nav>
  )
}

function Brand({ collapsed }) {
  return (
    <div className="flex h-14 items-center gap-2 border-b border-slate-800 px-4">
      <BrainCircuit className="h-6 w-6 shrink-0 text-teal-400" aria-hidden="true" />
      {!collapsed && <span className="text-sm font-semibold tracking-wide text-white">WorkForce AI Pro</span>}
    </div>
  )
}

export default function AppLayout() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [collapsed, setCollapsed] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [term, setTerm] = useState('')
  const current = NAV_ITEMS.find((item) => (item.to === '/' ? location.pathname === '/' : location.pathname.startsWith(item.to)))

  const submitSearch = (event) => {
    event.preventDefault()
    const q = term.trim()
    if (q.length >= 2) navigate(`/search?q=${encodeURIComponent(q)}`)
  }

  const signOut = async () => {
    await logout()
    navigate('/login', { replace: true })
  }

  return (
    <div className="flex min-h-screen bg-slate-50">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-white focus:px-3 focus:py-2">
        Skip to content
      </a>
      {/* Desktop sidebar */}
      <aside className={`hidden shrink-0 flex-col bg-slate-900 transition-[width] lg:flex ${collapsed ? 'w-16' : 'w-64'}`}>
        <Brand collapsed={collapsed} />
        <SidebarNav user={user} collapsed={collapsed} />
        <button type="button" onClick={() => setCollapsed((c) => !c)} aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          className="flex items-center gap-2 border-t border-slate-800 px-5 py-3 text-xs text-slate-400 hover:text-white focus-visible:outline-2 focus-visible:outline-blue-400">
          {collapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
          {!collapsed && 'Collapse'}
        </button>
      </aside>

      {/* Mobile drawer */}
      {drawerOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-slate-900/50" onClick={() => setDrawerOpen(false)} aria-hidden="true" />
          <aside className="relative flex h-full w-72 flex-col bg-slate-900" aria-label="Navigation">
            <div className="flex items-center justify-between pr-2">
              <Brand />
              <button type="button" aria-label="Close navigation" onClick={() => setDrawerOpen(false)} className="rounded p-2 text-slate-300 hover:text-white">
                <X className="h-5 w-5" />
              </button>
            </div>
            <SidebarNav user={user} onNavigate={() => setDrawerOpen(false)} />
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-slate-200 bg-white px-4 lg:px-6">
          <button type="button" aria-label="Open navigation" onClick={() => setDrawerOpen(true)} className="rounded p-1.5 text-slate-600 hover:bg-slate-100 lg:hidden">
            <Menu className="h-5 w-5" />
          </button>
          <p className="hidden text-sm font-medium text-slate-700 sm:block">{current?.label ?? ''}</p>
          <form role="search" onSubmit={submitSearch} className="ml-auto flex w-full max-w-xs items-center">
            <label htmlFor="global-search" className="sr-only">Search</label>
            <div className="relative w-full">
              <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-slate-400" aria-hidden="true" />
              <input id="global-search" value={term} onChange={(e) => setTerm(e.target.value)} maxLength={100}
                placeholder="Search people, tasks, goals…"
                className="h-9 w-full rounded-md border border-slate-300 bg-white pl-8 pr-3 text-sm focus:border-blue-500 focus:outline-2 focus:outline-blue-200" />
            </div>
          </form>
          <div className="hidden text-right sm:block">
            <p className="text-sm font-medium leading-tight text-slate-900">{user.full_name}</p>
            <p className="text-xs leading-tight text-slate-500">{humanize(user.role)}</p>
          </div>
          <button type="button" onClick={signOut} className="inline-flex items-center gap-1.5 rounded-md px-2 py-1.5 text-sm text-slate-600 hover:bg-slate-100 focus-visible:outline-2 focus-visible:outline-blue-600">
            <LogOut className="h-4 w-4" aria-hidden="true" />
            <span className="hidden md:inline">Sign out</span>
          </button>
        </header>
        <main id="main" className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 lg:px-8">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
