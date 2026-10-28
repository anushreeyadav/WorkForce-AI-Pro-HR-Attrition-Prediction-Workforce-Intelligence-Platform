import axios from 'axios'
import { endsCurrentSession } from '../lib/session.js'

// Shared API client. The access token lives in sessionStorage (cleared when the tab closes) and is attached to
// every request. A 401 for a request that carried the current token means the session is no longer valid: the token
// is cleared and the app is told to return to the login page. A late 401 from an earlier session is ignored.
const TOKEN_KEY = 'wfp.accessToken'

const api = axios.create({
  baseURL: import.meta.env?.VITE_API_BASE_URL || '/api', // env is undefined outside Vite (unit tests)
  timeout: 15000,
})

export function getToken() {
  try {
    return sessionStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setToken(token) {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token)
    else sessionStorage.removeItem(TOKEN_KEY)
  } catch {
    // Storage unavailable (private mode): the session simply won't survive a reload.
  }
}

api.interceptors.request.use((config) => {
  const token = getToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  config.sentToken = token // compared on 401: see endsCurrentSession
  return config
})

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (endsCurrentSession({ status: error.response?.status, url: error.config?.url || '',
      sentToken: error.config?.sentToken ?? null, currentToken: getToken() })) {
      setToken(null)
      window.dispatchEvent(new CustomEvent('auth:expired'))
    }
    return Promise.reject(error)
  },
)

export function isCancel(error) {
  return axios.isCancel(error) || error?.name === 'CanceledError'
}

// Human-readable, non-sensitive message for a failed request. Backend `detail` strings are written for users;
// stack traces and internal details are never shown (5xx bodies are ignored).
export function describeError(error) {
  if (!error) return ''
  if (!error.response) {
    return error.code === 'ECONNABORTED'
      ? 'The server took too long to respond. Try again.'
      : 'Cannot reach the server. Check your connection and try again.'
  }
  const { status, data } = error.response
  const detail = data?.detail
  if (status === 401) return 'Your session has ended. Please sign in again.'
  if (status === 403) return typeof detail === 'string' ? detail : 'You do not have permission to view or change this.'
  if (status === 404) return typeof detail === 'string' ? detail : 'Not found, or not available to you.'
  if (status === 422 && Array.isArray(detail)) return detail.map((d) => `${fieldName(d.loc)}${d.msg}`).join('; ')
  // The API rate-limits sign-in, registration and password changes (with its own message); a proxy may add others.
  if (status === 429) return typeof detail === 'string' ? detail : 'Too many requests. Wait a moment and try again.'
  if (status >= 500) {
    return status === 503
      ? 'The service is temporarily unavailable. Try again shortly.'
      : 'The server could not complete the request. Try again later.'
  }
  return typeof detail === 'string' ? detail : `Request failed (${status}).`
}

function fieldName(loc) {
  if (!Array.isArray(loc)) return ''
  const name = loc.filter((part) => !['body', 'query', 'path'].includes(part)).join('.')
  return name ? `${String(name).replaceAll('_', ' ')}: ` : ''
}

// Field-level messages from a 422 response, keyed by field name, for forms.
export function fieldErrors(error) {
  const detail = error?.response?.status === 422 ? error.response.data?.detail : null
  const result = {}
  if (Array.isArray(detail)) {
    for (const item of detail) {
      const key = Array.isArray(item.loc) ? item.loc[item.loc.length - 1] : null
      if (key && typeof key === 'string') result[key] = item.msg
    }
  }
  return result
}

export function errorStatus(error) {
  return error?.response?.status ?? null
}

// Error bodies of blob requests arrive as Blobs; turn them back into JSON so describeError can read them.
export async function readBlobError(error) {
  const data = error?.response?.data
  if (data instanceof Blob) {
    try {
      error.response.data = JSON.parse(await data.text())
    } catch {
      error.response.data = {}
    }
  }
  return error
}

// Downloads a file endpoint with the current credentials and saves it under the server's filename.
// Returns the row count reported by the server (X-Report-Rows), when present.
export async function downloadFile(path, params) {
  let response
  try {
    response = await api.get(path, { params, responseType: 'blob', paramsSerializer: { indexes: null } })
  } catch (error) {
    throw await readBlobError(error)
  }
  const disposition = response.headers['content-disposition'] || ''
  const match = /filename="([^"]+)"/.exec(disposition)
  const url = URL.createObjectURL(response.data)
  const link = document.createElement('a')
  link.href = url
  link.download = match ? match[1] : 'download'
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
  const rows = Number(response.headers['x-report-rows'])
  return Number.isFinite(rows) ? rows : null
}

export default api
