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
