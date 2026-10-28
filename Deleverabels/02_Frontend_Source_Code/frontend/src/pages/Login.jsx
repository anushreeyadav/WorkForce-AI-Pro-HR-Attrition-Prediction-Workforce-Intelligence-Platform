import { useState } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { BrainCircuit } from 'lucide-react'
import { useAuth } from '../auth/context'
import { describeError } from '../api/client'
import { postLoginPath } from '../lib/session.js'
import BackendStatus from '../components/BackendStatus'
import { Button, TextInput } from '../components/ui'

export default function Login() {
  const { user, status, expired, login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  if (status === 'authenticated') return <Navigate to={postLoginPath(user, location.state?.from)} replace />

  const submit = async (event) => {
    event.preventDefault()
    if (busy) return
    if (!email.trim() || !password) {
      setError('Enter your email and password.')
      return
    }
    setBusy(true)
    setError('')
    try {
      const signedIn = await login(email.trim(), password)
      navigate(postLoginPath(signedIn, location.state?.from), { replace: true })
    } catch (err) {
      // Same message for unknown accounts and wrong passwords (as the API does).
      setError(err.response?.status === 401 ? 'Invalid email or password.' : describeError(err))
      setPassword('')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-100 p-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center justify-center gap-2">
          <BrainCircuit className="h-8 w-8 text-teal-600" aria-hidden="true" />
          <span className="text-xl font-semibold text-slate-900">WorkForce AI Pro</span>
        </div>
        <form onSubmit={submit} noValidate className="space-y-4 rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
          <div>
            <h1 className="text-lg font-semibold text-slate-900">Sign in</h1>
            <p className="text-sm text-slate-500">HR attrition prediction &amp; workforce intelligence</p>
          </div>
          {expired && !error && (
            <p role="status" className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-900">Your session has ended. Please sign in again.</p>
          )}
          {error && <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-800">{error}</p>}
          <TextInput label="Work email" type="email" autoComplete="username" value={email} required maxLength={254}
            onChange={(e) => setEmail(e.target.value)} />
          <TextInput label="Password" type="password" autoComplete="current-password" value={password} required maxLength={256}
            onChange={(e) => setPassword(e.target.value)} />
          <Button type="submit" className="w-full" loading={busy}>Sign in</Button>
        </form>
        <BackendStatus />
      </div>
    </main>
  )
}
