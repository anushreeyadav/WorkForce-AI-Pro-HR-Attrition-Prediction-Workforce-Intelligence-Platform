import { useEffect, useState } from 'react'
import api from '../api/client'

// The original landing page's health check, kept as a small indicator on the sign-in page.
export default function BackendStatus() {
  const [status, setStatus] = useState('checking…')
  useEffect(() => {
    const controller = new AbortController()
    api
      .get('/health', { signal: controller.signal })
      .then((res) => setStatus(res.data.status))
      .catch((error) => {
        if (error?.name !== 'CanceledError') setStatus('unreachable')
      })
    return () => controller.abort()
  }, [])
  const tone = status === 'ok' ? 'bg-teal-500' : status === 'unreachable' ? 'bg-red-500' : 'bg-slate-400'
  return (
    <p className="mt-4 flex items-center justify-center gap-2 text-xs text-slate-500">
      <span className={`h-2 w-2 rounded-full ${tone}`} aria-hidden="true" />
      Service status: <span className="font-medium">{status}</span>
    </p>
  )
}
