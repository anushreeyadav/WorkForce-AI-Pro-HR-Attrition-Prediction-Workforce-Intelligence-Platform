import { useCallback, useEffect, useRef, useState } from 'react'
import { describeError, fieldErrors } from '../api/client'
import { useToast } from '../components/toastContext'

// Filters + page for server-paginated lists. Changing any filter returns to page 1.
export function useListState(initial) {
  const initialRef = useRef(initial)
  const [filters, setFilters] = useState(initial)
  const [page, setPage] = useState(1)
  const update = useCallback((patch) => {
    setFilters((current) => ({ ...current, ...patch }))
    setPage(1)
  }, [])
  const reset = useCallback(() => {
    setFilters(initialRef.current)
    setPage(1)
  }, [])
  return { filters, page, setPage, update, reset }
}

// Delays fast-changing input (keyword boxes) so a request is not sent per keystroke.
export function useDebounced(value, delay = 350) {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(timer)
  }, [value, delay])
  return debounced
}

// Runs a server action once at a time (duplicate submissions are ignored while busy). Success feedback is shown
// only after the server confirms; failures show the API's message and expose 422 field errors for forms.
export function useAction() {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const [errors, setErrors] = useState({})
  const [message, setMessage] = useState('')
  const running = useRef(false)

  const run = useCallback(
    async (perform, { success, onSuccess } = {}) => {
      if (running.current) return false
      running.current = true
      setBusy(true)
      setErrors({})
      setMessage('')
      try {
        const result = await perform()
        if (success) toast.success(typeof success === 'function' ? success(result) : success) // message may use the response
        onSuccess?.(result)
        return true
      } catch (error) {
        const fields = fieldErrors(error)
        setErrors(fields)
        const text = describeError(error)
        setMessage(text)
        if (!Object.keys(fields).length) toast.error(text)
        return false
      } finally {
        running.current = false
        setBusy(false)
      }
    },
    [toast],
  )
  const clear = useCallback(() => {
    setErrors({})
    setMessage('')
  }, [])
  return { busy, errors, message, run, clear }
}
