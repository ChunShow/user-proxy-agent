import { useLayoutEffect, useRef } from 'react'
import type { RefObject } from 'react'
import Icon from '../components/Icon'

interface Props {
  value: string
  onChange: (value: string) => void
  onSubmit: (text: string) => void
  inputRef: RefObject<HTMLTextAreaElement | null>
  targetLabel?: string
  onClearTarget: () => void
}

export default function Composer({ value, onChange, onSubmit, inputRef, targetLabel, onClearTarget }: Props) {
  const composing = useRef(false)
  useLayoutEffect(() => {
    const input = inputRef.current
    if (input) { input.style.height = 'auto'; input.style.height = `${Math.min(input.scrollHeight, 144)}px` }
  }, [value, inputRef])
  function submit() {
    if (!value.trim() || composing.current) return
    onSubmit(value.trim())
    inputRef.current?.focus()
  }
  return <div className="composer-dock">
    <form className="composer" onSubmit={event => { event.preventDefault(); submit() }}>
      {targetLabel && <div className="composer-target"><span>{targetLabel}에 추가 지시 · 예시</span>
        <button type="button" aria-label="지시 대상 해제" onClick={onClearTarget}><Icon name="close" /></button>
      </div>}
      <div className="composer-row">
        <label className="sr-only" htmlFor="message-input">메시지</label>
        <textarea id="message-input" ref={inputRef} value={value} rows={1} placeholder="메시지를 입력하세요"
          onChange={event => onChange(event.target.value)}
          onCompositionStart={() => { composing.current = true }}
          onCompositionEnd={() => { composing.current = false }}
          onKeyDown={event => {
            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229 && !composing.current) {
              event.preventDefault(); submit()
            }
          }} />
        <button className="send-button" type="submit" aria-label="메시지 추가" title="메시지 추가" disabled={!value.trim()}><Icon name="arrow" /></button>
      </div>
    </form>
    <div className="composer-note"><p>화면 미리보기 · 자동 응답과 저장은 아직 지원하지 않습니다.</p><span>Shift + Enter로 줄바꿈</span></div>
  </div>
}
