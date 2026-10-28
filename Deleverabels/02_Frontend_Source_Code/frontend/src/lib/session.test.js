import assert from 'node:assert/strict'
import { test } from 'node:test'
import { endsCurrentSession, postLoginPath } from './session.js'

const HR = { role: 'HR' }
const EMPLOYEE = { role: 'EMPLOYEE' }

test('a 401 for the current token ends the session', () => {
  assert.equal(endsCurrentSession({ status: 401, url: '/tasks', sentToken: 'a', currentToken: 'a' }), true)
})

test('a late 401 from an earlier session does not sign out the current user', () => {
  assert.equal(endsCurrentSession({ status: 401, url: '/tasks', sentToken: 'old', currentToken: 'new' }), false)
  assert.equal(endsCurrentSession({ status: 401, url: '/tasks', sentToken: 'old', currentToken: null }), false)
  assert.equal(endsCurrentSession({ status: 401, url: '/tasks', sentToken: null, currentToken: 'new' }), false)
})

test('login failures and other statuses never end the session', () => {
  assert.equal(endsCurrentSession({ status: 401, url: '/auth/login', sentToken: null, currentToken: null }), false)
  for (const status of [400, 403, 404, 409, 422, 429, 500, 503, undefined]) {
    assert.equal(endsCurrentSession({ status, url: '/tasks', sentToken: 'a', currentToken: 'a' }), false)
  }
})

test('after login, return only to a page the new account may open', () => {
  assert.equal(postLoginPath(HR, '/payroll?period=3'), '/payroll?period=3')
  assert.equal(postLoginPath(EMPLOYEE, '/payroll'), '/') // the previous user's admin page
  assert.equal(postLoginPath(EMPLOYEE, '/leave'), '/leave')
  assert.equal(postLoginPath(EMPLOYEE, undefined), '/')
  assert.equal(postLoginPath(HR, '/no-such-page'), '/')
})

test('post-login redirects never leave the app', () => {
  for (const from of ['https://example.com/', '//example.com/x', 'javascript:alert(1)', 'tasks']) {
    assert.equal(postLoginPath(HR, from), '/')
  }
})
