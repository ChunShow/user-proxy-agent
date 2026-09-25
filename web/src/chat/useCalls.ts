import { useEffect, useRef, useState } from 'react'
import { activeCalls, isActiveCall, listCalls, mergeCalls, refreshCall, stopCall } from './calls'
import type { PhoneCall } from './calls'
import { ChatError } from './stream'

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
        if (alive && version === revision.current && (!(cause instanceof ChatError) || cause.code !== 'not_found')) {
          setError(cause instanceof ChatError && cause.code === 'session_expired'
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

  async function act(id: string, action: 'stop' | 'refresh') {
    if (pendingIds.current.has(id)) return
    pendingIds.current.add(id); setPending([...pendingIds.current]); revision.current++
    setActionErrors(previous => ({ ...previous, [id]: '' }))
    try {
      const call = await (action === 'stop' ? stopCall(id) : refreshCall(id))
      if (mounted.current) setRecords(previous => mergeCalls(previous, [call]))
    } catch {
      if (mounted.current) setActionErrors(previous => ({ ...previous, [id]: action === 'stop'
        ? '종료 요청 결과를 확인하지 못했습니다. 다시 확인해 주세요.' : '상태를 확인하지 못했습니다. 다시 확인해 주세요.' }))
    } finally {
      revision.current++
      pendingIds.current.delete(id)
      if (mounted.current) { setPending([...pendingIds.current]); kick.current() }
    }
  }
  return {
    calls: Object.values(records).filter(c => c.conversation_id === conversationId),
    otherActive: activeIds.map(id => records[id]).filter(c => c && isActiveCall(c) && c.conversation_id !== conversationId),
    error, actionErrors, pending,
    stop: (id: string) => { void act(id, 'stop') },
    refresh: (id: string) => { void act(id, 'refresh') },
    reload: () => kick.current(),
  }
}
