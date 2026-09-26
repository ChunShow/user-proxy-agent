import type { CallActivityEvent, CallActivityPage } from './calls.ts'

export interface TranscriptRow { id: number; role: 'caller' | 'assistant'; text: string; createdAt: number }
export interface AudioProgress {
  generated: number; sent: number; acked: number; interrupted: boolean; createdAt: number
}

export function validateActivityPage(input: unknown, callId: string, after: number): CallActivityPage {
  const value = input as CallActivityPage
  if (!value || !Array.isArray(value.events) || value.events.length > 100
    || !Number.isSafeInteger(value.next_after) || typeof value.has_more !== 'boolean'
    || typeof value.terminal !== 'boolean') throw new Error('invalid_activity')
  let cursor = after
  for (const event of value.events) {
    if (!Number.isSafeInteger(event.id) || event.id <= cursor || event.call_id !== callId
      || !Number.isFinite(event.created_at) || typeof event.kind !== 'string'
      || !event.content || typeof event.content !== 'object') throw new Error('invalid_activity')
    cursor = event.id
  }
  if (cursor !== value.next_after || (value.has_more && !value.events.length)) throw new Error('invalid_activity')
  return value
}

export function mergeActivity(previous: CallActivityEvent[], incoming: CallActivityEvent[]) {
  const events = new Map(previous.map(event => [event.id, event]))
  for (const event of incoming) if (!events.has(event.id)) events.set(event.id, event)
  return [...events.values()].sort((a, b) => a.id - b.id)
}

export function transcriptRows(events: CallActivityEvent[]): TranscriptRow[] {
  const rows: TranscriptRow[] = []
  let lastAt = -Infinity
  for (const event of events) {
    if (event.kind !== 'transcript') continue
    const { role, text } = event.content
    if ((role !== 'caller' && role !== 'assistant') || typeof text !== 'string' || !text) continue
    const last = rows.at(-1)
    if (last && last.role === role && event.created_at - lastAt < 2) last.text += text
    else rows.push({ id: event.id, role, text, createdAt: event.created_at })
    lastAt = event.created_at
  }
  return rows
}

export function latestAudioProgress(events: CallActivityEvent[]): AudioProgress | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const event = events[i]
    if (event.kind !== 'audio_progress') continue
    const { generated_bytes: generated, sent_bytes: sent, playback_acked_bytes: acked, interrupted } = event.content
    if (typeof generated === 'number' && typeof sent === 'number' && typeof acked === 'number'
      && [generated, sent, acked].every(n => Number.isSafeInteger(n) && n >= 0)
      && generated >= sent && sent >= acked && typeof interrupted === 'boolean') {
      return { generated, sent, acked, interrupted, createdAt: event.created_at }
    }
  }
  return null
}

export function audioLabel(progress: AudioProgress | null, terminal: boolean, now = Date.now() / 1000): string {
  if (!progress) return terminal ? '음성 전송 기록 없음' : '음성 상태 확인 중'
  if (progress.interrupted) return '종료 요청으로 음성 전송 중단'
  if (terminal) return '음성 전송 종료'
  if (now - progress.createdAt > 5 || !progress.generated) return '음성 상태 확인 중'
  if (progress.generated > progress.sent) return '음성 전송 중'
  if (progress.sent > progress.acked) return '음성 재생 확인 중'
  return '음성 재생 확인 수신'
}
