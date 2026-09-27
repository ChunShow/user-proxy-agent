import { useEffect, useRef, useState } from 'react'
import ChatView from './chat/ChatView'
import Composer from './chat/Composer'
import useChat from './chat/useChat'
import useCalls from './calls/useCalls'
import PhoneCallCard, { ActivePhoneCall } from './calls/PhoneCallCard'
import type { CallConfirmation } from './calls/calls'

export default function App() {
  const chat = useChat()
  const phones = useCalls(chat.selectedId, chat.ready)
  const { syncCallResults, selectedId, busy } = chat
  useEffect(() => {
    void syncCallResults(selectedId, phones.calls.flatMap(call => call.result_message_id ? [call.result_message_id] : []))
  }, [syncCallResults, selectedId, busy, phones.calls])
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const draftKey = chat.selectedId ?? 'new'
  const draft = drafts[draftKey] ?? ''
  const setDraft = (text: string) => setDrafts(previous => ({ ...previous, [draftKey]: text }))
  const [replyTarget, setReplyTarget] = useState<{ callId: string; conversationId: string; question: CallConfirmation } | null>(null)
  const currentReply = replyTarget?.conversationId === chat.selectedId ? replyTarget : null
  const input = useRef<HTMLTextAreaElement>(null)
  function submit(text: string) {
    if (currentReply) {
      void phones.answer(currentReply.callId, currentReply.question, text).then(ok => {
        if (ok) { setReplyTarget(previous => previous?.question.id === currentReply.question.id ? null : previous); setDraft('') }
      })
      return
    }
    if (chat.send(text)) setDraft('')
  }
  return <ChatView messages={chat.messages} onRetry={chat.retry}
    conversationId={chat.selectedId} conversations={chat.items} onNewConversation={() => { setDrafts(previous => ({ ...previous, new: '' })); chat.newConversation() }} onSelectConversation={chat.open}
    onConversationsChanged={id => { if (id === chat.selectedId) chat.newConversation(); else void chat.refreshList() }}
    listError={chat.listError} hasMoreConversations={Boolean(chat.listCursor)} onMoreConversations={chat.moreList} onReloadList={chat.refreshList}
    loading={chat.loading} loadError={chat.loadError} onReload={chat.refresh}
    hasMoreMessages={Boolean(chat.messageCursor)} onMoreMessages={chat.loadMore} pageError={chat.pageError}
    remoteBusy={chat.remoteBusy}
    afterMessage={id => phones.calls.filter(c => c.source_user_message_id === id).map(c => <li className="phone-message" key={c.id}><PhoneCallCard call={c} onStop={phones.stop} onRefresh={phones.refresh} pending={phones.pending.includes(c.id)} error={phones.actionErrors[c.id]}
      onAnswer={(id, q, text) => { void phones.answer(id, q, text) }} answerPending={phones.pending}
      onReply={(id, question) => { setReplyTarget({ callId: id, conversationId: c.conversation_id, question }); input.current?.focus() }} /></li>)}
    activeCall={phones.otherActive.map(c => <ActivePhoneCall key={c.id} call={c} onOpen={() => chat.open(c.conversation_id)} onStop={phones.stop} onRefresh={phones.refresh} pending={phones.pending.includes(c.id)} error={phones.actionErrors[c.id]} />)}
    callError={phones.error && <div className="conversation-notice"><p role="status">{phones.error}</p><button type="button" onClick={phones.reload}>통화 상태 다시 불러오기</button></div>}
    composer={<Composer disabled={!chat.ready || chat.loading || Boolean(chat.loadError) || (currentReply ? phones.pending.includes(currentReply.question.id) : chat.remoteBusy)} busy={!currentReply && chat.busy} onStop={chat.stop} value={draft} onChange={setDraft} inputRef={input}
    confirmationTarget={Boolean(currentReply)} targetLabel={currentReply?.question.question} onClearTarget={() => { setReplyTarget(null); input.current?.focus() }} onSubmit={submit} />} />
}
