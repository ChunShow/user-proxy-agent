import { useEffect, useRef, useState } from 'react'
import { ChatError, streamChat } from './stream'
import type { ChatRequest } from './stream'
import type { ChatMessage } from './types'

function context(messages: ChatMessage[]): ChatRequest['messages'] {
  return messages.flatMap<ChatRequest['messages'][number]>(message => {
    if (message.role === 'user') return [{ role: 'user' as const, content: message.text }]
    if (message.role === 'assistant' && message.status === 'completed') return [{ role: 'assistant' as const, content: message.text }]
    return []
  })
}

export default function useChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [busy, setBusy] = useState(false)
  const latest = useRef<ChatMessage[]>([])
  const active = useRef<{ controller: AbortController; responseId: string } | null>(null)

  function replace(next: ChatMessage[]) { latest.current = next; setMessages(next) }
  function patch(id: string, update: Partial<ChatMessage>) {
    replace(latest.current.map(message => message.id === id ? { ...message, ...update } : message))
  }
  function stop() {
    const current = active.current
    if (!current) return
    active.current = null
    current.controller.abort()
    patch(current.responseId, { status: 'stopped', error: undefined, retryable: true })
    setBusy(false)
  }
  async function run(history: ChatMessage[], replacing?: string) {
    const responseId = crypto.randomUUID()
    const current = { controller: new AbortController(), responseId }
    active.current = current
    setBusy(true)
    const reply: ChatMessage = { id: responseId, role: 'assistant', text: '', status: 'submitting' }
    replace(replacing ? latest.current.map(m => m.id === replacing ? reply : m) : [...latest.current, reply])
    let text = ''
    try {
      await streamChat({ request_id: crypto.randomUUID(), messages: context(history) }, current.controller.signal, event => {
        if (active.current !== current) return
        if (event.type === 'delta') {
          text += event.text ?? ''
          patch(responseId, { text, status: 'streaming' })
        } else if (event.type === 'done') patch(responseId, { status: 'completed' })
      })
    } catch (error) {
      if (active.current !== current) return
      const failure = error instanceof ChatError ? error : new ChatError('provider_unavailable')
      patch(responseId, { status: 'failed', error: failure.message, retryable: failure.retryable })
    } finally {
      if (active.current === current) { active.current = null; setBusy(false) }
    }
  }
  function send(text: string) {
    if (active.current || !text.trim()) return false
    const next = [...latest.current, { id: crypto.randomUUID(), role: 'user' as const, text }]
    replace(next)
    void run(next)
    return true
  }
  function retry() {
    const last = latest.current.at(-1)
    if (active.current || !last?.retryable || !['failed', 'stopped'].includes(last.status ?? '')) return
    void run(latest.current.slice(0, -1), last.id)
  }
  useEffect(() => () => {
    const current = active.current
    active.current = null
    current?.controller.abort()
  }, [])
  return { messages, busy, send, stop, retry }
}
