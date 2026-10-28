import { useState } from 'react'
import api, { describeError } from '../api/client'
import { useAuth } from '../auth/context'
import { Button, Dialog, TextInput } from './ui'

const MIN_LENGTH = 12 // backend PASSWORD_MIN_LENGTH; the server checks every rule again

// Change your own password (POST /auth/change-password). On success the server ends every session of the account,
// including this one, so the user signs in again with the new password.
export function ChangePasswordDialog({ onClose }) {
  const { endSession } = useAuth()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const tooShort = next.length > 0 && next.length < MIN_LENGTH
  const mismatch = confirm.length > 0 && confirm !== next
  const ready = current && next.length >= MIN_LENGTH && confirm === next && !busy

  const submit = async (event) => {
    event.preventDefault()
    if (!ready) return
    setBusy(true)
    setError('')
    try {
      await api.post('/auth/change-password', { current_password: current, new_password: next })
      endSession()
    } catch (err) {
      setError(describeError(err))
      setBusy(false)
    }
  }

  return (
    <Dialog open title="Change password" onClose={onClose}
      footer={<><Button variant="secondary" onClick={onClose} disabled={busy}>Cancel</Button>
        <Button type="submit" form="password-form" loading={busy} disabled={!ready}>Change password</Button></>}>
      <form id="password-form" onSubmit={submit} noValidate className="space-y-4">
        <p className="text-sm text-slate-600">Every session of your account ends after the change, including this one. Sign in again with the new password.</p>
        {error && <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-800">{error}</p>}
        <TextInput label="Current password" type="password" autoComplete="current-password" required maxLength={256}
          value={current} onChange={(e) => setCurrent(e.target.value)} />
        <TextInput label="New password" type="password" autoComplete="new-password" required maxLength={72}
          value={next} onChange={(e) => setNext(e.target.value)} hint={`At least ${MIN_LENGTH} characters.`}
          error={tooShort ? `At least ${MIN_LENGTH} characters.` : undefined} />
        <TextInput label="Confirm new password" type="password" autoComplete="new-password" required maxLength={72}
          value={confirm} onChange={(e) => setConfirm(e.target.value)} error={mismatch ? 'The passwords do not match.' : undefined} />
      </form>
    </Dialog>
  )
}
