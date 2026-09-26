import Icon from '../components/Icon'
import CallTranscript from './CallTranscript'
import { callResult } from './callResult'
import { isActiveCall } from './calls'
import type { CallConfirmation, PhoneCall, PhoneStatus } from './calls'

const phoneLabels: Record<PhoneStatus, string> = {
  preparing: '통화 준비 중', dialing: '연결 중', connected: '통화 중', ending: '종료 확인 중',
  ended: '종료됨', failed: '연결하지 못함', canceled: '발신 취소됨', unknown: '연결 확인 필요',
}
const callProblems: Record<string, string> = {
  call_no_answer: '상대가 전화를 받지 않았습니다.',
  call_busy: '상대가 통화 중이어서 연결하지 못했습니다.',
  call_failed: '회선 연결에 실패했습니다.',
  call_canceled: '통화 연결이 취소됐습니다.',
  call_audio_failed: '통화 음성을 연결하거나 전달하는 중 문제가 발생했습니다.',
}
export interface PhoneActions {
  onStop: (id: string) => void
  onRefresh: (id: string) => void
  pending?: boolean
  error?: string
  onAnswer?: (id: string, question: CallConfirmation, text: string) => void
  onReply?: (id: string, question: CallConfirmation) => void
  answerPending?: string[]
}
export default function PhoneCallCard({ call, onStop, onRefresh, pending, error, onAnswer, onReply, answerPending = [] }: PhoneActions & { call: PhoneCall }) {
  const result = callResult(call)
  const active = isActiveCall(call)
  const ending = call.status === 'ending' || call.stop_requested
  const label = ending && active && call.status !== 'unknown' ? '종료 확인 중' : phoneLabels[call.status]
  const problem = call.error_code ? callProblems[call.error_code] : undefined
  return <section className="phone-card" aria-label={`${call.subject} 통화`}>
    <div className="phone-heading"><h2><Icon name="phone" />{call.subject}</h2><span className={`phone-state phone-${call.status}`} role="status">{label}</span></div>
    <p className="phone-destination">{call.destination}</p>
    <p className="phone-purpose">{call.purpose}</p>
    <CallTranscript key={call.id} callId={call.id} active={active} />
    {call.confirmations?.map(question => {
      const waiting = active && !ending && question.status === 'pending'
      const labels = { pending: waiting ? '답변을 기다리고 있어요' : '질문 마감', answered: '통화에 전달 중', applied: '통화 도우미에게 전달됨', expired: '답변 시간이 지났어요', canceled: '질문 마감', failed: '통화에 반영하지 못했어요' }
      return <div key={question.id} className="call-question" aria-label="통화 중 확인 질문">
        <p className="call-question-caption">통화 중 확인이 필요해요</p>
        <p className="call-question-text">{question.question}</p>
        {question.answer && <p className="call-question-answer">{question.answer}</p>}
        <p className="phone-note" role="status">{labels[question.status]}</p>
        {waiting && <div className="call-question-actions">
          {question.options.map(option => <button key={option} type="button" disabled={answerPending.includes(question.id)} onClick={() => onAnswer?.(call.id, question, option)}>{option}</button>)}
          <button type="button" disabled={answerPending.includes(question.id)} onClick={() => onReply?.(call.id, question)}>직접 답변하기</button>
        </div>}
      </div>
    })}
    {result.summary && <div className="phone-result"><p className="phone-result-label">통화 도우미가 정리한 결과</p><p>{result.summary}</p></div>}
    {result.note && <p className="phone-note">{result.note}</p>}
    {call.status === 'unknown' && <p className="phone-problem">발신 결과를 확인하지 못했습니다. ClawOps에서 회선 상태를 확인해야 합니다. 자동으로 다시 걸지 않습니다.</p>}
    {problem && <p className="phone-problem" role="status">{problem}</p>}
    {call.status === 'failed' && !problem && <p className="phone-problem">통화를 연결하지 못했습니다. 서버의 통화 설정과 회선 상태를 확인해 주세요.</p>}
    {call.error_code === 'call_end_unconfirmed' && <p className="phone-problem">회선 종료를 아직 확인하지 못했습니다. 다시 확인해 주세요.</p>}
    {call.error_code === 'call_status_unavailable' && <p className="phone-problem">현재 회선 상태를 확인하지 못했습니다.</p>}
    {error && <p className="phone-problem" role="status">{error}</p>}
    {active && <div className="phone-actions">
      {call.status !== 'unknown' && <button className="phone-stop" type="button" disabled={pending || ending} onClick={() => onStop(call.id)}><Icon name="phone" />{ending ? '종료 요청됨' : '통화 종료'}</button>}
      {(call.error_code || error || call.status === 'unknown') && <button type="button" disabled={pending} onClick={() => onRefresh(call.id)}>다시 확인</button>}
    </div>}
  </section>
}

export function ActivePhoneCall({ call, onOpen, onStop, onRefresh, pending, error }: PhoneActions & { call: PhoneCall; onOpen: () => void }) {
  return <section className="active-phone" aria-label="진행 중인 통화">
    <div><span>{call.subject}</span><span className="phone-note" role="status">{call.stop_requested && call.status !== 'unknown' ? '종료 확인 중' : phoneLabels[call.status]}</span></div>
    {call.confirmations?.some(q => q.status === 'pending') && <span role="status">확인할 질문이 도착했어요</span>}
    <div className="active-phone-actions"><button type="button" onClick={onOpen}>대화로 이동</button>
      {call.status !== 'unknown' && <button className="phone-stop" type="button" disabled={pending || call.stop_requested || call.status === 'ending'} onClick={() => onStop(call.id)}>통화 종료</button>}
      {(error || call.error_code) && <button type="button" disabled={pending} onClick={() => onRefresh(call.id)}>다시 확인</button>}
    </div>
    {error && <p role="status">{error}</p>}
  </section>
}
