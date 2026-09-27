export interface ChatMessage {
  id: string
  seq?: number
  kind?: 'chat' | 'call_result'
  role: 'user' | 'assistant' | 'notice'
  text: string
  status?: 'submitting' | 'streaming' | 'completed' | 'stopped' | 'failed' | 'interrupted'
  error?: string
  retryable?: boolean
}
