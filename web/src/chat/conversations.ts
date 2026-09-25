import { ChatError } from './stream'
import type { ChatMessage } from './types'

export interface Conversation { id: string; title: string; updated_at: string }
export interface ConversationPage { items: Conversation[]; next_cursor: string | null }
export interface SavedConversation {
  conversation: Conversation
  messages: (ChatMessage & { error_code?: string })[]
  next_cursor: string | null
}
export async function request<T>(path: string, body?: object, signal?: AbortSignal): Promise<T> {
  try {
    const response = await fetch(path, {
      method: body ? 'POST' : 'GET', cache: 'no-store', credentials: 'same-origin',
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(15_000)]) : AbortSignal.timeout(15_000),
    })
    if (!response.ok) {
      const data = await response.json().catch(() => ({}))
      throw new ChatError(data.error?.code ?? 'load_failed')
    }
    return response.status === 204 ? undefined as T : await response.json() as T
  } catch (error) {
    if (error instanceof ChatError) throw error
    throw new ChatError('load_failed')
  }
}
export const prepareSession = () => request<void>('/api/session', {})
export const listConversations = (cursor?: string) => request<ConversationPage>(`/api/conversations${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`)
export const createConversation = (id: string, signal: AbortSignal) => request<Conversation>('/api/conversations', { conversation_id: id }, signal)
export async function getConversation(id: string, before?: string): Promise<SavedConversation> {
  const data = await request<SavedConversation>(`/api/conversations/${encodeURIComponent(id)}${before ? `?before=${encodeURIComponent(before)}` : ''}`)
  data.messages = data.messages.map(m => ({ ...m, status: m.role === 'assistant' ? m.status : undefined, error: m.error_code ? new ChatError(m.error_code).message : undefined }))
  return data
}
