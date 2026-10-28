import { useCallback, useEffect, useState } from 'react'
import api, { isCancel } from '../api/client'

// GET a resource and track loading/error state. A new request cancels the previous one, so quickly changing
// filters never show stale results; unmounting cancels too. Errors are kept (not converted to empty data) so
// pages can tell "no permission" or "failed" apart from "no records". While a new request is pending, the
// previous data stays visible (no flicker) and `loading` is true.
export function useApi(path, params, { enabled = true } = {}) {
  const [version, setVersion] = useState(0)
  const [state, setState] = useState({ key: null, data: null, error: null })
  const query = JSON.stringify(params ?? {})
  const requestKey = path && enabled ? `${version}|${path}|${query}` : null

  useEffect(() => {
    if (!requestKey) return undefined
    const controller = new AbortController()
    api
      .get(path, { params: JSON.parse(query), signal: controller.signal, paramsSerializer: { indexes: null } })
      .then((res) => setState({ key: requestKey, data: res.data, error: null }))
      .catch((error) => {
        if (!isCancel(error)) setState({ key: requestKey, data: null, error })
      })
    return () => controller.abort()
  }, [requestKey, path, query]) // path and query are part of requestKey; listing them changes nothing

  const reload = useCallback(() => setVersion((v) => v + 1), [])
  if (!requestKey) return { data: null, error: null, loading: false, reload }
  const current = state.key === requestKey
  return { data: state.data, error: current ? state.error : null, loading: !current, reload }
}

// Removes empty values so they are not sent as query parameters.
export function cleanParams(params) {
  return Object.fromEntries(Object.entries(params).filter(([, v]) => v !== '' && v !== null && v !== undefined && v !== false))
}
