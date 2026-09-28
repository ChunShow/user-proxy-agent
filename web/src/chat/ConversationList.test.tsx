import assert from 'node:assert/strict'
import test from 'node:test'
import { renderToStaticMarkup } from 'react-dom/server'
import ConversationList from './ConversationList'

test('conversation navigation has no trash entry or restoration screen', () => {
  const html = renderToStaticMarkup(<ConversationList items={[]} selectedId={null} error="" hasMore={false}
    onSelect={() => {}} onMore={() => {}} onReload={() => {}} onChanged={() => {}} />)
  assert.ok(html.includes('이전 대화'))
  assert.doesNotMatch(html, /삭제한 대화|복원|trash-list/)
})

test('virtual conversations remain visibly distinct in the shared history', () => {
  const html = renderToStaticMarkup(<ConversationList items={[
    { id: 'sim', title: '진료시간 확인', updated_at: '', mode: 'simulation' },
    { id: 'real', title: '일반 요청', updated_at: '', mode: 'real' },
  ]} selectedId="sim" error="" hasMore={false}
    onSelect={() => {}} onMore={() => {}} onReload={() => {}} onChanged={() => {}} />)
  assert.equal((html.match(/가상 통화/g) ?? []).length, 1)
})
