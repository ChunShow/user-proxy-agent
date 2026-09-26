import { useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { audioLabel, latestAudioProgress, transcriptRows } from './transcriptData'
import useCallActivity from './useCallActivity'

export default function CallTranscript({ callId, active }: { callId: string; active: boolean }) {
  const [expanded, setExpanded] = useState(active)
  const activity = useCallActivity(callId, expanded)
  const rows = useMemo(() => transcriptRows(activity.events), [activity.events])
  const progress = useMemo(() => latestAudioProgress(activity.events), [activity.events])
  const last = rows.at(-1)
  const revision = `${last?.id ?? 0}:${last?.text.length ?? 0}`
  const id = useId()
  const scroll = useRef<HTMLDivElement>(null)
  const following = useRef(true)
  const [read, setRead] = useState({ following: true, revision: '' })
  useLayoutEffect(() => {
    if (expanded && following.current && scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight
  }, [expanded, revision])
  function latest() {
    following.current = true
    setRead({ following: true, revision })
    if (scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight
  }
  return <div className="call-transcript">
    <button className="call-transcript-toggle" type="button" aria-expanded={expanded} aria-controls={id}
      aria-label={expanded ? '통화 내용 접기' : '통화 내용 보기'} onClick={() => setExpanded(value => !value)}>
      <span>통화 내용</span><span>{expanded ? '접기' : '보기'}</span>
    </button>
    {expanded && <div id={id}>
      <p className="call-transcript-caption">자동 전사 · 실제 발화와 다를 수 있습니다</p>
      <div className="call-transcript-scroll" ref={scroll} role="region" aria-label="통화 내용" tabIndex={0}
        onScroll={() => {
          const el = scroll.current
          if (!el) return
          const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40
          following.current = atBottom
          setRead(previous => ({
            following: atBottom,
            revision: atBottom || previous.following ? revision : previous.revision,
          }))
        }}>
        {rows.length ? <ol>{rows.map(row => <li key={row.id} className={`call-transcript-row speaker-${row.role}`}>
          <span className="call-transcript-speaker">{row.role === 'caller' ? '상대방' : '도우미'}</span>
          <p>{row.text}</p>
        </li>)}</ol> : <p className="call-transcript-empty">{!activity.loaded
          ? activity.error ? '통화 내용을 불러오지 못했어요.' : '통화 내용을 불러오는 중이에요.'
          : activity.terminal ? '저장된 통화 내용이 없습니다.' : '통화 내용을 기다리고 있어요.'}</p>}
      </div>
      {!read.following && read.revision !== revision && <button className="call-transcript-latest" type="button" onClick={latest}>새 내용 보기</button>}
      <p className="call-transcript-caption" title="통화 전체의 음성 전송 상태입니다. 개별 문장의 청취 완료를 뜻하지 않습니다.">
        {audioLabel(progress, activity.terminal)}
      </p>
      {activity.error && <div className="call-transcript-error"><p role="status">내용을 갱신하지 못했어요.</p>
        <button type="button" onClick={activity.reload}>통화 내용 다시 불러오기</button></div>}
    </div>}
  </div>
}
