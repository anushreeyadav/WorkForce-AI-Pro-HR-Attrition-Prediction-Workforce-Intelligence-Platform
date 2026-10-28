import assert from 'node:assert/strict'
import { test } from 'node:test'
import { cursorNeedsReset, mergeUpdates, pollDelay } from './feed.js'

const item = (id) => ({ event_id: id, description: id })

test('updates accumulate in order and keep the newest cursor', () => {
  const first = mergeUpdates(null, { items: [item('a'), item('b')], next_cursor: 'c1', has_more: true, settled_until: 't1' })
  const second = mergeUpdates(first, { items: [item('c')], next_cursor: 'c2', has_more: false, settled_until: 't2' })
  assert.deepEqual(second.items.map((i) => i.event_id), ['a', 'b', 'c'])
  assert.equal(second.next_cursor, 'c2')
  assert.equal(second.has_more, false)
  assert.equal(second.settled_until, 't2')
  assert.equal(second.error, null)
})

test('overlapping responses never show an event twice', () => {
  const first = mergeUpdates(null, { items: [item('a'), item('b')], next_cursor: 'c1' })
  const overlapping = mergeUpdates(first, { items: [item('b'), item('c'), item('c')], next_cursor: 'c2' })
  assert.deepEqual(overlapping.items.map((i) => i.event_id), ['a', 'b', 'c'])
})

test('events already on the feed page are not repeated as new', () => {
  const merged = mergeUpdates(null, { items: [item('page-1'), item('new')], next_cursor: 'c1' }, ['page-1', 'page-2'])
  assert.deepEqual(merged.items.map((i) => i.event_id), ['new'])
})

test('an empty response keeps the previous cursor', () => {
  const first = mergeUpdates(null, { items: [item('a')], next_cursor: 'c1', recommended_poll_seconds: 30 })
  const empty = mergeUpdates(first, { items: [] })
  assert.equal(empty.next_cursor, 'c1')
  assert.equal(empty.recommended_poll_seconds, 30)
})

test('expired (410) and invalid (422) cursors are recovered by reloading the feed', () => {
  assert.equal(cursorNeedsReset(410), true)
  assert.equal(cursorNeedsReset(422), true)
  for (const status of [401, 403, 404, 500, 503, null]) assert.equal(cursorNeedsReset(status), false)
})

test('polling follows the server recommendation but never faster than 15 seconds', () => {
  assert.equal(pollDelay(60), 60000)
  assert.equal(pollDelay(5), 15000)
  assert.equal(pollDelay(undefined), 60000)
  assert.equal(pollDelay('nonsense'), 60000)
})
