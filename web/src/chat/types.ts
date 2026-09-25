export interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'notice'
  text: string
  status?: 'submitting' | 'streaming' | 'completed' | 'stopped' | 'failed'
  error?: string
  retryable?: boolean
}

export type TaskStatus = 'working' | 'needs_input' | 'succeeded' | 'incomplete'
export type CallStatus = 'dialing' | 'connected' | 'ending' | 'ended' | 'failed'
export type CallAction = 'listen' | 'stop_listening' | 'instruct' | 'end'
export interface CallViewModel {
  id: string
  subject: string
  purpose: string
  taskStatus: TaskStatus
  callStatus: CallStatus
  summary: string
  transcript: { id: string; speaker: 'agent' | 'counterpart'; text: string; final: boolean }[]
  listening: boolean
}
