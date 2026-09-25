import { useEffect, useRef, useState } from 'react'
import ChatView from './chat/ChatView'
import Composer from './chat/Composer'
import CallCard from './chat/CallCard'
import MessageList from './chat/MessageList'
import { createPreview, previewMessages, previewOptions } from './chat/preview'
import type { PreviewId } from './chat/preview'
import type { CallAction, ChatMessage } from './chat/types'
import useChat from './chat/useChat'

export default function App() {
  const chat = useChat()
  const [exampleMessages, setExampleMessages] = useState<ChatMessage[]>([])
  const [draft, setDraft] = useState('')
  const [preview, setPreview] = useState(new URLSearchParams(location.search).get('preview') === 'call')
  const [example, setExample] = useState<PreviewId>('connected')
  const [call, setCall] = useState(() => createPreview('connected'))
  const [target, setTarget] = useState(false)
  const input = useRef<HTMLTextAreaElement>(null)
  const callSurface = useRef<HTMLDivElement>(null)
  const previousExample = useRef(example)
  useEffect(() => {
    if (previousExample.current !== example) callSurface.current?.scrollIntoView({ block: 'start' })
    previousExample.current = example
  }, [example])

  function changeExample(id: PreviewId) {
    setExample(id); setCall(createPreview(id)); setTarget(false); setExampleMessages([])
  }
  function handleCall(action: CallAction) {
    if (action === 'instruct' && call.callStatus === 'connected') {
      setTarget(true); input.current?.focus(); return
    }
    if (action === 'end' && (call.callStatus === 'connected' || call.callStatus === 'dialing')) {
      setCall({ ...call, callStatus: 'ending', listening: false, summary: '종료를 확인하고 있습니다. 아직 통화는 끝나지 않았습니다.' })
      setTarget(false)
    } else if (call.callStatus === 'connected' && (action === 'listen' || action === 'stop_listening')) {
      setCall({ ...call, listening: action === 'listen' })
    }
  }
  function submit(text: string) {
    if (!preview) {
      if (chat.send(text)) setDraft('')
      return
    }
    const message: ChatMessage = {
      id: crypto.randomUUID(), role: 'user',
      text: target && preview && call.callStatus === 'connected' ? `지시 예시 · ${call.subject}\n${text}` : text,
    }
    setExampleMessages(previous => [...previous, message])
    setDraft(''); setTarget(false)
  }
  return <ChatView messages={preview ? exampleMessages : chat.messages} onRetry={preview ? undefined : chat.retry} preview={preview} onNavigate={next => {
    if (next) chat.stop()
    setPreview(next); setTarget(false)
    history.replaceState(null, '', next ? '/?preview=call' : '/')
  }} composer={<Composer preview={preview} busy={!preview && chat.busy} onStop={chat.stop} value={draft} onChange={setDraft} inputRef={input}
    targetLabel={target ? call.subject : undefined} onClearTarget={() => { setTarget(false); input.current?.focus() }} onSubmit={submit} />}>
    {preview && <>
      <div className="preview-tools">
        <p className="preview-banner">예시 데이터 · 실제 전화가 연결되지 않습니다</p>
        <label className="sr-only" htmlFor="preview-state">통화 예시 상태</label>
        <select id="preview-state" value={example} onChange={event => changeExample(event.target.value as PreviewId)}>
          {previewOptions.map(option => <option key={option.id} value={option.id}>{option.label}</option>)}
        </select>
        {call.callStatus === 'ending' && <button type="button" onClick={() => setCall({
          ...call, callStatus: 'ended', taskStatus: call.taskStatus === 'working' ? 'incomplete' : call.taskStatus,
          summary: '통화가 종료되었습니다. 종료 전에 확인하지 못한 항목은 그대로 남아 있습니다.',
        })}>종료 확인 (예시)</button>}
      </div>
      <MessageList messages={previewMessages} label="예시 대화" />
      <div ref={callSurface}><CallCard key={example} call={call} onAction={handleCall} /></div>
    </>}
  </ChatView>
}
