import { useEffect, useId, useRef, useState } from 'react'
import Icon from '../components/Icon'
import type { CallAction, CallViewModel } from './types'

const callLabels = { dialing: '연결 중', connected: '통화 중', ending: '종료 확인 중', ended: '통화 종료됨', failed: '연결 실패' }
const taskLabels = { working: '확인 중', needs_input: '추가 확인 필요', succeeded: '목표 달성', incomplete: '미완료' }

export default function CallCard({ call, onAction }: { call: CallViewModel; onAction: (action: CallAction) => void }) {
  const [expanded, setExpanded] = useState(false)
  const transcriptId = useId()
  const transcript = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (expanded) transcript.current?.scrollIntoView({ block: 'nearest' })
  }, [expanded])
  const active = call.callStatus === 'connected'
  const canEnd = active || call.callStatus === 'dialing'
  return <article className={`call-card call-${call.callStatus}`} aria-label={`${call.subject} 통화`}>
    <div className="call-purpose"><h2>{call.purpose}</h2><span className={`task-status task-${call.taskStatus}`} role="status">{taskLabels[call.taskStatus]}</span></div>
    <div className="call-line"><span>{call.subject}</span><span className="call-status" role="status"><i aria-hidden="true" />{callLabels[call.callStatus]}</span></div>
    <p className="call-summary">{call.summary}</p>
    <div className="call-actions">
      <button type="button" disabled={!active} onClick={() => onAction(call.listening ? 'stop_listening' : 'listen')} aria-pressed={call.listening}><Icon name="volume" />{call.listening ? '듣기 끄기' : '듣기'}</button>
      <button type="button" disabled={!active} onClick={() => onAction('instruct')}><Icon name="chat" />추가 지시</button>
      <button className="end-call" type="button" disabled={!canEnd} onClick={() => onAction('end')}><span className="stop-square" aria-hidden="true" />통화 종료</button>
    </div>
    <p className="audio-note" role="status">{call.listening ? '듣기 켜짐 (예시)' : '음성은 재생되지 않는 화면 예시입니다.'}</p>
    <button className="transcript-toggle" type="button" aria-expanded={expanded} aria-controls={transcriptId} onClick={() => setExpanded(!expanded)}>
      <span>통화 내역 {expanded ? '접기' : '보기'}</span><Icon name="chevron" />
    </button>
    <div className="transcript" id={transcriptId} ref={transcript} hidden={!expanded}>
      <p className="transcript-note">예시 대화 · 실제 통화 기록이 아닙니다</p>
      {call.transcript.length === 0 ? <p className="transcript-empty">아직 통화 내역이 없습니다.</p> : call.transcript.map(line => <div key={line.id} className={`transcript-line ${line.final ? '' : 'partial'}`}>
        <span className="transcript-speaker">{line.speaker === 'agent' ? '비서' : '상대'}<small>{line.final ? '확정 · 예시' : '발화 중 · 예시'}</small></span>
        <p>{line.text}</p>
      </div>)}
    </div>
  </article>
}
