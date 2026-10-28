import { Navigate, useLocation } from 'react-router-dom'
import { Lock } from 'lucide-react'
import { useAuth } from '../auth/context'
import { canAccess } from '../lib/navigation'
import { Spinner } from './ui'

export function RequireAuth({ children }) {
  const { status } = useAuth()
  const location = useLocation()
  if (status === 'loading') return <Spinner label="Checking your session" />
  if (status !== 'authenticated') return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />
  return children
}

// Hides pages the role cannot use. This is a convenience only; the API enforces every permission itself.
export function RequireRole({ path, children }) {
  const { user } = useAuth()
  if (!canAccess(user, path)) {
    return (
      <div className="flex flex-col items-center py-20 text-center text-slate-600">
        <Lock className="h-8 w-8" aria-hidden="true" />
        <h1 className="mt-3 text-lg font-semibold text-slate-900">Not available for your role</h1>
        <p className="mt-1 text-sm">Ask an administrator if you need access to this area.</p>
      </div>
    )
  }
  return children
}
