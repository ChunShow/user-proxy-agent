import { ApiError } from '../api/errors'
import { request } from '../api/request'

import type { ChatMessage } from './types'

export type ConversationMode = 'real' | 'simulation'
export interface Conversation { id: string; title: string; updated_at: string; mode?: ConversationMode }
export interface ConversationPage { items: Conversation[]; next_cursor: string | null }
export interface SavedConversation {
  conversation: Conversation
  messages: (ChatMessage & { error_code?: string })[]
  next_cursor: string | null
}
export const prepareSession = () => request<void>('/api/session', {})
export const listConversations = (cursor?: string) => request<ConversationPage>(`/api/conversations${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`)
export const createConversation = (id: string, signal: AbortSignal, mode: ConversationMode = 'real') => request<Conversation>('/api/conversations', { conversation_id: id, mode }, signal)
export async function getConversation(id: string, before?: string): Promise<SavedConversation> {
  const data = await request<SavedConversation>(`/api/conversations/${encodeURIComponent(id)}${before ? `?before=${encodeURIComponent(before)}` : ''}`)
  data.messages = data.messages.map(m => ({ ...m, status: m.role === 'assistant' ? m.status : undefined, error: m.error_code ? new ApiError(m.error_code).message : undefined }))
  return data
}

export const renameConversation = (id: string, title: string) => request<Conversation>(`/api/conversations/${encodeURIComponent(id)}/rename`, { title })
export const deleteConversation = (id: string) => request<void>(`/api/conversations/${encodeURIComponent(id)}/delete`, {})
export const generateTitle = (id: string) => request<void>(`/api/conversations/${encodeURIComponent(id)}/title`, {})
