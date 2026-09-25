import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import ConnectionStatus from '../components/ConnectionStatus'
import Icon from '../components/Icon'
import type { ChatMessage } from './types'
import './chat.css'

interface Props {
  messages: ChatMessage[]
  preview: boolean
  onNavigate: (preview: boolean) => void
  composer: ReactNode
  children?: ReactNode
}

export default function ChatView({ messages, preview, onNavigate, composer, children }: Props) {
  const dialog = useRef<HTMLDialogElement>(null)
  const menuButton = useRef<HTMLButtonElement>(null)
  const bottom = useRef<HTMLDivElement>(null)
  const [menuOpen, setMenuOpen] = useState(false)
  const [collapsed, setCollapsed] = useState(false)
  useEffect(() => { bottom.current?.scrollIntoView({ block: 'nearest' }) }, [messages.length])
  function navigate(next: boolean) {
    onNavigate(next)
    dialog.current?.close()
  }
  const navigation = <nav aria-label="대화 탐색">
    <button aria-label="메인 대화" className={`nav-item ${!preview ? 'selected' : ''}`} aria-current={!preview ? 'page' : undefined} onClick={() => navigate(false)}><Icon name="chat" /><span>메인 대화</span></button>
    <button aria-label="화면 예시" className={`nav-item ${preview ? 'selected' : ''}`} aria-current={preview ? 'page' : undefined} onClick={() => navigate(true)}><Icon name="phone" /><span>화면 예시</span></button>
  </nav>
  return <div className={`app-shell ${collapsed ? 'nav-collapsed' : ''}`}>
    <aside className="sidebar">
      <div className="sidebar-brand"><span className="agent-mark" aria-hidden="true">a<span>·</span></span><span className="brand-name">agent service</span></div>
      {navigation}
      <div className="sidebar-bottom"><p>일을 맡기고,<br />대화는 이어가세요.</p>
        <button className="nav-collapse" aria-label={collapsed ? '탐색 영역 펼치기' : '탐색 영역 접기'} onClick={() => setCollapsed(!collapsed)}><Icon name="menu" /><span>{collapsed ? '펼치기' : '접기'}</span></button>
      </div>
    </aside>
    <dialog ref={dialog} className="mobile-menu" aria-label="탐색 메뉴" onClose={() => { setMenuOpen(false); menuButton.current?.focus() }}>
      <div className="menu-heading"><strong>agent service</strong><button aria-label="메뉴 닫기" onClick={() => dialog.current?.close()}><Icon name="close" /></button></div>
      {navigation}
    </dialog>
    <main className="chat-main">
      <header className="chat-header">
        <div className="assistant-heading">
          <button ref={menuButton} className="mobile-menu-button" aria-label="메뉴 열기" aria-expanded={menuOpen} onClick={() => { setMenuOpen(true); dialog.current?.showModal() }}><Icon name="menu" /></button>
          <div className="assistant-avatar" aria-hidden="true"><span /><span /></div>
          <div><h1>내 비서</h1><p>{preview ? '통화 화면 예시' : '당신의 일을 함께하는 대화'}</p></div>
        </div>
        <ConnectionStatus />
      </header>
      {preview && <div className="preview-banner">예시 데이터 · 실제 전화가 연결되지 않습니다</div>}
      <div className="conversation-scroll">
        <div className={`conversation ${messages.length === 0 && !preview ? 'is-empty' : ''}`}>
          {messages.length === 0 && !preview && <div className="empty-chat">
            <span className="empty-kicker">내 비서와의 첫 대화</span><h2>어떤 일을 도와드릴까요?</h2>
            <p>아직 비서가 연결되지 않았습니다.<br />메시지를 적어 화면을 먼저 살펴보세요.</p>
          </div>}
          <ul className="messages" aria-label="대화 내역">{messages.map(message => <li className={`message ${message.role}`} key={message.id}>
            <span className="message-author">{message.role === 'user' ? '나' : message.role === 'assistant' ? '비서' : '안내'}</span>
            <div className="message-bubble">{message.text}</div>
          </li>)}</ul>
          {children}
          <div ref={bottom} />
        </div>
      </div>
      {composer}
    </main>
  </div>
}
