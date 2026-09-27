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
  if (typeof end.reason === 'string' && ['goal_achieved', 'recipient_declined', 'unable_to_continue'].includes(end.reason)) {
    const outcome = end.reason === 'goal_achieved' ? '요청을 마쳤다고 보고했습니다. '
      : end.reason === 'recipient_declined' ? '상대방이 통화를 거절했다고 보고했습니다. '
      : '요청을 완료하지 못했다고 보고했습니다. '
    if (end.status === 'audio_drained') return {
      note: outcome + '마지막 음성 전송과 무음을 확인한 뒤 통화를 종료했습니다. 실제 청취 여부는 확인할 수 없습니다.', summary,
    }
    if (end.status === 'playback_unconfirmed') return {
      note: outcome + '음성 재생 확인을 기다리다 통화를 마쳤습니다.', summary,
    }
  }
  return { note: '확인하지 못한 내용이 남아 있습니다.', summary }
}
