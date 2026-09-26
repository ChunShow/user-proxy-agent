import assert from 'node:assert/strict'
import { test } from 'node:test'
import { mergeActivity, transcriptRows, latestAudioProgress, audioLabel, validateActivityPage } from './transcriptData.ts'
import type { CallActivityEvent } from './calls.ts'

function part(id: number, role: string, text: string, created_at = id / 10): CallActivityEvent {
  return { id, call_id: 'call1', kind: 'transcript', content: { role, text }, created_at }
}

test('out of order pages and duplicates produce intact Korean text with stable speaker rows', () => {
  const events = mergeActivity([part(2, 'caller', ' 3시')], [
    part(3, 'caller', '요.'), part(1, 'caller', '오후'), part(2, 'caller', ' 3시'),
    part(4, 'assistant', '확인할게요.'), part(5, 'assistant', '가능합니다.', 3),
  ])
  assert.deepEqual(transcriptRows(events).map(r => [r.id, r.role, r.text]), [
    [1, 'caller', '오후 3시요.'], [4, 'assistant', '확인할게요.'], [5, 'assistant', '가능합니다.'],
  ])
})

test('unknown events and empty text do not enter the transcript or claim a sentence was played', () => {
  const events = [part(1, 'caller', ''), part(2, 'stranger', 'ignore'), part(3, 'assistant', '<script>hello</script>'),
    { id: 4, call_id: 'call1', kind: 'audio_progress', created_at: 1,
      content: { generated_bytes: 1600, sent_bytes: 800, playback_acked_bytes: 0, interrupted: false } }]
  const rows = transcriptRows(events)
  assert.equal(rows.length, 1)
  assert.equal(rows[0].text, '<script>hello</script>')
  assert.equal('played' in rows[0], false)
  const progress = latestAudioProgress(events)
  assert.equal(audioLabel(progress, false, 2), '음성 전송 중')
  assert.equal(audioLabel(progress, false, 10), '음성 상태 확인 중')
  assert.equal(audioLabel(progress, true, 10), '음성 전송 종료')
})

test('audio acknowledgement stays independent of generated text and explicit stop is preserved', () => {
  const event: CallActivityEvent = { id: 1, call_id: 'call1', kind: 'audio_progress', created_at: 1,
    content: { generated_bytes: 1600, sent_bytes: 1600, playback_acked_bytes: 800, interrupted: false } }
  assert.equal(audioLabel(latestAudioProgress([event]), false, 1), '음성 재생 확인 중')
  event.content.playback_acked_bytes = 1600
  assert.equal(audioLabel(latestAudioProgress([event]), false, 1), '음성 재생 확인 수신')
  event.content.interrupted = true
  assert.equal(audioLabel(latestAudioProgress([event]), true, 1), '종료 요청으로 음성 전송 중단')
  assert.equal(audioLabel(null, true, 1), '음성 전송 기록 없음')
})

test('invalid pages cannot advance a cursor or merge another calls private transcript', () => {
  const page = { events: [part(3, 'caller', 'test')], questions: [], next_after: 3, has_more: false, terminal: false }
  assert.equal(validateActivityPage(page, 'call1', 0).next_after, 3)
  assert.throws(() => validateActivityPage(page, 'other', 0))
  assert.throws(() => validateActivityPage({ ...page, next_after: 9 }, 'call1', 0))
  assert.throws(() => validateActivityPage({ ...page, events: [], has_more: true }, 'call1', 0))
  assert.throws(() => validateActivityPage({ status: 'connected' }, 'call1', 0))
})
