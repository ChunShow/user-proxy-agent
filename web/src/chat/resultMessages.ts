import type { ChatMessage } from './types'

export function mergeSavedMessages(current: ChatMessage[], incoming: ChatMessage[]): ChatMessage[] {
  const byId = new Map(current.map(message => [message.id, message]))
  for (const message of incoming) {
    const local = byId.get(message.id)
    if (local?.status === 'streaming' || local?.status === 'submitting'
      || (local?.status === 'stopped' && message.status === 'streaming')) continue
    byId.set(message.id, message)
  }
  return [...byId.values()].sort((a, b) => (a.seq ?? Number.MAX_SAFE_INTEGER) - (b.seq ?? Number.MAX_SAFE_INTEGER))
}

export function latestChatMessage(messages: ChatMessage[]) {
  return messages.filter(message => message.kind !== 'call_result').at(-1)
}
