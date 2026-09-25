import { request } from './conversations'

export type PhoneStatus = 'preparing' | 'dialing' | 'connected' | 'ending' | 'ended' | 'failed' | 'canceled' | 'unknown'
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
}
interface Page { items: PhoneCall[]; next_cursor: string | null }
export const isActiveCall = (call: PhoneCall) => !['ended', 'failed', 'canceled'].includes(call.status)
export const activeCalls = (signal?: AbortSignal) => request<{ items: PhoneCall[] }>('/api/calls/active', undefined, signal)
export const listCalls = (id: string, cursor?: string, signal?: AbortSignal) => request<Page>(`/api/conversations/${encodeURIComponent(id)}/calls${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`, undefined, signal)
export const stopCall = (id: string) => request<PhoneCall>(`/api/calls/${encodeURIComponent(id)}/stop`, {})
export const refreshCall = (id: string) => request<PhoneCall>(`/api/calls/${encodeURIComponent(id)}/refresh`, {})
export function mergeCalls(previous: Record<string, PhoneCall>, incoming: PhoneCall[]) {
  const next = { ...previous }
  for (const call of incoming) if (!next[call.id] || call.version >= next[call.id].version) next[call.id] = call
  return next
}
