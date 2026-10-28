// Incremental activity updates (GET /api/workforce-activity/updates). The server returns items oldest first after
// a signed cursor and asks clients to pass next_cursor back unchanged and to de-duplicate by event_id. Pure
// functions, unit-tested with `npm test`.

// Merge one /updates response into the accumulated updates. `seenIds` are event ids already on screen (the feed page),
// so an item that overlaps the page or an earlier response is never shown twice.
export function mergeUpdates(previous, response, seenIds = []) {
  const known = new Set(seenIds)
  const items = []
  for (const item of [...(previous?.items ?? []), ...(response?.items ?? [])]) {
    if (known.has(item.event_id)) continue
    known.add(item.event_id)
    items.push(item)
  }
  return {
    items,
    next_cursor: response?.next_cursor ?? previous?.next_cursor ?? null,
    has_more: Boolean(response?.has_more),
    settled_until: response?.settled_until ?? previous?.settled_until ?? null,
    recommended_poll_seconds: response?.recommended_poll_seconds ?? previous?.recommended_poll_seconds ?? null,
    error: null,
  }
}

// 410: the cursor expired; 422: it no longer matches the user or filters. Both are recovered by reloading the feed,
// which issues a fresh cursor (documented API behaviour).
export function cursorNeedsReset(status) {
  return status === 410 || status === 422
}

// Polling interval in milliseconds: the server's recommendation, never faster than 15 s.
export function pollDelay(recommendedSeconds) {
  const seconds = Number(recommendedSeconds)
  return Math.max(15, Number.isFinite(seconds) && seconds > 0 ? seconds : 60) * 1000
}
