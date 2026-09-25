import type { Conversation } from './conversations'

export default function ConversationList({ items, selectedId, error, hasMore, onSelect, onMore, onReload }: {
  items: Conversation[]; selectedId: string | null; error: string; hasMore: boolean
  onSelect: (id: string) => void; onMore: () => void; onReload: () => void
}) {
  return <div className="conversation-list">
    <p className="conversation-list-heading">이전 대화</p>
    {items.map(item => <button key={item.id} type="button" title={item.title}
      className={`conversation-link ${item.id === selectedId ? 'selected' : ''}`}
      aria-current={item.id === selectedId ? 'page' : undefined} onClick={() => onSelect(item.id)}>{item.title}</button>)}
    {!items.length && !error && <p className="conversation-list-empty">아직 저장된 대화가 없습니다.</p>}
    {error && <div className="list-error"><p role="status">{error}</p><button type="button" onClick={onReload}>목록 다시 불러오기</button></div>}
    {hasMore && <button className="conversation-link more-conversations" type="button" onClick={onMore}>대화 더 보기</button>}
  </div>
}
