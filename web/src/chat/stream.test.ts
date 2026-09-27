import { ApiError } from '../api/errors.ts'
import assert from 'node:assert/strict'
import { afterEach, mock, test } from 'node:test'

const request = { request_id: 'request-1', conversation_id: 'conversation-1', content: '안녕' }
const frame = (event: string, extra = {}) => `event: ${event}\ndata: ${JSON.stringify({ request_id: 'request-1', message_id: 'message-1', conversation_id: 'conversation-1', user_message_id: 'user-1', ...extra })}\n\n`
afterEach(() => mock.restoreAll())

function response(text: string, bytewise = false) {
  const bytes = new TextEncoder().encode(text)
  return new Response(new ReadableStream({ start(controller) {
    if (bytewise) for (const byte of bytes) controller.enqueue(Uint8Array.of(byte))
    else controller.enqueue(bytes)
    controller.close()
  } }), { headers: { 'Content-Type': 'text/event-stream' } })
}

test('parses fragmented UTF-8, comments and several events without losing Korean text', async () => {
  const { streamChat } = await import('./stream.ts')
  mock.method(globalThis, 'fetch', async (url: string, init: RequestInit) => {
    assert.equal(url, '/api/chat')
    assert.equal(init.method, 'POST')
    assert.deepEqual(JSON.parse(init.body as string), request)
    return response(': heartbeat\n\n' + frame('start') + frame('delta', { text: '안녕\n하세요' }) + frame('done'), true)
  })
  const events: { type: string; text?: string }[] = []
  await streamChat(request, new AbortController().signal, e => events.push(e))
  assert.deepEqual(events.map(e => e.type), ['start', 'delta', 'done'])
  assert.equal(events[1].text, '안녕\n하세요')
})

for (const [label, body] of [
  ['truncated', frame('start') + frame('delta', { text: '일부' })],
  ['out of order', frame('delta', { text: 'x' })],
  ['invalid payload', frame('start') + frame('delta', { text: 42 })],
  ['wrong conversation', frame('start', { conversation_id: 'other' })],
  ['wrong request', frame('start', { request_id: 'old-request' })],
  ['malformed JSON', 'event: start\ndata: {bad}\n\n'],
  ['message changed', frame('start') + frame('done', { message_id: 'other' })],
] as const) {
  test(`rejects ${label} instead of completing the response`, async () => {
    const { streamChat } = await import('./stream.ts')
    mock.method(globalThis, 'fetch', async () => response(body))
    await assert.rejects(streamChat(request, new AbortController().signal, () => {}))
  })
}

test('provider and HTTP errors use safe error codes, not arbitrary upstream text', async () => {
  const { streamChat } = await import('./stream.ts')
  for (const result of [
    response(frame('start') + frame('error', { code: 'provider_auth', message: 'secret', retryable: false })),
    Response.json({ error: { code: 'provider_auth', message: 'secret', retryable: false } }, { status: 503 }),
  ]) {
    mock.method(globalThis, 'fetch', async () => result)
    await assert.rejects(streamChat(request, new AbortController().signal, () => {}), e => {
      assert.ok(e instanceof Error)
      assert.ok(!e.message.includes('secret'))
      return true
    })
  }
})

test('step limit preserves partial text and reports a non-retryable workflow failure', async () => {
  const { streamChat } = await import('./stream.ts')
  mock.method(globalThis, 'fetch', async () => response(
    frame('start') + frame('delta', { text: '일부 결과' }) + frame('error', {
      code: 'agent_step_limit', message: 'private graph state', retryable: false,
    }),
  ))
  const seen: { type: string; text?: string }[] = []
  await assert.rejects(streamChat(request, new AbortController().signal, e => seen.push(e)), e => {
    assert.ok(e instanceof ApiError)
    assert.equal(e.code, 'agent_step_limit')
    assert.equal(e.retryable, false)
    assert.match(e.message, /범위/)
    assert.ok(!e.message.includes('private'))
    return true
  })
  assert.deepEqual(seen.map(e => e.type), ['start', 'delta'])
  assert.equal(seen[1].text, '일부 결과')
})
