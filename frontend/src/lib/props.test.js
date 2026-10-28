import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { test } from 'node:test'

// Every props object inherits Object.prototype members, so a destructuring default for a prop with one of these names
// never applies (the inherited function is used instead). EmployeePicker's `valueOf = ...` crashed the review and
// intervention forms this way until Sub-prompt 32.
const INHERITED = Object.getOwnPropertyNames(Object.prototype).filter((name) => /^[a-zA-Z]+$/.test(name))

function sources(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) return sources(path)
    return /\.jsx?$/.test(entry.name) && !entry.name.endsWith('.test.js') ? [path] : []
  })
}

test('no destructured prop default shadows an Object.prototype member', () => {
  const pattern = new RegExp(`[{,]\\s*(${INHERITED.join('|')})\\s*=(?!=)`)
  const offenders = sources(new URL('..', import.meta.url).pathname.replace(/^\/(\w:)/, '$1'))
    .filter((file) => pattern.test(readFileSync(file, 'utf8')))
  assert.deepEqual(offenders, [])
})
