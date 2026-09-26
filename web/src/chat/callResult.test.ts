import assert from 'node:assert/strict'
import { test } from 'node:test'
import { callResult } from './callResult.ts'
import type { PhoneCall } from './calls.ts'

const base = { status: 'ended', outcome: 'incomplete', reported_summary: '합성 결과',
  error_code: null, stop_requested: false } as PhoneCall
const report = (status: string, reason = 'goal_achieved') => JSON.stringify({ status, reason })

test('live completion separates model report from playback certainty', () => {
  assert.match(callResult({ ...base, end_report: report('audio_drained') }).note, /요청을 마쳤다고 보고/)
  assert.match(callResult({ ...base, end_report: report('audio_drained') }).note, /재생 완료는 확정하지/)
  assert.match(callResult({ ...base, end_report: report('playback_unconfirmed') }).note, /기다리다/)
  assert.equal(callResult({ ...base, end_report: report('audio_drained') }).summary, '합성 결과')
})

test('failed, active and user-stopped calls never show a goal achievement report', () => {
  for (const patch of [{ error_code: 'call_failed' }, { status: 'failed' as const },
    { status: 'connected' as const }, { stop_requested: true }, { outcome: 'canceled' as const }]) {
    const result = callResult({ ...base, end_report: report('audio_drained'), ...patch })
    assert.doesNotMatch(result.note, /요청을 마쳤다고/)
    assert.equal(result.summary, '')
  }
})

test('missing, invalid and unknown reports retain the existing incomplete result', () => {
  for (const end_report of [undefined, '{bad', 'null', '[]', '1', '{}',
    report('unknown'), report('audio_drained', 'recipient_declined'), report('audio_drained', 'unable_to_continue')]) {
    assert.match(callResult({ ...base, end_report }).note, /확인하지 못한 내용/)
  }
  assert.match(callResult({ ...base, outcome: 'model_reported_success' }).note, /재확인했다고 보고/)
})
