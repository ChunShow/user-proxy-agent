import type { PhoneCall } from './calls'

export function callResult(call: PhoneCall): { note: string; summary: string } {
  if (call.status !== 'ended') return { note: '', summary: '' }
  if (call.stop_requested || call.outcome === 'canceled') {
    return { note: '종료 요청에 따라 마쳤습니다.', summary: '' }
  }
  if (call.error_code) return { note: '', summary: '' }
  const summary = call.reported_summary
  if (call.outcome === 'model_reported_success') {
    return { note: '통화 도우미가 필요한 답을 재확인했다고 보고했습니다.', summary }
  }
  if (call.outcome !== 'incomplete') return { note: '', summary: '' }
  let end: Record<string, unknown> = {}
  try {
    const value: unknown = typeof call.end_report === 'string' ? JSON.parse(call.end_report) : null
    if (value && typeof value === 'object' && !Array.isArray(value)) end = value as Record<string, unknown>
  } catch { /* Older or malformed reports retain the ordinary incomplete result. */ }
  if (end.reason === 'goal_achieved') {
    if (end.status === 'audio_drained') return {
      note: '요청을 마쳤다고 보고했습니다. 마지막 음성의 재생 완료는 확정하지 못했습니다.', summary,
    }
    if (end.status === 'playback_unconfirmed') return {
      note: '음성 재생 확인을 기다리다 통화를 마쳤습니다.', summary,
    }
  }
  return { note: '확인하지 못한 내용이 남아 있습니다.', summary }
}
