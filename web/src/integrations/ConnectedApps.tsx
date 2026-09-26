import { useEffect, useRef, useState } from 'react'
import { prepareSession } from '../chat/conversations'
import { connectGoogle, disconnectGoogle, getGoogle } from './apps'
import type { GoogleConnection } from './apps'
import './apps.css'

const labels = { not_configured: '연결 설정이 필요해요', disconnected: '아직 연결하지 않았어요', connected: '연결됨', reconnect_required: '다시 로그인해 주세요' }
const callbackNotes: Record<string, string> = {
  connected: 'Google 계정 연결을 확인했어요.', oauth_denied: '연결을 취소했어요. 필요할 때 다시 연결할 수 있어요.',
  oauth_invalid: '연결 요청이 만료되었거나 확인되지 않았어요. 다시 시작해 주세요.',
}
export default function ConnectedApps() {
  const dialog = useRef<HTMLDialogElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const [open, setOpen] = useState(() => new URLSearchParams(location.search).get('apps') === 'google')
  const [attempt, setAttempt] = useState(0)
  const [state, setState] = useState<GoogleConnection | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [note, setNote] = useState(() => {
    const result = new URLSearchParams(location.search).get('result')
    return result ? callbackNotes[result] ?? '계정을 연결하지 못했어요. 설정과 허용 권한을 확인한 뒤 다시 시도해 주세요.' : ''
  })
  useEffect(() => {
    if (!open) return
    dialog.current?.showModal()
    const controller = new AbortController()
    prepareSession().then(() => getGoogle(controller.signal)).then(value => {
      if (!controller.signal.aborted) { setState(value); setError('') }
    }, () => { if (!controller.signal.aborted) setError('연결 상태를 불러오지 못했어요.') })
    const url = new URL(location.href)
    if (url.searchParams.has('apps')) {
      url.searchParams.delete('apps'); url.searchParams.delete('result')
      history.replaceState(null, '', url)
    }
    return () => controller.abort()
  }, [open, attempt])
  async function connect(allowWrite = false) {
    setBusy(true); setError('')
    try {
      const { url } = await connectGoogle(allowWrite)
      const destination = new URL(url)
      if (destination.origin !== 'https://accounts.google.com' || destination.pathname !== '/o/oauth2/v2/auth') throw new Error('unexpected destination')
      if (dialog.current?.open) location.assign(destination.href)
    } catch { setError('연결을 시작하지 못했어요. 서버 설정을 확인한 뒤 다시 시도해 주세요.') }
    finally { setBusy(false) }
  }
  async function disconnect() {
    setBusy(true); setError('')
    try {
      const result = await disconnectGoogle()
      setState({ status: 'disconnected', email: null, calendar_read: false, gmail_read: false })
      setNote(result.revoked ? 'Google 연결을 해제했어요.' : '이 서비스의 연결은 해제했어요. Google 계정에서도 접근 권한을 확인해 주세요.')
    } catch { setError('연결 해제를 확인하지 못했어요. 상태를 다시 확인해 주세요.') }
    finally { setBusy(false) }
  }
  return <>
    <button ref={trigger} className="apps-trigger" onClick={() => { setOpen(true); setAttempt(n => n + 1) }}>앱 연결</button>
    <dialog ref={dialog} className="apps-dialog" aria-labelledby="apps-title" onClose={() => { setOpen(false); trigger.current?.focus() }}>
      <header><h2 id="apps-title">앱 연결</h2><button className="apps-close" aria-label="앱 연결 닫기" onClick={() => dialog.current?.close()}>닫기</button></header>
      <p className="apps-intro">필요한 정보를 찾고, 같은 채팅에서 일을 이어가세요.</p>
      <section aria-label="Google 연결" className="apps-account">
        <div className="apps-heading"><h3>Google</h3><span role="status">{state ? labels[state.status] : '확인 중'}</span></div>
        {state?.email && <p className="apps-email">{state.email}</p>}
        <p>Google Calendar에서 일정을 확인하고 Gmail에서 메일을 찾아 읽습니다.</p>
        <p className="apps-muted">처음에는 조회 권한만 요청합니다. 등록·발송은 별도로 허용하고, 매번 내용을 확인한 뒤 실행합니다. 조회한 내용은 답변을 위해 설정된 AI 모델에 전달됩니다.</p>
        {state?.status === 'connected' && <ul className="apps-permissions">
          <li>{state.calendar_read ? 'Calendar 조회 가능' : 'Calendar 권한이 필요해요'}</li>
          <li>{state.gmail_read ? 'Gmail 조회 가능' : 'Gmail 권한이 필요해요'}</li>
          <li>{state.calendar_write && state.gmail_send ? '일정 등록·메일 발송 가능 (실행 전 확인)' : '등록·발송은 추가 동의가 필요해요'}</li>
        </ul>}
        {state?.status === 'not_configured' && <details className="apps-setup"><summary>설정 방법 보기</summary>
          <p>서버의 <code>.env</code>에 <code>GOOGLE_CLIENT_ID</code>와 <code>GOOGLE_CLIENT_SECRET</code>을 등록해 주세요. Google Cloud에서 Calendar와 Gmail API를 켜고 웹 앱 OAuth 클라이언트를 만듭니다.</p>
          <p>승인된 리디렉션 URI</p><code className="apps-uri">{location.origin}/api/integrations/google/callback</code>
          <p>자세한 순서는 프로젝트의 <code>docs/google-setup.md</code>에 정리되어 있습니다.</p>
        </details>}
        <div className="apps-actions">
          {state?.status !== 'connected' && <button className="apps-primary" disabled={busy || !state || state.status === 'not_configured'} onClick={() => void connect()}>Google 계정 연결</button>}
          {state?.status === 'connected' && (!state.calendar_read || !state.gmail_read) && <button disabled={busy} onClick={() => void connect()}>권한 다시 확인</button>}
          {state?.status === 'connected' && (!state.calendar_write || !state.gmail_send) && <button disabled={busy} onClick={() => void connect(true)}>일정 등록·메일 발송 허용</button>}
          {state && ['connected', 'reconnect_required'].includes(state.status) && <button disabled={busy} onClick={() => void disconnect()}>연결 해제</button>}
          {error && <button disabled={busy} onClick={() => setAttempt(n => n + 1)}>다시 확인</button>}
        </div>
      </section>
      {error && <p className="apps-error" role="alert">{error}</p>}
      {note && <p className="apps-muted" role="status">{note}</p>}
    </dialog>
  </>
}
