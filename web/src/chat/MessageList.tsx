import type { ChatMessage } from './types'

export default function MessageList({ messages, label = '대화 내역', onRetry }: {
  messages: ChatMessage[]; label?: string; onRetry?: () => void
}) {
  return <ul className="messages" aria-label={label}>{messages.map((message, index) => <li className={`message ${message.role}`} key={message.id}>
    <span className="message-author">{message.role === 'user' ? '나' : message.role === 'assistant' ? 'user proxy agent' : '안내'}</span>
    {message.text && <div className="message-bubble">{message.text}</div>}
    {message.status && <div className="reply-feedback">
      <span role="status" className={message.status === 'completed' ? 'sr-only' : ''}>
        {message.status === 'submitting' ? '답변을 준비하고 있습니다.'
          : message.status === 'streaming' ? '답변 작성 중'
          : message.status === 'stopped' ? '응답을 중단했습니다.'
          : message.status === 'failed' ? message.error : '답변이 완료되었습니다.'}
      </span>
      {onRetry && index === messages.length - 1 && message.retryable &&
        <button type="button" onClick={onRetry}>다시 시도</button>}
    </div>}
  </li>)}</ul>
}
