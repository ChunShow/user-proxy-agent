import assert from 'node:assert/strict'
import { test } from 'node:test'
import { mergeActivity, transcriptRows, transcriptRevision, latestAudioProgress, audioLabel, validateActivityPage } from './transcriptData.ts'
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

test('discarded barge-in audio is neither pending transmission nor acknowledged playback', () => {
  const event: CallActivityEvent = { id: 1, call_id: 'call1', kind: 'audio_progress', created_at: 1,
    content: { generated_bytes: 2400, sent_bytes: 1600, playback_acked_bytes: 1600,
      dropped_audio_bytes: 800, cleared_unacked_bytes: 400, interrupted: false } }
  const progress = latestAudioProgress([event])
  assert.equal(progress?.acked, 1200)
  assert.equal(audioLabel(progress, false, 1), '음성 재생 확인 수신')
})

function timed(id: number, role: string, text: string, start: number, end: number, arrival = id / 10) {
  return { ...part(id, role, text, arrival), content: { role, text, start_ms: start, end_ms: end } }
}

test('interleaved timed syllables and punctuation stay in their own utterances', () => {
  const events = [
    timed(1, 'caller', ' 네, 여보', 1600, 2000),
    timed(2, 'assistant', ' 안녕하세요', 2000, 2200),
    timed(3, 'caller', '세요', 2200, 2400),
    timed(4, 'assistant', ', 질문 하나 드려도 될까요?', 2400, 7800),
    timed(5, 'caller', ' 아 네, 괜찮', 11400, 12200),
    timed(6, 'assistant', ' 감사합니다', 12000, 12200),
    timed(7, 'caller', '아요', 12200, 12400),
    timed(8, 'assistant', '. 내일 시간 되세요?', 12600, 14400),
  ]
  assert.deepEqual(transcriptRows(events).map(r => [r.id, r.role, r.text]), [
    [1, 'caller', ' 네, 여보세요'],
    [2, 'assistant', ' 안녕하세요, 질문 하나 드려도 될까요?'],
    [5, 'caller', ' 아 네, 괜찮아요'],
    [6, 'assistant', ' 감사합니다. 내일 시간 되세요?'],
  ])
  assert.equal(transcriptRows(events).map(r => r.text.length).reduce((a, b) => a + b, 0),
    events.reduce((sum, e) => sum + e.content.text.length, 0))
})

test('audio time tolerates delivery delays but separates long pauses and new answers', () => {
  const rows = transcriptRows([
    timed(1, 'caller', '안녕', 0, 200, 1),
    timed(2, 'assistant', '네?', 200, 400, 2),
    timed(3, 'caller', '하세요.', 400, 600, 20),
    timed(4, 'assistant', '반갑습니다.', 800, 1200, 20.1),
    timed(5, 'caller', '질문이 있어요.', 1250, 1500, 20.2),
    timed(6, 'caller', '다음 질문입니다.', 5000, 6000, 20.3),
  ])
  assert.deepEqual(rows.map(r => r.text), ['안녕하세요.', '네?', '반갑습니다.', '질문이 있어요.', '다음 질문입니다.'])
})

test('missing or invalid timing never joins across another speaker', () => {
  const events = [part(1, 'caller', '안녕'), part(2, 'assistant', '네?'), part(3, 'caller', '하세요')]
  assert.equal(transcriptRows(events).length, 3)
  assert.equal(transcriptRows(events.map(e => ({ ...e, content: { ...e.content, start_ms: null, end_ms: -1 } }))).length, 3)
})

test('late fragments update the revision even when the last row is unchanged', () => {
  const initial = [timed(1, 'caller', '안녕', 0, 200), timed(2, 'assistant', '네?', 200, 400)]
  const before = transcriptRows(initial)
  const after = transcriptRows(mergeActivity(initial, [timed(3, 'caller', '하세요', 400, 600)]))
  assert.deepEqual(before.at(-1), after.at(-1))
  assert.notEqual(transcriptRevision(before), transcriptRevision(after))
  assert.deepEqual(after, transcriptRows(mergeActivity(initial, [...initial, timed(3, 'caller', '하세요', 400, 600)])))
})
