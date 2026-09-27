import assert from 'node:assert/strict'
import test from 'node:test'
import { renderToStaticMarkup } from 'react-dom/server'
import MessageList from './MessageList'
import type { ChatMessage } from './types'

const render = (text: string, role: ChatMessage['role'] = 'assistant', status?: ChatMessage['status']) =>
  renderToStaticMarkup(<MessageList messages={[{ id: 'test', role, text, status }]} />)

test('assistant replies render Markdown and GFM structures', () => {
  const html = render('## 일정 안내\n\n**오후 6시**에 *만나요*.\n\n- 확인 완료\n- 다음 단계\n\n1. 첫 번째\n\n> 참고 사항\n\n`inline`\n\n```js\nconst time = 18\n```\n\n| 시간 | 상태 |\n| --- | --- |\n| 18시 | 가능 |\n\n~~취소~~\n\n[안내](https://example.com)')
  for (const markup of ['<h2>일정 안내</h2>', '<strong>오후 6시</strong>', '<em>만나요</em>', '<li>확인 완료</li>', '<ol>', '<blockquote>', '<code>inline</code>', '<pre', '<table>', '<th>시간</th>', '<del>취소</del>', 'href="https://example.com"', 'target="_blank"', 'rel="noopener noreferrer"']) {
    assert.ok(html.includes(markup), `Missing ${markup}`)
  }
})

test('user and notice messages preserve literal input', () => {
  for (const role of ['user', 'notice'] as const) {
    const html = render('**원문**\n- 목록', role)
    assert.ok(html.includes('**원문**\n- 목록'))
    assert.ok(!html.includes('<strong>'))
  }
})

test('untrusted Markdown cannot run HTML, unsafe links, or load remote images', () => {
  const html = render('<script>alert(1)</script>\n\n<img src="https://example.com/track">\n\n[위험](javascript:alert%281%29)\n\n[데이터](data:text/html,test)\n\n![그림 설명](https://example.com/image.png)')
  assert.doesNotMatch(html, /<script|<img|javascript:|data:text|src=/)
  assert.ok(html.includes('그림 설명'))
})

test('partial streaming Markdown renders without breaking reply state', () => {
  for (const partial of ['**오후', '[안내](https://', '```js\nconst', '| 시간 |\n| ---']) {
    const html = render(partial, 'assistant', 'streaming')
    assert.ok(html.includes('답변 작성 중'))
  }
  assert.ok(render('**오후 6시**', 'assistant', 'completed').includes('<strong>오후 6시</strong>'))
})

test('retry and attached cards remain available with formatted replies', () => {
  const html = renderToStaticMarkup(<MessageList
    messages={[{ id: 'retry', role: 'assistant', text: '**부분 응답**', status: 'failed', retryable: true, error: '연결 오류' }]}
    onRetry={() => {}} afterMessage={id => <li>카드 {id}</li>} />)
  assert.ok(html.includes('<strong>부분 응답</strong>'))
  assert.ok(html.includes('다시 시도'))
  assert.ok(html.includes('카드 retry'))
})

test('repeated assistant branding is replaced with a screen-reader label', () => {
  const html = render('답변')
  assert.ok(!html.includes('user proxy agent'))
  assert.ok(html.includes('message-author sr-only'))
})
