import { useRef, useState } from 'react'
import ChatView from './chat/ChatView'
import Composer from './chat/Composer'
import type { ChatMessage } from './chat/types'

export default function App() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [draft, setDraft] = useState('')
  const [preview, setPreview] = useState(new URLSearchParams(location.search).get('preview') === 'call')
  const input = useRef<HTMLTextAreaElement>(null)
  return <ChatView messages={messages} preview={preview} onNavigate={next => {
    setPreview(next)
    history.replaceState(null, '', next ? '/?preview=call' : '/')
  }} composer={<Composer value={draft} onChange={setDraft} inputRef={input} onClearTarget={() => {}}
    onSubmit={text => { setMessages(previous => [...previous, { id: crypto.randomUUID(), role: 'user', text }]); setDraft('') }} />} />
}
