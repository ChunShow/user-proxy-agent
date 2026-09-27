import { useEffect, useRef, useState } from 'react'
import Icon from '../components/Icon'
import { deleteConversation, renameConversation } from './conversations'
import type { Conversation } from './conversations'

type Edit = { mode: 'rename' | 'delete'; item: Conversation }

export default function ConversationList({ items, selectedId, error, hasMore, onSelect, onMore, onReload, onChanged }: {
  items: Conversation[]; selectedId: string | null; error: string; hasMore: boolean
  onSelect: (id: string) => void; onMore: () => void; onReload: () => void
  onChanged: (deletedId?: string) => void
}) {
  const [edit, setEdit] = useState<Edit | null>(null)
  const [title, setTitle] = useState('')
  const [pending, setPending] = useState(false)
  const [problem, setProblem] = useState('')
  const dialog = useRef<HTMLDialogElement>(null)
  const list = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLElement | null>(null)
  useEffect(() => {
    const close = (event: Event) => {
      list.current?.querySelectorAll('details[open]').forEach(details => {
        if (event.type === 'keydown' || !details.contains(event.target as Node)) details.removeAttribute('open')
      })
    }
    const key = (event: KeyboardEvent) => { if (event.key === 'Escape') close(event) }
    document.addEventListener('click', close); document.addEventListener('keydown', key)
    return () => { document.removeEventListener('click', close); document.removeEventListener('keydown', key) }
  }, [])
  useEffect(() => { if (edit) dialog.current?.showModal() }, [edit])
  function open(next: Edit, source: HTMLElement) {
    trigger.current = source.closest('details')?.querySelector('summary') ?? source
    source.closest('details')?.removeAttribute('open')
    setTitle(next.mode === 'rename' ? next.item.title : '')
    setProblem(''); setEdit(next)
  }
  async function save() {
    if (!edit || pending) return
    setPending(true); setProblem('')
    try {
      if (edit.mode === 'rename') await renameConversation(edit.item.id, title)
      else await deleteConversation(edit.item.id)
      onChanged(edit.mode === 'delete' ? edit.item.id : undefined)
      dialog.current?.close()
    } catch (error) { setProblem(error instanceof Error ? error.message : '변경하지 못했습니다. 다시 시도해 주세요.') }
    finally { setPending(false) }
  }
  return <div className="conversation-list" ref={list}>
    <p className="conversation-list-heading">이전 대화</p>
    <div className="conversation-items">
      {items.map(item => <div key={item.id} className={`conversation-row ${item.id === selectedId ? 'selected' : ''}`}>
        <button type="button" title={item.title} className="conversation-link"
          aria-current={item.id === selectedId ? 'page' : undefined} onClick={() => onSelect(item.id)}>{item.title}</button>
        <details className="conversation-options" onToggle={event => {
          if (event.currentTarget.open) event.currentTarget.querySelector('.conversation-menu')?.scrollIntoView({ block: 'nearest' })
        }}>
          <summary aria-label={`${item.title} 대화 관리`} title="대화 관리"><Icon name="more" /></summary>
          <div className="conversation-menu">
            <button type="button" onClick={event => open({ mode: 'rename', item }, event.currentTarget)}><Icon name="compose" />이름 변경</button>
            <button type="button" className="danger-text" onClick={event => open({ mode: 'delete', item }, event.currentTarget)}><Icon name="trash" />삭제</button>
          </div>
        </details>
      </div>)}
      {!items.length && !error && <p className="conversation-list-empty">아직 저장된 대화가 없습니다.</p>}
      {error && <div className="list-error"><p role="status">{error}</p><button type="button" onClick={onReload}>목록 다시 불러오기</button></div>}
      {hasMore && <button className="conversation-link more-conversations" type="button" onClick={onMore}>대화 더 보기</button>}
    </div>
    <dialog ref={dialog} className="conversation-dialog" aria-label={edit?.mode === 'delete' ? '대화 삭제' : '대화 이름 변경'}
      onCancel={event => { if (pending) event.preventDefault() }} onClose={() => { setEdit(null); trigger.current?.focus() }}>
      <form onSubmit={event => { event.preventDefault(); void save() }}>
        <h2>{edit?.mode === 'delete' ? '대화를 삭제할까요?' : '대화 이름 변경'}</h2>
        {edit?.mode === 'rename' && <label className="conversation-title-field">대화 이름
          <input autoFocus value={title} onChange={event => setTitle(event.target.value)} maxLength={80} disabled={pending} />
        </label>}
        {edit?.mode === 'delete' && <p className="delete-description"><strong>{edit.item.title}</strong><br />목록에서 이 대화가 사라집니다. 연결된 일정과 메일은 삭제되지 않습니다.</p>}
        {pending && <p className="dialog-feedback" role="status">처리 중입니다.</p>}
        {problem && <p className="dialog-error" role="alert">{problem}</p>}
        <div className="conversation-dialog-actions">
          <button type="button" disabled={pending} onClick={() => dialog.current?.close()}>취소</button>
          <button className={edit?.mode === 'delete' ? 'delete-confirm' : 'save-title'} type="submit" disabled={pending || (edit?.mode === 'rename' && !title.trim())}>{edit?.mode === 'delete' ? '삭제' : '저장'}</button>
        </div>
      </form>
    </dialog>
  </div>
}
