import { useCallback, useEffect, useRef, useState } from 'react'
import { ChatError, streamChat } from './stream'
import type { ChatRequest } from './stream'
import type { ChatMessage } from './types'
import { mergeSavedMessages, latestChatMessage } from './resultMessages'
import { createConversation, getConversation, listConversations, prepareSession } from './conversations'
import type { Conversation } from './conversations'

type View = { id: string | null; preview: boolean }
type Active = { controller: AbortController; responseId: string; userId: string; request: ChatRequest; accepted: boolean }
const selectionKey = 'proxy.selectedConversation'
function savedSelection() { try { return localStorage.getItem(selectionKey) } catch { return null } }
function readView(initial = false): View {
  const query = new URLSearchParams(location.search)
  return { id: query.get('conversation') || (initial ? savedSelection() : null), preview: query.get('preview') === 'call' }
}
function remember(id: string | null) { try { if (id) localStorage.setItem(selectionKey, id); else localStorage.removeItem(selectionKey) } catch { /* Storage may be unavailable. */ } }
function urlFor(view: View) { return view.preview ? '/?preview=call' : view.id ? `/?conversation=${encodeURIComponent(view.id)}` : '/' }
const failure = (error: unknown) => error instanceof ChatError ? error : new ChatError('load_failed')

export default function useChat() {
  const [view, setView] = useState<View>(() => readView(true))
  const viewRef = useRef(view)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const latest = useRef<ChatMessage[]>([])
  const [items, setItems] = useState<Conversation[]>([])
  const [listCursor, setListCursor] = useState<string | null>(null)
  const [messageCursor, setMessageCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [ready, setReady] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [listError, setListError] = useState('')
  const [pageError, setPageError] = useState('')
  const [busy, setBusy] = useState(false)
  const active = useRef<Active | null>(null)
  const pendingRetry = useRef<Active | null>(null)
  const generation = useRef(0)
  const listGeneration = useRef(0)
  const alive = useRef(true)
  const session = useRef<Promise<void> | null>(null)
  const paging = useRef(false)
  const resultSync = useRef<symbol | null>(null)
  const seenResults = useRef(new Map<string, Set<string>>())
  const replace = useCallback((next: ChatMessage[]) => { latest.current = next; setMessages(next) }, [])
  const patch = useCallback((id: string, update: Partial<ChatMessage>) => {
    replace(latest.current.map(m => m.id === id ? { ...m, ...update } : m))
  }, [replace])
  const refreshList = useCallback(async (cursor?: string) => {
    const version = ++listGeneration.current
    try {
      const data = await listConversations(cursor)
      if (!alive.current || listGeneration.current !== version) return
      setItems(old => cursor ? [...old, ...data.items.filter(m => !old.some(o => o.id === m.id))] : data.items)
      setListCursor(data.next_cursor); setListError('')
    } catch (error) { if (alive.current && listGeneration.current === version) setListError(failure(error).message) }
  }, [])
  const load = useCallback(async (id: string | null) => {
    const version = ++generation.current
    pendingRetry.current = null
    setLoadError(''); setPageError(''); setMessageCursor(null); replace([]); setLoading(Boolean(id))
    if (!id) return
    try {
      const data = await getConversation(id)
      if (!alive.current || generation.current !== version) return
      replace(data.messages); setMessageCursor(data.next_cursor)
    } catch (error) { if (alive.current && generation.current === version) setLoadError(failure(error).message) }
    finally { if (alive.current && generation.current === version) setLoading(false) }
  }, [replace])
  const stop = useCallback(() => {
    const current = active.current
    if (!current) return
    active.current = null
    current.controller.abort()
    pendingRetry.current = current
    patch(current.responseId, { status: 'stopped', error: undefined, retryable: true })
    setBusy(false)
  }, [patch])
  const select = useCallback((next: View, push = true) => {
    stop()
    viewRef.current = next; setView(next)
    if (!next.preview) remember(next.id)
    if (push) history.pushState(null, '', urlFor(next))
    void load(next.preview ? null : next.id)
    void refreshList()
  }, [load, refreshList, stop])
  useEffect(() => {
    alive.current = true
    let cancelled = false
    session.current ??= prepareSession()
    void session.current.then(() => {
      if (cancelled) return
      setReady(true)
      const current = viewRef.current
      history.replaceState(null, '', urlFor(current))
      void refreshList()
      void load(current.preview ? null : current.id)
    }).catch(error => { if (!cancelled) { setLoadError(failure(error).message); setLoading(false) } })
    const pop = () => select(readView(), false)
    window.addEventListener('popstate', pop)
    return () => {
      cancelled = true; alive.current = false
      active.current?.controller.abort(); active.current = null
      window.removeEventListener('popstate', pop)
    }
  }, [load, refreshList, select])

  async function run(request: ChatRequest, userId: string, responseId: string) {
    const current: Active = { controller: new AbortController(), responseId, userId, request, accepted: false }
    active.current = current; pendingRetry.current = null; setBusy(true)
    try {
      await createConversation(request.conversation_id, current.controller.signal)
      if (active.current !== current) return
      void refreshList()
      await streamChat(request, current.controller.signal, event => {
        if (active.current !== current) return
        if (event.type === 'start') {
          current.accepted = true
          patch(current.userId, { id: event.user_message_id! })
          patch(current.responseId, { id: event.message_id })
          current.userId = event.user_message_id!; current.responseId = event.message_id
        } else if (event.type === 'delta') {
          const previous = latest.current.find(m => m.id === current.responseId)?.text ?? ''
          patch(current.responseId, { text: previous + (event.text ?? ''), status: 'streaming' })
        } else patch(current.responseId, { status: 'completed' })
      })
    } catch (error) {
      if (active.current !== current) return
      const problem = failure(error)
      if (['request_exists', 'conversation_busy'].includes(problem.code)) {
        await load(request.conversation_id)
      } else {
        pendingRetry.current = current
        patch(current.responseId, { status: 'failed', error: problem.message, retryable: problem.retryable })
        if (problem.code === 'session_expired') setReady(false)
      }
    } finally {
      if (active.current === current) { active.current = null; setBusy(false); void refreshList() }
    }
  }
  function send(text: string) {
    if (active.current || loading || loadError || !ready || viewRef.current.preview || !text.trim() || latest.current.some(m => m.status === 'streaming')) return false
    let id = viewRef.current.id
    if (!id) {
      id = crypto.randomUUID()
      const next = { id, preview: false }
      viewRef.current = next; setView(next); remember(id); history.replaceState(null, '', urlFor(next))
    }
    const userId = crypto.randomUUID(), responseId = crypto.randomUUID()
    replace([...latest.current, { id: userId, role: 'user', text }, { id: responseId, role: 'assistant', text: '', status: 'submitting' }])
    void run({ request_id: crypto.randomUUID(), conversation_id: id, content: text }, userId, responseId)
    return true
  }
  function retry() {
    const last = latestChatMessage(latest.current), id = viewRef.current.id
    if (!id || active.current || loading || !ready || !last?.retryable) return
    const pending = pendingRetry.current
    const userId = latest.current.filter(m => m.role === 'user').at(-1)?.id ?? ''
    const request = pending && !pending.accepted ? pending.request : { request_id: crypto.randomUUID(), conversation_id: id, retry_message_id: last.id }
    patch(last.id, { text: '', status: 'submitting', error: undefined, retryable: false })
    void run(request, userId, last.id)
  }
  async function loadMore() {
    const id = viewRef.current.id, version = generation.current
    if (!id || !messageCursor || paging.current) return
    paging.current = true; setPageError('')
    try {
      const data = await getConversation(id, messageCursor)
      if (generation.current !== version || !alive.current) return
      replace([...data.messages.filter(m => !latest.current.some(o => o.id === m.id)), ...latest.current])
      setMessageCursor(data.next_cursor)
    } catch (error) { if (generation.current === version) setPageError(failure(error).message) }
    finally { paging.current = false }
  }
  const syncCallResults = useCallback(async (cid: string | null, ids: string[]) => {
    if (!cid || cid !== viewRef.current.id || viewRef.current.preview || !ready || loading || loadError
      || active.current || resultSync.current || latest.current.some(m => ['streaming', 'submitting'].includes(m.status ?? ''))) return
    const seen = seenResults.current.get(cid) ?? new Set<string>()
    if (!ids.some(id => !seen.has(id) && !latest.current.some(m => m.id === id))) return
    const version = generation.current, ticket = Symbol('call-result')
    resultSync.current = ticket
    try {
      const data = await getConversation(cid)
      if (!alive.current || generation.current !== version || viewRef.current.id !== cid || active.current) return
      replace(mergeSavedMessages(latest.current, data.messages))
      // Older result messages remain accessible through the existing history pagination.
      setMessageCursor(previous => previous ?? data.next_cursor)
      ids.forEach(id => seen.add(id)); seenResults.current.set(cid, seen)
      void refreshList()
    } catch { /* Existing call polling retries; keep loaded messages and draft intact. */ }
    finally { if (resultSync.current === ticket) resultSync.current = null }
  }, [ready, loading, loadError, replace, refreshList])
  const remoteBusy = !busy && messages.some(m => m.status === 'streaming')
  return { messages, busy, send, stop, retry, items, loading, loadError, listError, pageError, ready, syncCallResults,
    selectedId: view.id, preview: view.preview, remoteBusy, messageCursor, listCursor, loadMore,
    refresh: async () => {
      if (!ready) {
        setLoading(true); setLoadError('')
        try { await prepareSession(); setReady(true); void refreshList() }
        catch (error) { setLoadError(failure(error).message); setLoading(false); return }
      }
      await load(viewRef.current.id)
    }, refreshList: () => refreshList(),
    moreList: () => refreshList(listCursor ?? undefined),
    open: (id: string) => select({ id, preview: false }),
    newConversation: () => select({ id: null, preview: false }),
    navigate: (preview: boolean) => select({ id: viewRef.current.id, preview }),
  }
}
