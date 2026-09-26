import { request } from '../chat/conversations'

export interface GoogleConnection {
  status: 'not_configured' | 'disconnected' | 'connected' | 'reconnect_required'
  email: string | null
  calendar_read: boolean
  gmail_read: boolean
  calendar_write?: boolean
  gmail_send?: boolean
}
export const getGoogle = (signal?: AbortSignal) => request<GoogleConnection>('/api/integrations/google', undefined, signal)
export const connectGoogle = () => request<{ url: string }>('/api/integrations/google/connect', {})
export const disconnectGoogle = () => request<{ status: string; revoked: boolean }>('/api/integrations/google/disconnect', {})
