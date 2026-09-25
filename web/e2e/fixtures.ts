import type { Page } from '@playwright/test'

type Message = { id: string; role: string; text: string; status: string; retryable: boolean }
export async function mockConversations(page: Page) {
  const conversations = new Map<string, { id: string; title: string; updated_at: string; messages: Message[] }>()
  const calls: Record<string, string>[] = []
  await page.route('**/api/calls/active', route => route.fulfill({ json: { items: [] } }))
  await page.route('**/api/health', route => route.fulfill({ json: { status: 'ok', service: 'agent-service' } }))
  await page.route('**/api/session', route => route.fulfill({ status: 204 }))
  await page.route('**/api/conversations**', route => {
    const request = route.request(), url = new URL(request.url())
    if (request.method() === 'POST') {
      const { conversation_id: id } = request.postDataJSON()
      if (!conversations.has(id)) conversations.set(id, { id, title: '새 대화', updated_at: new Date().toISOString(), messages: [] })
      return route.fulfill({ status: 201, json: conversations.get(id) })
    }
    if (url.pathname.endsWith('/calls')) return route.fulfill({ json: { items: [], next_cursor: null } })
    const id = url.pathname.split('/')[3]
    if (id) {
      const conversation = conversations.get(id)
      return conversation ? route.fulfill({ json: { conversation, messages: conversation.messages, next_cursor: null } })
        : route.fulfill({ status: 404, json: { error: { code: 'not_found' } } })
    }
    return route.fulfill({ json: { items: [...conversations.values()].reverse(), next_cursor: null } })
  })
  await page.route('**/api/chat', route => {
    const body = route.request().postDataJSON()
    calls.push(body)
    const conversation = conversations.get(body.conversation_id)
    if (!conversation) return route.fulfill({ status: 422, json: { error: { code: 'invalid_request' } } })
    const uid = crypto.randomUUID(), aid = body.retry_message_id || crypto.randomUUID()
    if (body.content) {
      if (!conversation.messages.length) conversation.title = body.content.replace(/\s+/g, ' ').slice(0, 40)
      conversation.messages.push({ id: uid, role: 'user', text: body.content, status: 'completed', retryable: false })
    }
    const reply = { id: aid, role: 'assistant', text: '테스트 응답입니다.', status: 'completed', retryable: false }
    conversation.messages = [...conversation.messages.filter(m => m.id !== aid), reply]
    const frame = (event: string, extra = {}) => `event: ${event}\ndata: ${JSON.stringify({ request_id: body.request_id, conversation_id: body.conversation_id, message_id: aid, user_message_id: uid, ...extra })}\n\n`
    return route.fulfill({ contentType: 'text/event-stream', body: frame('start') + frame('delta', { text: reply.text }) + frame('done') })
  })
  return { conversations, calls }
}
