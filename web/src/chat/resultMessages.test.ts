import assert from 'node:assert/strict'
import { test } from 'node:test'
import { mergeSavedMessages, latestChatMessage } from './resultMessages.ts'
import type { ChatMessage } from './types.ts'

const saved = (id: string, seq: number, text = id): ChatMessage => ({ id, seq, role: 'assistant', text, status: 'completed' })

test('background results merge once in server order while retaining earlier pages', () => {
  const current = [saved('old', 1), saved('reply', 9)]
  const remote = [saved('reply', 9), saved('result', 10)]
  const merged = mergeSavedMessages(current, remote)
  assert.deepEqual(merged.map(m => m.id), ['old', 'reply', 'result'])
  assert.deepEqual(mergeSavedMessages(merged, remote), merged)
})

test('a delayed report fetch cannot overwrite an in-progress local response', () => {
  const streaming = { ...saved('reply', 9, '최신 응답 조각'), status: 'streaming' as const }
  const draft = { id: 'submitting', role: 'assistant' as const, text: '', status: 'submitting' as const }
  const merged = mergeSavedMessages([saved('old', 1), streaming, draft], [saved('reply', 9, '오래된 응답'), saved('result', 10)])
  assert.equal(merged.find(m => m.id === 'reply')?.text, '최신 응답 조각')
  assert.equal(merged.at(-1)?.id, 'submitting')
  assert.equal(merged.filter(m => m.id === 'result').length, 1)
})

test('a local stop survives a server response captured before cancellation settled', () => {
  const stopped = { ...saved('reply', 9), status: 'stopped' as const, retryable: true }
  const stale = { ...saved('reply', 9), status: 'streaming' as const }
  assert.equal(mergeSavedMessages([stopped], [stale])[0].status, 'stopped')
})

test('background reports do not hide retry, but a newer ordinary message does', () => {
  const stopped = { ...saved('reply', 9), status: 'stopped' as const, retryable: true }
  const result = { ...saved('result', 10), kind: 'call_result' as const }
  assert.equal(latestChatMessage([stopped, result])?.id, 'reply')
  assert.equal(latestChatMessage([stopped, result, saved('new', 11)])?.retryable, undefined)
})
