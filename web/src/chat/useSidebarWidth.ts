import { useEffect, useRef, useState } from 'react'
import type { KeyboardEvent, PointerEvent } from 'react'
import { DEFAULT_SIDEBAR_WIDTH, MIN_SIDEBAR_WIDTH, SIDEBAR_WIDTH_KEY, keyboardSidebarWidth, sidebarWidth, storedSidebarWidth } from './sidebarWidth'

export default function useSidebarWidth() {
  const [preferred, setPreferred] = useState(() => {
    try { return storedSidebarWidth(localStorage.getItem(SIDEBAR_WIDTH_KEY)) }
    catch { return DEFAULT_SIDEBAR_WIDTH }
  })
  const [viewport, setViewport] = useState(() => window.innerWidth)
  const [dragging, setDragging] = useState(false)
  const drag = useRef<{ id: number; x: number; width: number; latest: number } | null>(null)
  const width = sidebarWidth(preferred, viewport)
  useEffect(() => {
    const resize = () => setViewport(window.innerWidth)
    window.addEventListener('resize', resize)
    return () => window.removeEventListener('resize', resize)
  }, [])
  useEffect(() => {
    if (!dragging) return
    document.body.classList.add('resizing-sidebar')
    return () => document.body.classList.remove('resizing-sidebar')
  }, [dragging])
  function persist(value: number) {
    try { localStorage.setItem(SIDEBAR_WIDTH_KEY, String(value)) } catch { /* Optional preference storage. */ }
  }
  function finish(event: PointerEvent<HTMLDivElement>) {
    if (drag.current?.id !== event.pointerId) return
    persist(drag.current.latest)
    drag.current = null
    setDragging(false)
  }
  return {
    width,
    dragging,
    separator: {
      role: 'separator', tabIndex: 0,
      'aria-label': '사이드바 너비 조절', 'aria-orientation': 'vertical' as const,
      'aria-valuemin': MIN_SIDEBAR_WIDTH, 'aria-valuemax': sidebarWidth(440, viewport),
      'aria-valuenow': width, 'aria-valuetext': `${width}픽셀`,
      'aria-controls': 'desktop-sidebar',
      title: '드래그 또는 방향키로 너비 조절 · 두 번 클릭하면 기본 너비',
      onPointerDown(event: PointerEvent<HTMLDivElement>) {
        if (event.button !== 0 || !event.isPrimary) return
        event.preventDefault()
        event.currentTarget.focus()
        event.currentTarget.setPointerCapture(event.pointerId)
        drag.current = { id: event.pointerId, x: event.clientX, width, latest: width }
        setDragging(true)
      },
      onPointerMove(event: PointerEvent<HTMLDivElement>) {
        if (!drag.current || drag.current.id !== event.pointerId) return
        const next = sidebarWidth(drag.current.width + event.clientX - drag.current.x, window.innerWidth)
        drag.current.latest = next
        setPreferred(next)
      },
      onPointerUp: finish, onPointerCancel: finish, onLostPointerCapture: finish,
      onDoubleClick() { setPreferred(DEFAULT_SIDEBAR_WIDTH); persist(DEFAULT_SIDEBAR_WIDTH) },
      onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
        const next = keyboardSidebarWidth(event.key, width, viewport)
        if (next === null) return
        event.preventDefault(); setPreferred(next); persist(next)
      },
    },
  }
}
