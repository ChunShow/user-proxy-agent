import type { ChatMessage } from './types'

export default function MessageList({ messages, label = '대화 내역' }: { messages: ChatMessage[]; label?: string }) {
  return <ul className="messages" aria-label={label}>{messages.map(message => <li className={`message ${message.role}`} key={message.id}>
    <span className="message-author">{message.role === 'user' ? '나' : message.role === 'assistant' ? 'user proxy agent' : '안내'}</span>
    <div className="message-bubble">{message.text}</div>
  </li>)}</ul>
}
