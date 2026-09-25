import { useEffect, useState } from 'react'

import { fetchHealth } from './api'

type ConnectionState = 'loading' | 'connected' | 'error'

const messages = {
  loading: { title: '연결을 확인하고 있어요', detail: '잠시만 기다려 주세요.' },
  connected: { title: '연결되었습니다', detail: '웹과 서버가 정상적으로 연결되어 있습니다.' },
  error: { title: '연결할 수 없습니다', detail: '서버가 실행 중인지 확인한 뒤 다시 시도해 주세요.' },
}

export default function App() {
  const [state, setState] = useState<ConnectionState>('loading')
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    fetchHealth(controller.signal).then(
      () => { if (!controller.signal.aborted) setState('connected') },
      () => { if (!controller.signal.aborted) setState('error') },
    )
    return () => controller.abort()
  }, [attempt])

  function retry() {
    setState('loading')
    setAttempt((previous) => previous + 1)
  }

  return (
    <main className="page">
      <header className="brand">agent service<span className="brand-note">연결 확인</span></header>
      <section className={`connection-panel ${state}`} aria-labelledby="connection-title">
        <div className="connection-symbol" aria-hidden="true"><i /><span /><i /></div>
        <div role="status" aria-live="polite" aria-atomic="true">
          <h1 id="connection-title">{messages[state].title}</h1>
          <p className="description">{messages[state].detail}</p>
        </div>
        <button type="button" onClick={retry} disabled={state === 'loading'}>
          {state === 'loading' ? '확인 중…' : state === 'error' ? '다시 시도' : '다시 확인'}
          <span aria-hidden="true">↗</span>
        </button>
        <p className="scope-note">현재는 기본 연결을 확인하는 단계입니다.</p>
      </section>
    </main>
  )
}
