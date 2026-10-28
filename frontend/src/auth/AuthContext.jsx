import { useCallback, useEffect, useMemo, useState } from 'react'
import api, { getToken, setToken } from '../api/client'
import { AuthContext } from './context'

// Identity always comes from the server (/auth/me or the login response), never from client-side input.
export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [status, setStatus] = useState(getToken() ? 'loading' : 'anonymous')
  const [expired, setExpired] = useState(false)

  useEffect(() => {
    if (!getToken()) return undefined
    const controller = new AbortController()
    api
      .get('/auth/me', { signal: controller.signal })
      .then((res) => {
        setUser(res.data)
        setStatus('authenticated')
      })
      .catch((error) => {
        if (error?.name === 'CanceledError') return
        setToken(null)
        setUser(null)
        setStatus('anonymous')
      })
    return () => controller.abort()
  }, [])

  useEffect(() => {
    const onExpired = () => {
      setUser(null)
      setStatus('anonymous')
      setExpired(true)
    }
    window.addEventListener('auth:expired', onExpired)
    return () => window.removeEventListener('auth:expired', onExpired)
  }, [])

  const login = useCallback(async (email, password) => {
    const res = await api.post('/auth/login', { email, password })
    setToken(res.data.access_token)
    setUser(res.data.user)
    setExpired(false)
    setStatus('authenticated')
    return res.data.user
  }, [])

  const logout = useCallback(async () => {
    try {
      await api.post('/auth/logout') // revokes this token on the server (it is rejected from now on)
    } catch {
      // Server unreachable: the token is still discarded locally, but it was not revoked and stays valid on the
      // server until it expires (ACCESS_TOKEN_EXPIRE_MINUTES). An administrator can end all sessions of an account.
    }
    setToken(null)
    setUser(null)
    setExpired(false)
    setStatus('anonymous')
  }, [])

  // The server already ended every session (e.g. after a password change): discard the token locally and show the
  // sign-in page with the "session has ended" notice. No logout request: the token is no longer valid anyway.
  const endSession = useCallback(() => {
    setToken(null)
    setUser(null)
    setExpired(true)
    setStatus('anonymous')
  }, [])

  const value = useMemo(() => ({ user, status, expired, login, logout, endSession }),
    [user, status, expired, login, logout, endSession])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
