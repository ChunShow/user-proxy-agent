import { Fragment, type ReactNode } from 'react'
import type { ChatMessage } from './types'

export default function MessageList({ messages, label = '대화 내역', onRetry, afterMessage }: {
  messages: ChatMessage[]; label?: string; onRetry?: () => void; afterMessage?: (id: string) => ReactNode
}) {
  return <ul className="messages" aria-label={label}>{messages.map((message, index) => <Fragment key={message.id}><li className={`message ${message.role}`}>
    <span className="message-author">{message.role === 'user' ? '나' : message.role === 'assistant' ? 'user proxy agent' : '안내'}</span>
    {message.text && <div className="message-bubble">{message.text}</div>}
    {message.status && <div className="reply-feedback">
      <span role="status" className={message.status === 'completed' ? 'sr-only' : ''}>
        {message.status === 'submitting' ? '답변을 준비하고 있습니다.'
          : message.status === 'streaming' ? '답변 작성 중'
          : message.status === 'stopped' ? '응답을 중단했습니다.'
          : message.status === 'interrupted' ? '연결이 끊겨 응답이 중단되었습니다.'
          : message.status === 'failed' ? message.error : '답변이 완료되었습니다.'}
      </span>
      {onRetry && index === messages.length - 1 && message.retryable &&
        <button type="button" onClick={onRetry}>다시 시도</button>}
    </div>}
  </li>{afterMessage?.(message.id)}</Fragment>)}</ul>
}
