import { useEffect, useState } from 'react'
import { fetchHealth } from '../api'

type State = 'loading' | 'connected' | 'error'
const labels = { loading: '서버 확인 중', connected: '서버 연결됨', error: '서버 연결 실패' }

export default function ConnectionStatus() {
  const [state, setState] = useState<State>('loading')
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    fetchHealth(controller.signal).then(
      () => { if (!controller.signal.aborted) setState('connected') },
      () => { if (!controller.signal.aborted) setState('error') },
    )
    return () => controller.abort()
  }, [attempt])
  return <button className={`connection-status ${state}`} type="button"
    aria-label="서버 연결 다시 확인" disabled={state === 'loading'}
    title={state === 'error' ? '서버가 실행 중인지 확인하고 다시 눌러 주세요.' : '웹과 서버 연결만 확인합니다. 눌러서 다시 확인'}
    onClick={() => { setState('loading'); setAttempt(n => n + 1) }}>
    <span className="status-dot" aria-hidden="true" />
    <span role="status">{labels[state]}</span>
  </button>
}
