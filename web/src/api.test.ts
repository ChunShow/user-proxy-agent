import assert from 'node:assert/strict'
import { afterEach, mock, test } from 'node:test'

import { fetchHealth } from './api.ts'

afterEach(() => mock.restoreAll())

test('requests the relative API and returns the verified identity', async () => {
  mock.method(globalThis, 'fetch', async (url: string, init: RequestInit) => {
    assert.equal(url, '/api/health')
    assert.equal(init.cache, 'no-store')
    assert.ok(init.signal)
    return Response.json({ status: 'ok', service: 'agent-service' })
  })
  assert.deepEqual(await fetchHealth(), { status: 'ok', service: 'agent-service' })
})

for (const [label, response] of [
  ['HTTP error', new Response('unavailable', { status: 503 })],
  ['malformed JSON', new Response('not-json')],
  ['wrong service', Response.json({ status: 'ok', service: 'another-service' })],
  ['unhealthy response', Response.json({ status: 'error', service: 'agent-service' })],
] as const) {
  test(`rejects ${label} instead of reporting connected`, async () => {
    mock.method(globalThis, 'fetch', async () => response.clone())
    await assert.rejects(fetchHealth())
  })
}

test('rejects a failed network request', async () => {
  mock.method(globalThis, 'fetch', async () => { throw new TypeError('network failed') })
  await assert.rejects(fetchHealth())
})

test('forwards cancellation to the active request', async () => {
  const controller = new AbortController()
  mock.method(globalThis, 'fetch', (_url: string, init: RequestInit) => new Promise((_, reject) => {
    init.signal!.addEventListener('abort', () => reject(init.signal!.reason), { once: true })
  }))
  const pending = fetchHealth(controller.signal)
  controller.abort()
  await assert.rejects(pending, { name: 'AbortError' })
})

test('aborts a stalled request after five seconds', async () => {
  mock.timers.enable({ apis: ['setTimeout'] })
  try {
    let aborted = false
    mock.method(globalThis, 'fetch', (_url: string, init: RequestInit) => new Promise((_, reject) => {
      init.signal!.addEventListener('abort', () => {
        aborted = true
        reject(init.signal!.reason)
      }, { once: true })
    }))
    const pending = fetchHealth()
    mock.timers.tick(4999)
    assert.equal(aborted, false)
    mock.timers.tick(1)
    await assert.rejects(pending, { name: 'TimeoutError' })
    assert.equal(aborted, true)
  } finally {
    mock.timers.reset()
  }
})
