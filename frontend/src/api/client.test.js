import assert from 'node:assert/strict'
import { test } from 'node:test'
import { describeError, errorStatus, fieldErrors } from './client.js'

const failed = (status, data) => ({ response: { status, data } })

test('network failures and timeouts are explained, not shown as empty data', () => {
  assert.match(describeError({ code: 'ERR_NETWORK' }), /Cannot reach the server/)
  assert.match(describeError({ code: 'ECONNABORTED' }), /took too long/)
})

test('each HTTP status has a clear, non-technical message', () => {
  assert.match(describeError(failed(401, { detail: 'Invalid or expired token' })), /session has ended/)
  assert.equal(describeError(failed(403, { detail: 'Only ADMIN and HR can configure review cycles' })),
    'Only ADMIN and HR can configure review cycles')
  assert.match(describeError(failed(403, {})), /do not have permission/)
  assert.match(describeError(failed(404, {})), /Not found, or not available to you/)
  assert.equal(describeError(failed(409, { detail: 'The task was changed by someone else; reload it and try again' })),
    'The task was changed by someone else; reload it and try again')
  assert.match(describeError(failed(429, {})), /Too many requests/)
  assert.match(describeError(failed(503, { detail: { code: 'no_active_model' } })), /temporarily unavailable/)
})

test('server errors never show internal details', () => {
  const message = describeError(failed(500, { detail: 'Traceback (most recent call last): secret' }))
  assert.match(message, /could not complete the request/)
  assert.doesNotMatch(message, /Traceback|secret/)
})

test('validation errors are listed by field', () => {
  const error = failed(422, { detail: [{ loc: ['body', 'start_date'], msg: 'Field required' },
    { loc: ['query', 'page_size'], msg: 'Input should be less than or equal to 100' }] })
  assert.equal(describeError(error), 'start date: Field required; page size: Input should be less than or equal to 100')
  assert.deepEqual(fieldErrors(error), { start_date: 'Field required', page_size: 'Input should be less than or equal to 100' })
  assert.deepEqual(fieldErrors(failed(409, { detail: 'conflict' })), {})
})

test('status helper', () => {
  assert.equal(errorStatus(failed(410, {})), 410)
  assert.equal(errorStatus({}), null)
})
