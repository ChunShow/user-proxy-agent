import { useEffect, useRef, useState } from 'react'
import type { CSSProperties, ReactNode } from 'react'
import ConnectionStatus from '../components/ConnectionStatus'
import ActionCards from '../actions/ActionCards'
import ConnectedApps from '../integrations/ConnectedApps'
import Icon from '../components/Icon'
import Brand from '../components/Brand'
import useSidebarWidth from './useSidebarWidth'
import ConversationList from './ConversationList'
import type { Conversation } from './conversations'
import type { ChatMessage } from './types'
import MessageList from './MessageList'
import './chat.css'

interface Props {
  messages: ChatMessage[]
  composer: ReactNode
  onRetry?: () => void
  conversationId: string | null
  conversations: Conversation[]
  onConversationsChanged: (deletedId?: string) => void
  onNewConversation: () => void
  onSelectConversation: (id: string) => void
  listError: string
  hasMoreConversations: boolean
  onMoreConversations: () => void
  onReloadList: () => void
  loading: boolean
  loadError: string
  onReload: () => void
  hasMoreMessages: boolean
  onMoreMessages: () => Promise<void>
  pageError: string
  remoteBusy: boolean
  afterMessage?: (id: string) => ReactNode
  activeCall?: ReactNode
  callError?: ReactNode
}

export default function ChatView({ messages, composer, onRetry, onConversationsChanged,
  conversationId, conversations, onNewConversation, onSelectConversation, listError, hasMoreConversations,
  onMoreConversations, onReloadList, loading, loadError, onReload, hasMoreMessages, onMoreMessages, pageError, remoteBusy, afterMessage, activeCall, callError }: Props) {
  const simulation = import.meta.env.VITE_SIMULATION === '1'
  const sidebar = useSidebarWidth()
  const dialog = useRef<HTMLDialogElement>(null)
  const menuButton = useRef<HTMLButtonElement>(null)
  const bottom = useRef<HTMLDivElement>(null)
  const scroller = useRef<HTMLDivElement>(null)
  const previous = useRef({ conversationId, userId: messages.filter(m => m.role === 'user').at(-1)?.id })
  const following = useRef(true)
  const [showLatest, setShowLatest] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const [collapsed, setCollapsed] = useState(false)
  const isEmpty = messages.length === 0 && !loading && !loadError
  useEffect(() => {
    const userId = messages.filter(m => m.role === 'user').at(-1)?.id
    const scroll = scroller.current
    if (!scroll) return
    if (previous.current.conversationId !== conversationId) following.current = true
    if (userId !== previous.current.userId) following.current = true
    if (following.current) scroll.scrollTop = scroll.scrollHeight
    previous.current = { conversationId, userId }
  }, [messages, conversationId])
  useEffect(() => {
    const scroll = scroller.current, content = scroll?.firstElementChild
    if (!scroll || !content) return
    const observer = new ResizeObserver(() => {
      if (following.current) scroll.scrollTop = scroll.scrollHeight
    })
    observer.observe(content)
    return () => observer.disconnect()
  }, [])
  function closeMenu() { setShowLatest(false); dialog.current?.close() }
  const navigation = <nav aria-label="대화 탐색">
    <button aria-label="새 대화" className="nav-item new-conversation" onClick={() => { onNewConversation(); closeMenu() }}><Icon name="newChat" /><span>새 대화</span></button>
    <ConversationList items={conversations} selectedId={conversationId} onChanged={onConversationsChanged} error={listError}
      hasMore={hasMoreConversations} onSelect={id => { onSelectConversation(id); closeMenu() }} onMore={onMoreConversations} onReload={onReloadList} />
  </nav>
  return <div className={`app-shell ${collapsed ? 'nav-collapsed' : ''}`} style={{ '--sidebar-width': `${sidebar.width}px` } as CSSProperties}>
    <aside id="desktop-sidebar" className="sidebar">
      <div className="sidebar-heading"><Brand />
        <button className="nav-collapse" aria-label={collapsed ? '탐색 영역 펼치기' : '탐색 영역 접기'} onClick={() => setCollapsed(!collapsed)}><Icon name="panel" /><span>{collapsed ? '펼치기' : '접기'}</span></button>
      </div>
      {navigation}
      {!collapsed && <div className={`sidebar-resizer ${sidebar.dragging ? 'is-dragging' : ''}`} {...sidebar.separator} />}
    </aside>
    <dialog ref={dialog} className="mobile-menu" aria-label="탐색 메뉴" onClose={() => { setMenuOpen(false); menuButton.current?.focus() }}>
      <div className="menu-heading"><Brand /><button aria-label="메뉴 닫기" onClick={() => dialog.current?.close()}><Icon name="close" /></button></div>
      {navigation}
    </dialog>
    <main className="chat-main">
      <header className="chat-header">
        <div className="assistant-heading">
          <button ref={menuButton} className="mobile-menu-button" aria-label="메뉴 열기" aria-expanded={menuOpen} onClick={() => { setMenuOpen(true); dialog.current?.showModal() }}><Icon name="menu" /></button>
          <h1>User Proxy Agent</h1>
        </div>
        <div className="header-tools">{!simulation && <ConnectedApps />}<ConnectionStatus /></div>
      </header>
      {simulation && <p className="simulation-notice"><strong>가상 통화</strong> · 실제 전화 발신 없음 <span>시험 번호 01000000001</span></p>}
      {activeCall}
      <div className={`chat-workspace ${isEmpty ? 'is-start' : ''}`}>
        <div className="conversation-scroll" ref={scroller} onScroll={() => {
          const scroll = scroller.current
          if (!scroll) return
          following.current = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 64
          setShowLatest(!following.current)
        }}>
          <div className={`conversation ${isEmpty ? 'is-empty' : ''}`}>
            {isEmpty && <div className="empty-chat">
              <h2>어떤 일을 도와드릴까요?</h2>
              <p>확인하거나 부탁할 일을 편하게 적어 주세요.</p>
            </div>}
            {loading && <p className="conversation-notice" role="status">대화를 불러오고 있습니다.</p>}
            {loadError && <div className="conversation-notice"><p role="status">{loadError}</p><button type="button" onClick={onReload}>다시 불러오기</button></div>}
            {remoteBusy && <div className="conversation-notice"><p role="status">다른 창에서 답변을 작성하고 있습니다.</p><button type="button" onClick={onReload}>다시 불러오기</button></div>}
            {hasMoreMessages && <button className="older-messages" type="button" onClick={async () => {
              const scroll = scroller.current
              if (!scroll) return
              const height = scroll.scrollHeight, top = scroll.scrollTop
              following.current = false
              await onMoreMessages()
              requestAnimationFrame(() => { scroll.scrollTop = top + scroll.scrollHeight - height })
            }}>이전 메시지 더 보기</button>}
            {pageError && <p className="conversation-notice" role="status">{pageError}</p>}
            {callError}
            <MessageList messages={messages} onRetry={onRetry} afterMessage={afterMessage} />
            {conversationId && !loading && !loadError && <ActionCards key={conversationId} conversationId={conversationId} />}
            <div ref={bottom} />
          </div>
        </div>
        {showLatest && <button className="latest-message" type="button" onClick={() => {
          following.current = true
          bottom.current?.scrollIntoView({ block: 'end' })
          setShowLatest(false)
        }}>최신 메시지로 <Icon name="arrow" /></button>}
        {composer}
      </div>
    </main>
  </div>
}
