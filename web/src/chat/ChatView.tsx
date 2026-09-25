import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import ConnectionStatus from '../components/ConnectionStatus'
import Icon from '../components/Icon'
import type { ChatMessage } from './types'
import MessageList from './MessageList'
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
  const scroller = useRef<HTMLDivElement>(null)
  const previous = useRef({ preview, count: messages.length })
  const [menuOpen, setMenuOpen] = useState(false)
  const [collapsed, setCollapsed] = useState(false)
  const isEmpty = messages.length === 0 && !preview
  useEffect(() => {
    if (previous.current.preview !== preview) scroller.current?.scrollTo(0, 0)
    else if (messages.length > previous.current.count) bottom.current?.scrollIntoView({ block: 'nearest' })
    previous.current = { preview, count: messages.length }
  }, [messages.length, preview])
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
      <div className="sidebar-heading"><span>대화</span>
        <button className="nav-collapse" aria-label={collapsed ? '탐색 영역 펼치기' : '탐색 영역 접기'} onClick={() => setCollapsed(!collapsed)}><Icon name="menu" /><span>{collapsed ? '펼치기' : '접기'}</span></button>
      </div>
      {navigation}
    </aside>
    <dialog ref={dialog} className="mobile-menu" aria-label="탐색 메뉴" onClose={() => { setMenuOpen(false); menuButton.current?.focus() }}>
      <div className="menu-heading"><strong>user proxy agent</strong><button aria-label="메뉴 닫기" onClick={() => dialog.current?.close()}><Icon name="close" /></button></div>
      {navigation}
    </dialog>
    <main className="chat-main">
      <header className="chat-header">
        <div className="assistant-heading">
          <button ref={menuButton} className="mobile-menu-button" aria-label="메뉴 열기" aria-expanded={menuOpen} onClick={() => { setMenuOpen(true); dialog.current?.showModal() }}><Icon name="menu" /></button>
          <h1>user proxy agent</h1>
        </div>
        <ConnectionStatus />
      </header>
      <div className={`chat-workspace ${isEmpty ? 'is-start' : ''}`}>
        <div className="conversation-scroll" ref={scroller}>
          <div className={`conversation ${isEmpty ? 'is-empty' : ''}`}>
            {isEmpty && <div className="empty-chat">
              <h2>어떤 일을 도와드릴까요?</h2>
              <p>확인하거나 부탁할 일을 편하게 적어 주세요.</p>
            </div>}
            {children}
            <MessageList messages={messages} />
            <div ref={bottom} />
          </div>
        </div>
        {composer}
      </div>
    </main>
  </div>
}
