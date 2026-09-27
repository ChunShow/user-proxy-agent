import assert from 'node:assert/strict'
import { test } from 'node:test'
import { callResult } from './callResult.ts'
import type { PhoneCall } from './calls.ts'

const base = { status: 'ended', outcome: 'incomplete', reported_summary: '합성 결과',
  error_code: null, stop_requested: false } as PhoneCall
const report = (status: string, reason = 'goal_achieved') => JSON.stringify({ status, reason })

test('live completion separates model report from playback certainty', () => {
  assert.match(callResult({ ...base, end_report: report('audio_drained') }).note, /요청을 마쳤다고 보고/)
  assert.match(callResult({ ...base, end_report: report('audio_drained') }).note, /실제 청취 여부는 확인할 수 없습니다/)
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
    report('unknown')]) {
    assert.match(callResult({ ...base, end_report }).note, /확인하지 못한 내용/)
  }
  assert.match(callResult({ ...base, outcome: 'model_reported_success' }).note, /재확인했다고 보고/)
})

test('playback evidence remains distinct from the business outcome for every known end reason', () => {
  for (const reason of ['goal_achieved', 'recipient_declined', 'unable_to_continue']) {
    const result = callResult({ ...base, end_report: report('audio_drained', reason) })
    assert.match(result.note, /마지막 음성 전송과 무음을 확인한 뒤 통화를 종료/)
    assert.match(result.note, /실제 청취 여부는 확인할 수 없습니다/)
    if (reason !== 'goal_achieved') assert.doesNotMatch(result.note, /요청을 마쳤다고/)
    assert.equal(result.summary, '합성 결과')
  }
})

test('timeout and unknown reports never claim that audio transmission was confirmed', () => {
  for (const reason of ['goal_achieved', 'recipient_declined', 'unable_to_continue']) {
    const result = callResult({ ...base, end_report: report('playback_unconfirmed', reason) })
    assert.match(result.note, /음성 재생 확인을 기다리다/)
    assert.doesNotMatch(result.note, /무음을 확인한 뒤/)
  }
  assert.doesNotMatch(callResult({ ...base, end_report: report('audio_drained', 'unknown') }).note, /무음을 확인한 뒤/)
})
