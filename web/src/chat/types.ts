export interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'notice'
  text: string
}
