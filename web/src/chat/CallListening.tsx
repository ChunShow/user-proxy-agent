import { useEffect, useRef, useState } from 'react'
import { CallAudioPlayer } from './callAudio'

export default function CallListening({ callId }: { callId: string }) {
  const [state, setState] = useState<'idle' | 'connecting' | 'listening'>('idle')
  const [note, setNote] = useState('')
  const generation = useRef(0)
  const resources = useRef<{ socket?: WebSocket; player: CallAudioPlayer; timer?: ReturnType<typeof setTimeout> } | null>(null)
  function dispose() {
    generation.current++
    const value = resources.current
    resources.current = null
    if (value) { clearTimeout(value.timer); value.socket?.close(); value.player.close() }
  }
  useEffect(() => () => dispose(), [callId])
  async function start() {
    if (resources.current) return
    const attempt = ++generation.current
    setNote(''); setState('connecting')
    try {
      const player = new CallAudioPlayer()
      resources.current = { player }
      await player.resume()
      if (generation.current !== attempt || !resources.current) return
      const url = new URL(`/api/calls/${encodeURIComponent(callId)}/listen`, location.href)
      url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
      const socket = new WebSocket(url)
      resources.current.socket = socket
      function failed(message = '듣기 연결이 끊겼어요. 통화는 계속 진행됩니다.') {
        if (generation.current !== attempt) return
        dispose(); setState('idle'); setNote(message)
      }
      resources.current.timer = setTimeout(() => failed('듣기를 연결하지 못했어요. 잠시 후 다시 시도해 주세요.'), 8000)
      socket.onmessage = event => {
        if (generation.current !== attempt || typeof event.data !== 'string' || event.data.length > 321000) return
        try {
          const message = JSON.parse(event.data)
          if (message.type === 'ready' && message.encoding === 'mulaw' && message.sample_rate === 8000) {
            clearTimeout(resources.current?.timer); setState('listening')
          } else if (message.type === 'audio' && ['caller', 'assistant'].includes(message.track) && typeof message.payload === 'string') {
            player.play(message.track, message.payload)
          } else if (message.type === 'clear' && message.track === 'assistant') player.clear('assistant')
          else if (message.type === 'closed') failed(message.reason === 'call_ended' ? '통화 듣기가 종료됐어요.' : '연결 지연으로 듣기를 중지했어요. 다시 연결할 수 있어요.')
        } catch { failed() }
      }
      socket.onerror = () => failed()
      socket.onclose = () => failed()
    } catch {
      if (generation.current === attempt) { dispose(); setState('idle'); setNote('브라우저에서 소리를 재생하지 못했어요. 다시 눌러 주세요.') }
    }
  }
  return <div className="call-listening">
    <button type="button" onClick={() => { if (state === 'idle') void start(); else { dispose(); setState('idle'); setNote('') } }}>
      {state === 'idle' ? '통화 듣기' : '듣기 중지'}
    </button>
    <span className="phone-note" role="status">{state === 'connecting' ? '소리 연결 중…' : state === 'listening' ? '통화 소리를 듣고 있어요 · 마이크 꺼짐' : note || '상대방과 도우미의 음성을 함께 듣습니다.'}</span>
  </div>
}
