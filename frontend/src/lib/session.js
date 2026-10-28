import { canAccess } from './navigation.js'

// Session rules shared by the API client and the login page. Pure functions, so they can be unit-tested with
// `npm test` (Node's built-in test runner) without a browser.

// Does this failed response end the CURRENT session? Only a 401 for a request that carried the current token does.
// A request still in flight from an earlier session (signed out, or another user signed in since) must not sign out
// whoever is using the app now. Login failures are reported by the login form, not as an expired session.
export function endsCurrentSession({ status, url = '', sentToken = null, currentToken = null }) {
  if (status !== 401 || url.includes('/auth/login')) return false
  return Boolean(currentToken) && sentToken === currentToken
}

// Where to go after signing in: the page the user was sent away from, but only if the account that has now signed
// in may open it (another user may sign in on the same tab). Anything else goes to the dashboard.
export function postLoginPath(user, from) {
  if (typeof from !== 'string' || !from.startsWith('/') || from.startsWith('//')) return '/'
  const pathname = from.split(/[?#]/)[0]
  return canAccess(user, pathname) ? from : '/'
}
