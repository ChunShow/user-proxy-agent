import { useEffect, useRef, useState } from 'react'
import ChatView from './chat/ChatView'
import Composer from './chat/Composer'
import CallCard from './chat/CallCard'
import MessageList from './chat/MessageList'
import { createPreview, previewMessages, previewOptions } from './chat/preview'
import type { PreviewId } from './chat/preview'
import type { CallAction, ChatMessage } from './chat/types'
import useChat from './chat/useChat'
import useCalls from './chat/useCalls'
import PhoneCallCard, { ActivePhoneCall } from './chat/PhoneCallCard'
import type { CallConfirmation } from './chat/calls'

export default function App() {
  const chat = useChat()
  const phones = useCalls(chat.preview ? null : chat.selectedId, chat.ready)
  const { syncCallResults, selectedId, busy } = chat
  useEffect(() => {
    void syncCallResults(selectedId, phones.calls.flatMap(call => call.result_message_id ? [call.result_message_id] : []))
  }, [syncCallResults, selectedId, busy, phones.calls])
  const [exampleMessages, setExampleMessages] = useState<ChatMessage[]>([])
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const preview = chat.preview
  const draftKey = preview ? 'preview' : chat.selectedId ?? 'new'
  const draft = drafts[draftKey] ?? ''
  const setDraft = (text: string) => setDrafts(previous => ({ ...previous, [draftKey]: text }))
  const [example, setExample] = useState<PreviewId>('connected')
  const [call, setCall] = useState(() => createPreview('connected'))
  const [target, setTarget] = useState(false)
  const [replyTarget, setReplyTarget] = useState<{ callId: string; conversationId: string; question: CallConfirmation } | null>(null)
  const currentReply = !preview && replyTarget?.conversationId === chat.selectedId ? replyTarget : null
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
      if (currentReply) {
        void phones.answer(currentReply.callId, currentReply.question, text).then(ok => {
          if (ok) { setReplyTarget(previous => previous?.question.id === currentReply.question.id ? null : previous); setDraft('') }
        })
        return
      }
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
  return <ChatView messages={preview ? exampleMessages : chat.messages} onRetry={preview ? undefined : chat.retry} preview={preview}
    conversationId={chat.selectedId} conversations={chat.items} onNewConversation={() => { setTarget(false); setDrafts(previous => ({ ...previous, new: '' })); chat.newConversation() }} onSelectConversation={id => { setTarget(false); chat.open(id) }}
    listError={chat.listError} hasMoreConversations={Boolean(chat.listCursor)} onMoreConversations={chat.moreList} onReloadList={chat.refreshList}
    loading={!preview && chat.loading} loadError={!preview ? chat.loadError : ''} onReload={chat.refresh}
    hasMoreMessages={!preview && Boolean(chat.messageCursor)} onMoreMessages={chat.loadMore} pageError={chat.pageError}
    remoteBusy={!preview && chat.remoteBusy}
    afterMessage={preview ? undefined : id => phones.calls.filter(c => c.source_user_message_id === id).map(c => <li className="phone-message" key={c.id}><PhoneCallCard call={c} onStop={phones.stop} onRefresh={phones.refresh} pending={phones.pending.includes(c.id)} error={phones.actionErrors[c.id]}
      onAnswer={(id, q, text) => { void phones.answer(id, q, text) }} answerPending={phones.pending}
      onReply={(id, question) => { setReplyTarget({ callId: id, conversationId: c.conversation_id, question }); input.current?.focus() }} /></li>)}
    activeCall={phones.otherActive.map(c => <ActivePhoneCall key={c.id} call={c} onOpen={() => chat.open(c.conversation_id)} onStop={phones.stop} onRefresh={phones.refresh} pending={phones.pending.includes(c.id)} error={phones.actionErrors[c.id]} />)}
    callError={phones.error && <div className="conversation-notice"><p role="status">{phones.error}</p><button type="button" onClick={phones.reload}>통화 상태 다시 불러오기</button></div>}
    onNavigate={next => {
    if (next) chat.stop()
    chat.navigate(next); setTarget(false)
  }} composer={<Composer preview={preview} disabled={!preview && (!chat.ready || chat.loading || Boolean(chat.loadError) || (currentReply ? phones.pending.includes(currentReply.question.id) : chat.remoteBusy))} busy={!preview && !currentReply && chat.busy} onStop={chat.stop} value={draft} onChange={setDraft} inputRef={input}
    confirmationTarget={Boolean(currentReply)} targetLabel={currentReply?.question.question ?? (preview && target ? call.subject : undefined)} onClearTarget={() => { setTarget(false); setReplyTarget(null); input.current?.focus() }} onSubmit={submit} />}>
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
