import { request } from './conversations'

export type PhoneStatus = 'preparing' | 'dialing' | 'connected' | 'ending' | 'ended' | 'failed' | 'canceled' | 'unknown'
export interface CallConfirmation {
  id: string
  revision: number
  question: string
  options: string[]
  status: 'pending' | 'answered' | 'applied' | 'expired' | 'canceled' | 'failed'
  answer: string | null
  expires_at: number
}
export interface PhoneCall {
  id: string
  conversation_id: string
  source_user_message_id: string
  destination: string
  subject: string
  purpose: string
  status: PhoneStatus
  outcome: 'pending' | 'model_reported_success' | 'incomplete' | 'canceled'
  reported_summary: string
  error_code: string | null
  stop_requested: boolean
  version: number
  confirmations?: CallConfirmation[]
}
interface Page { items: PhoneCall[]; next_cursor: string | null }
export interface CallActivityEvent {
  id: number
  call_id: string
  kind: string
  content: Record<string, unknown>
  created_at: number
}
export interface CallActivityPage {
  events: CallActivityEvent[]
  questions: CallConfirmation[]
  next_after: number
  has_more: boolean
  terminal: boolean
}
export const getCallActivity = (id: string, after: number, signal?: AbortSignal) =>
  request<CallActivityPage>(`/api/calls/${encodeURIComponent(id)}/activity?after=${after}`, undefined, signal)
export const isActiveCall = (call: PhoneCall) => !['ended', 'failed', 'canceled'].includes(call.status)
export const activeCalls = (signal?: AbortSignal) => request<{ items: PhoneCall[] }>('/api/calls/active', undefined, signal)
export const listCalls = (id: string, cursor?: string, signal?: AbortSignal) => request<Page>(`/api/conversations/${encodeURIComponent(id)}/calls${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`, undefined, signal)
export const stopCall = (id: string) => request<PhoneCall>(`/api/calls/${encodeURIComponent(id)}/stop`, {})
export const refreshCall = (id: string) => request<PhoneCall>(`/api/calls/${encodeURIComponent(id)}/refresh`, {})
export const answerCall = (id: string, question: CallConfirmation, answer: string, requestId: string) =>
  request<CallConfirmation>(`/api/calls/${encodeURIComponent(id)}/confirmations/${encodeURIComponent(question.id)}/answer`,
    { answer, expected_revision: question.revision, request_id: requestId })
export function mergeCalls(previous: Record<string, PhoneCall>, incoming: PhoneCall[]) {
  const next = { ...previous }
  for (const call of incoming) if (!next[call.id] || call.version >= next[call.id].version) next[call.id] = call
  return next
}
