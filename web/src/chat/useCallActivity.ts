import { useEffect, useRef, useState } from 'react'
import { getCallActivity } from './calls'
import type { CallActivityEvent } from './calls'
import { mergeActivity, validateActivityPage } from './transcriptData'

interface ActivityState {
  id: string; events: CallActivityEvent[]; cursor: number; more: boolean
  terminal: boolean; loaded: boolean; error: boolean
}
const empty = (id: string): ActivityState => ({ id, events: [], cursor: 0, more: false, terminal: false, loaded: false, error: false })

export default function useCallActivity(callId: string, enabled: boolean) {
  const [state, setState] = useState(() => empty(callId))
  const cache = useRef(new Map<string, ActivityState>())
  const retry = useRef<() => void>(() => {})
  useEffect(() => {
    if (!enabled) return
    let alive = true, generation = 0
    let timer: ReturnType<typeof setTimeout> | undefined
    let controller: AbortController | null = null
    let current = cache.current.get(callId) ?? empty(callId)
    function publish(next: ActivityState) {
      current = next; cache.current.set(callId, next); setState(next)
    }
    function cancel() {
      generation++; clearTimeout(timer); controller?.abort(); controller = null
    }
    async function poll(force = false) {
      if (!alive || document.hidden || controller) return
      if (!force && current.loaded && current.terminal && !current.more) return
      const token = ++generation
      const request = new AbortController()
      controller = request
      try {
        for (let batch = 0; batch < 5; batch++) {
          const page = validateActivityPage(await getCallActivity(callId, current.cursor, request.signal), callId, current.cursor)
          if (!alive || token !== generation) return
          publish({ id: callId, events: mergeActivity(current.events, page.events), cursor: page.next_after,
            more: page.has_more, terminal: page.terminal, loaded: true, error: false })
          if (!page.has_more) break
        }
      } catch {
        if (alive && token === generation) publish({ ...current, error: true })
      } finally {
        if (alive && token === generation) {
          controller = null
          if (!document.hidden && (current.error || !current.terminal || current.more)) {
            timer = setTimeout(() => { void poll(current.error) }, current.more && !current.error ? 0 : 1000)
          }
        }
      }
    }
    function resume() { cancel(); if (!document.hidden) void poll() }
    retry.current = () => { cancel(); void poll(true) }
    document.addEventListener('visibilitychange', resume)
    void poll()
    return () => { alive = false; cancel(); document.removeEventListener('visibilitychange', resume) }
  }, [callId, enabled])
  return { ...(state.id === callId ? state : empty(callId)), reload: () => retry.current() }
}
