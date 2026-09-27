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
  assert.match(callResult({ ...base, end_report: report('playback_unconfirmed') }).note, /확인되지 않았습니다/)
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
    assert.match(result.note, /음성 전송과 무음 조건/)
    assert.match(result.note, /실제 청취 여부는 확인할 수 없습니다/)
    if (reason !== 'goal_achieved') assert.doesNotMatch(result.note, /요청을 마쳤다고/)
    assert.equal(result.summary, '합성 결과')
  }
})

test('timeout and unknown reports never claim that audio transmission was confirmed', () => {
  for (const reason of ['goal_achieved', 'recipient_declined', 'unable_to_continue']) {
    const result = callResult({ ...base, end_report: report('playback_unconfirmed', reason) })
    assert.match(result.note, /음성 재생 완료는 확인되지 않았습니다/)
    assert.doesNotMatch(result.note, /무음을 확인한 뒤/)
  }
  assert.doesNotMatch(callResult({ ...base, end_report: report('audio_drained', 'unknown') }).note, /무음을 확인한 뒤/)
})


test('ordinary recipient end requests are not refusal or goal achievement', () => {
  const result = callResult({ ...base, end_report: report('audio_drained', 'recipient_requested_end') })
  assert.match(result.note, /상대방이 통화 종료를 요청/)
  assert.doesNotMatch(result.note, /거절|요청을 마쳤다고/)
})

test('audio drainage alone does not identify who hung up, including legacy refusal reports', () => {
  for (const reason of ['goal_achieved', 'recipient_requested_end', 'recipient_declined']) {
    const result = callResult({ ...base, end_report: report('audio_drained', reason) })
    assert.match(result.note, /종료 주체는 확인되지 않았습니다/)
    assert.doesNotMatch(result.note, /확인한 뒤 통화를 종료|자동으로 종료/)
  }
  assert.match(callResult({ ...base, end_report: report('audio_drained', 'recipient_declined') }).note, /거절 또는 종료 의사/)
})

test('carrier observations distinguish our hangup request from an already ended line', () => {
  for (const [carrier_action, expected] of [
    ['hangup_requested', /시스템의 회선 종료 요청이 기록/],
    ['already_ended', /종료 처리 시 회선이 이미 종료/],
    ['unknown', /종료 주체는 확인되지/],
  ] as const) {
    const result = callResult({ ...base, end_report: JSON.stringify({
      reason: 'recipient_requested_end', status: 'audio_drained', carrier_action,
    }) })
    assert.match(result.note, expected)
    assert.doesNotMatch(result.note, /상대방이 직접 끊|자동으로 종료/)
  }
})
