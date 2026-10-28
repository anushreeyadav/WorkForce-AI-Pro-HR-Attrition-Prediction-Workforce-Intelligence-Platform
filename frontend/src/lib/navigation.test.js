import assert from 'node:assert/strict'
import { test } from 'node:test'
import { linkFor } from './links.js'
import { NAV_ITEMS, canAccess } from './navigation.js'

const role = (name) => ({ role: name })
const pages = (name) => NAV_ITEMS.filter((item) => canAccess(role(name), item.to)).map((item) => item.to).sort()

test('19 pages, each reachable by at least one role', () => {
  assert.equal(NAV_ITEMS.length, 19)
  for (const item of NAV_ITEMS) assert.ok(item.roles.length > 0, item.to)
})

test('employees see only self-service and shared pages', () => {
  assert.deepEqual(pages('EMPLOYEE'), ['/', '/attendance', '/departments', '/employees', '/leave', '/notifications', '/payslips',
    '/performance', '/search', '/tasks'].sort())
})

test('managers get department intelligence but no payroll administration or datasets', () => {
  const manager = pages('MANAGER')
  for (const path of ['/risk', '/analytics', '/recommendations', '/interventions', '/activity', '/reports', '/audit']) {
    assert.ok(manager.includes(path), path)
  }
  for (const path of ['/payroll', '/datasets']) assert.ok(!manager.includes(path), path)
})

test('HR and ADMIN reach every page', () => {
  assert.equal(pages('HR').length, 19)
  assert.equal(pages('ADMIN').length, 19)
})

test('no user or an unknown path has no access', () => {
  assert.equal(canAccess(null, '/'), false)
  assert.equal(canAccess(role('HR'), '/admin-console'), false)
})

test('notification and search links only point to pages the role may open', () => {
  assert.equal(linkFor(role('EMPLOYEE'), 'intervention', 4), null)
  assert.equal(linkFor(role('MANAGER'), 'intervention', 4), '/interventions?focus=4')
  assert.equal(linkFor(role('EMPLOYEE'), 'risk_alert', 1), null)
  assert.equal(linkFor(role('EMPLOYEE'), 'task', 7), '/tasks?focus=7')
  assert.equal(linkFor(role('EMPLOYEE'), 'payroll_record', 2), '/payslips')
  assert.equal(linkFor(role('HR'), 'unknown_type', 1), null)
})
