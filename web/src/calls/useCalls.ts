import { ApiError } from '../api/errors'
import { useEffect, useRef, useState } from 'react'
import { activeCalls, answerCall, approveCall, isActiveCall, listCalls, mergeCalls, refreshCall, stopCall } from './calls'
import type { CallConfirmation, PhoneCall } from './calls'

export default function useCalls(conversationId: string | null, enabled: boolean) {
  const [records, setRecords] = useState<Record<string, PhoneCall>>({})
  const [activeIds, setActiveIds] = useState<string[]>([])
  const [error, setError] = useState('')
  const [actionErrors, setActionErrors] = useState<Record<string, string>>({})
  const [pending, setPending] = useState<string[]>([])
  const pendingIds = useRef(new Set<string>())
  const revision = useRef(0)
  const mounted = useRef(false)
  const kick = useRef<() => void>(() => {})
  const answerIds = useRef(new Map<string, { answer: string; id: string }>())
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  useEffect(() => {
    if (!enabled) return
    let alive = true, running = false, initial = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const controller = new AbortController()
    async function poll() {
      if (!alive || running || document.hidden) return
      running = true
      clearTimeout(timer)
      const version = revision.current
      try {
        const [active, first] = await Promise.all([
          activeCalls(controller.signal),
          conversationId ? listCalls(conversationId, undefined, controller.signal) : Promise.resolve({ items: [], next_cursor: null }),
        ])
        const items = [...first.items]
        let cursor = initial ? first.next_cursor : null
        while (cursor && alive) {
          const page = await listCalls(conversationId!, cursor, controller.signal)
          items.push(...page.items); cursor = page.next_cursor
        }
        if (!alive || version !== revision.current) return
        initial = false
        setRecords(previous => mergeCalls(previous, [...items, ...active.items]))
        setActiveIds(active.items.map(c => c.id)); setError('')
      } catch (cause) {
        if (alive && version === revision.current && (!(cause instanceof ApiError) || cause.code !== 'not_found')) {
          setError(cause instanceof ApiError && cause.code === 'session_expired'
            ? '세션이 만료되었습니다. 화면을 새로 불러와 주세요.'
            : '통화 상태를 불러오지 못했습니다. 연결을 확인하고 다시 불러와 주세요.')
        }
      } finally {
        running = false
        if (alive && !document.hidden) timer = setTimeout(() => { void poll() }, 2000)
      }
    }
    function resume() { clearTimeout(timer); if (!document.hidden) void poll() }
    kick.current = resume
    document.addEventListener('visibilitychange', resume)
    void poll()
    return () => { alive = false; controller.abort(); clearTimeout(timer); document.removeEventListener('visibilitychange', resume) }
  }, [conversationId, enabled])

  async function act(id: string, action: 'stop' | 'refresh' | 'approve', expectedVersion?: number) {
    if (pendingIds.current.has(id)) return
    pendingIds.current.add(id); setPending([...pendingIds.current]); revision.current++
    setActionErrors(previous => ({ ...previous, [id]: '' }))
    try {
      const call = await (action === 'approve' ? approveCall(id, expectedVersion!) : action === 'stop' ? stopCall(id) : refreshCall(id))
      if (mounted.current) setRecords(previous => mergeCalls(previous, [call]))
    } catch (cause) {
      if (mounted.current) setActionErrors(previous => ({ ...previous, [id]: action === 'approve'
        ? cause instanceof ApiError && ['call_approval_inactive', 'call_approval_changed'].includes(cause.code)
          ? '승인할 수 없는 요청입니다. 현재 카드를 확인하고 필요하면 채팅에서 다시 요청해 주세요.'
          : '승인 결과를 확인하지 못했습니다. 다시 확인을 눌러 현재 상태를 확인해 주세요.'
        : action === 'stop'
        ? '종료 요청 결과를 확인하지 못했습니다. 다시 확인해 주세요.' : '상태를 확인하지 못했습니다. 다시 확인해 주세요.' }))
    } finally {
      revision.current++
      pendingIds.current.delete(id)
      if (mounted.current) { setPending([...pendingIds.current]); kick.current() }
    }
  }
  async function answer(id: string, question: CallConfirmation, text: string) {
    if (pendingIds.current.has(question.id)) return false
    pendingIds.current.add(question.id); setPending([...pendingIds.current]); revision.current++
    setActionErrors(previous => ({ ...previous, [id]: '' }))
    let request = answerIds.current.get(question.id)
    if (!request || request.answer !== text) {
      request = { answer: text, id: crypto.randomUUID() }; answerIds.current.set(question.id, request)
    }
    try {
      const updated = await answerCall(id, question, text, request.id)
      if (mounted.current) setRecords(previous => {
        const call = previous[id]
        if (!call) return previous
        return { ...previous, [id]: { ...call, confirmations: call.confirmations?.map(q => q.id === updated.id ? updated : q) } }
      })
      return true
    } catch (cause) {
      if (mounted.current) setActionErrors(previous => ({ ...previous, [id]: cause instanceof ApiError && cause.code === 'call_question_inactive'
        ? '질문이 마감되어 답변을 전달하지 못했습니다.' : '답변 접수를 확인하지 못했습니다. 같은 답변으로 다시 시도해 주세요.' }))
      return false
    } finally {
      revision.current++; pendingIds.current.delete(question.id)
      if (mounted.current) { setPending([...pendingIds.current]); kick.current() }
    }
  }
  return {
    calls: Object.values(records).filter(c => c.conversation_id === conversationId),
    otherActive: activeIds.map(id => records[id]).filter(c => c && isActiveCall(c) && c.conversation_id !== conversationId),
    error, actionErrors, pending, answer,
    approve: (id: string, version: number) => { void act(id, 'approve', version) },
    stop: (id: string) => { void act(id, 'stop') },
    refresh: (id: string) => { void act(id, 'refresh') },
    reload: () => kick.current(),
  }
}
