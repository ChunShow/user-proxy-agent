import { useEffect, useRef, useState } from 'react'
import { request } from '../chat/conversations'
import { ChatError } from '../chat/stream'
import './actions.css'

type Status = 'pending' | 'executing' | 'succeeded' | 'failed' | 'unknown' | 'rejected' | 'expired'
interface Proposal {
  id: string; source_user_message_id: string; kind: 'calendar_event' | 'email'; status: Status
  version: number; account_email: string
  payload: { title?: string; start?: string; end?: string; description?: string; location?: string; to?: string[]; subject?: string; body?: string }
}
function calendarTime(value = '') {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', year: 'numeric', month: 'long', day: 'numeric', weekday: 'short', hour: 'numeric', minute: '2-digit' }).format(date)
}
const labels: Record<Status, string> = { pending: '내용을 확인해 주세요', executing: '실행 결과 확인 중', succeeded: '완료', failed: '실행하지 못했어요', unknown: '실행 여부 확인 필요', rejected: '취소됨', expired: '확인 기한이 지났어요' }
function merge(previous: Proposal[], items: Proposal[]) {
  const records = new Map(previous.map(item => [item.id, item]))
  for (const item of items) if (!records.has(item.id) || records.get(item.id)!.version <= item.version) records.set(item.id, item)
  return [...records.values()]
}
export default function ActionCards({ conversationId }: { conversationId: string }) {
  const [items, setItems] = useState<Proposal[]>([])
  const [error, setError] = useState('')
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState<string[]>([])
  const locked = useRef(new Set<string>())
  const alive = useRef(false)
  const reload = useRef(() => {})
  useEffect(() => {
    alive.current = true
    let current = true, running = false
    let timer: ReturnType<typeof setTimeout>
    const controller = new AbortController()
    async function poll() {
      if (!current || running || document.hidden) return
      running = true; clearTimeout(timer)
      try {
        const result = await request<{ items: Proposal[] }>(`/api/conversations/${encodeURIComponent(conversationId)}/actions`, undefined, controller.signal)
        if (!Array.isArray(result.items)) throw new Error('invalid actions')
        if (current) { setItems(old => merge(old, result.items)); setError('') }
      } catch { if (current) setError('실행 요청을 불러오지 못했어요.') }
      finally { running = false; if (current && !document.hidden) timer = setTimeout(() => void poll(), 2500) }
    }
    function resume() { clearTimeout(timer); void poll() }
    reload.current = resume
    document.addEventListener('visibilitychange', resume); void poll()
    return () => { current = false; alive.current = false; controller.abort(); clearTimeout(timer); document.removeEventListener('visibilitychange', resume) }
  }, [conversationId])
  async function decide(item: Proposal, decision: 'approve' | 'reject') {
    if (locked.current.has(item.id)) return
    locked.current.add(item.id); setBusy([...locked.current]); setErrors(old => ({ ...old, [item.id]: '' }))
    try {
      const next = await request<Proposal>(`/api/actions/${encodeURIComponent(item.id)}/${decision}`, { expected_version: item.version })
      if (alive.current) setItems(old => merge(old, [next]))
    } catch (cause) {
      const message = cause instanceof ChatError && cause.code === 'integration_permission_required'
        ? '앱 연결에서 일정 등록·메일 발송 권한을 먼저 허용해 주세요.'
        : cause instanceof ChatError && cause.code === 'action_account_changed'
          ? '연결 계정이 바뀌었어요. 현재 계정으로 새 실행안을 요청해 주세요.'
          : '처리 결과를 확인하지 못했어요. 상태를 다시 불러온 뒤 확인해 주세요.'
      if (alive.current) setErrors(old => ({ ...old, [item.id]: message }))
    } finally {
      locked.current.delete(item.id)
      if (alive.current) { setBusy([...locked.current]); reload.current() }
    }
  }
  return <div className="action-requests">
    {error && <div className="conversation-notice"><p role="status">{error}</p><button onClick={() => reload.current()}>실행 요청 다시 확인</button></div>}
    {items.map(item => <section key={item.id} className="action-card" aria-label={item.kind === 'email' ? '메일 발송 확인' : '일정 등록 확인'}>
      <header><h3>{item.kind === 'email' ? '메일 발송' : '일정 등록'}</h3><span role="status">{labels[item.status]}</span></header>
      <p className="action-account">{item.account_email}{item.kind === 'calendar_event' && ' · 기본 캘린더'}</p>
      <dl>{item.kind === 'email' ? <>
        <dt>받는 사람</dt><dd>{item.payload.to?.join(', ')}</dd>
        <dt>제목</dt><dd>{item.payload.subject}</dd><dt>본문</dt><dd className="action-body">{item.payload.body}</dd>
      </> : <><dt>일정</dt><dd>{item.payload.title}</dd>
        <dt>시간 <span className="action-timezone">한국 시간</span></dt><dd><time dateTime={item.payload.start} title={item.payload.start}>{calendarTime(item.payload.start)}</time><br />~ <time dateTime={item.payload.end} title={item.payload.end}>{calendarTime(item.payload.end)}</time></dd>
        {item.payload.location && <><dt>장소</dt><dd>{item.payload.location}</dd></>}
        {item.payload.description && <><dt>설명</dt><dd className="action-body">{item.payload.description}</dd></>}
      </>}</dl>
      {item.status === 'pending' && <><p className="action-note">확인한 내용으로 한 번 실행합니다. 수정하려면 취소 후 채팅으로 다시 요청해 주세요. 30분 뒤 만료됩니다.</p>
        <div className="action-buttons"><button className="action-confirm" disabled={busy.includes(item.id)} onClick={() => void decide(item, 'approve')}>{item.kind === 'email' ? '확인하고 발송' : '확인하고 등록'}</button><button disabled={busy.includes(item.id)} onClick={() => void decide(item, 'reject')}>취소</button></div></>}
      {item.status === 'unknown' && <p className="action-note">응답을 확인하지 못해 자동으로 다시 실행하지 않습니다. Google에서 실제 메일 또는 일정을 먼저 확인해 주세요.</p>}
      {item.status === 'failed' && <p className="action-note">권한과 계정을 확인한 뒤 채팅에서 새로 요청해 주세요.</p>}
      {item.status === 'succeeded' && <p className="action-note">Google에서 {item.kind === 'email' ? '발송' : '등록'}을 확인했습니다.</p>}
      {errors[item.id] && <p className="action-error" role="alert">{errors[item.id]}</p>}
    </section>)}
  </div>
}
