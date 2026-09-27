import { ApiError } from './errors.ts'
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { request } from './request'

test('JSON requests preserve session cookies, disable caching and send an explicit body', async t => {
  const calls: RequestInit[] = []
  t.mock.method(globalThis, 'fetch', async (path: string, options: RequestInit) => {
    assert.equal(path, '/api/example')
    calls.push(options)
    return Response.json({ ok: true })
  })
  assert.deepEqual(await request('/api/example'), { ok: true })
  assert.deepEqual(await request('/api/example', { answer: '네' }), { ok: true })
  assert.equal(calls[0].method, 'GET')
  assert.equal(calls[0].body, undefined)
  assert.equal(calls[1].method, 'POST')
  assert.equal(calls[1].body, JSON.stringify({ answer: '네' }))
  assert.deepEqual(calls[1].headers, { 'Content-Type': 'application/json' })
  for (const call of calls) {
    assert.equal(call.credentials, 'same-origin')
    assert.equal(call.cache, 'no-store')
    assert.ok(call.signal instanceof AbortSignal)
  }
})

test('empty successful responses do not require JSON', async t => {
  t.mock.method(globalThis, 'fetch', async () => new Response(null, { status: 204 }))
  assert.equal(await request('/api/example', {}), undefined)
})

test('API errors retain the code but never expose upstream text', async t => {
  t.mock.method(globalThis, 'fetch', async () => Response.json(
    { error: { code: 'session_expired', message: 'private upstream details' } }, { status: 401 },
  ))
  await assert.rejects(request('/api/example'), error => {
    assert.ok(error instanceof ApiError)
    assert.equal(error.code, 'session_expired')
    assert.equal(error.retryable, false)
    assert.ok(!error.message.includes('private upstream'))
    return true
  })
})

test('network failures and malformed error responses have a safe fallback', async t => {
  const fetch = t.mock.method(globalThis, 'fetch', async () => { throw new Error('private network details') })
  const isSafe = (error: unknown) => error instanceof ApiError && error.code === 'load_failed'
  await assert.rejects(request('/api/example'), isSafe)
  fetch.mock.mockImplementation(async () => new Response('not JSON', { status: 502 }))
  await assert.rejects(request('/api/example'), isSafe)
})

test('caller cancellation reaches a pending request and releases it', async t => {
  const controller = new AbortController()
  let signal: AbortSignal | null | undefined
  t.mock.method(globalThis, 'fetch', (_path: string, options: RequestInit) => {
    signal = options.signal
    return new Promise<Response>((_resolve, reject) => {
      signal?.addEventListener('abort', () => reject(signal?.reason), { once: true })
    })
  })
  const pending = request('/api/example', undefined, controller.signal)
  controller.abort()
  assert.ok(signal?.aborted)
  await assert.rejects(pending, error => error instanceof ApiError && error.code === 'load_failed')
})
