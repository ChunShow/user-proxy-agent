import { expect, test } from '@playwright/test'
import { mockConversations } from './fixtures'

const frame = (body: { request_id: string; conversation_id: string }, event: string, extra = {}) => `event: ${event}\ndata: ${JSON.stringify({ request_id: body.request_id, conversation_id: body.conversation_id, user_message_id: 'user-id', message_id: 'reply-id', ...extra })}\n\n`

test.beforeEach(async ({ page }) => {
  await mockConversations(page)
})

test('sends only new input, retries without duplicate users, and preserves the next draft', async ({ page }) => {
  const calls: { content?: string; retry_message_id?: string; conversation_id: string }[] = []
  await page.route('**/api/chat', route => {
    const body = route.request().postDataJSON()
    calls.push(body)
    const reply = calls.length === 1
      ? frame(body, 'start') + frame(body, 'delta', { text: '미완료 응답' }) + frame(body, 'error', { code: 'provider_unavailable', message: 'secret', retryable: true })
      : frame(body, 'start', calls.length > 2 ? { message_id: 'reply-2', user_message_id: 'user-2' } : {}) + frame(body, 'delta', { text: '완료된 답변', ...(calls.length > 2 ? { message_id: 'reply-2' } : {}) }) + frame(body, 'done', calls.length > 2 ? { message_id: 'reply-2' } : {})
    return route.fulfill({ contentType: 'text/event-stream', body: reply })
  })
  await page.goto('/')
  const input = page.getByRole('textbox', { name: '메시지' })
  await input.fill('첫 질문')
  await input.press('Enter')
  await expect(page.getByText('미완료 응답', { exact: true })).toBeVisible()
  await expect(page.getByText('secret', { exact: true })).not.toBeVisible()
  await input.fill('다음 초안')
  await page.getByRole('button', { name: '다시 시도', exact: true }).click()
  await expect(page.getByText('완료된 답변', { exact: true })).toBeVisible()
  await expect(input).toHaveValue('다음 초안')
  expect(calls[1].retry_message_id).toBe('reply-id')
  expect(calls[1].content).toBeUndefined()
  await expect(page.locator('.message.user')).toHaveCount(1)
  await input.press('Enter')
  await expect(page.locator('.message.user')).toHaveCount(2)
  await expect.poll(() => calls.length).toBe(3)
  expect(calls[2].content).toBe('다음 초안')
  expect(calls[2].conversation_id).toBe(calls[0].conversation_id)
  expect(calls.every(body => !('messages' in body))).toBe(true)
})

test('streams incrementally, stops locally, ignores late tokens and keeps the typed draft', async ({ page }) => {
  await page.addInitScript(() => {
    const original = window.fetch
    window.fetch = async (url, init) => {
      if (url !== '/api/chat') return original(url, init)
      const { request_id, conversation_id } = JSON.parse(String(init?.body))
      const encoder = new TextEncoder()
      return new Response(new ReadableStream({ start(controller) {
        const push = (type: string, extra = {}) => controller.enqueue(encoder.encode(`event: ${type}\ndata: ${JSON.stringify({ request_id, conversation_id, user_message_id: 'stream-user', message_id: 'stream-id', ...extra })}\n\n`))
        push('start'); push('delta', { text: '첫 번째 조각' })
        setTimeout(() => { try { push('delta', { text: ' 늦게 도착한 조각' }); push('done'); controller.close() } catch { /* cancelled reader */ } }, 1500)
      } }), { headers: { 'Content-Type': 'text/event-stream' } })
    }
  })
  await page.goto('/')
  const input = page.getByRole('textbox', { name: '메시지' })
  await input.fill('길게 설명해 줘')
  await input.press('Enter')
  await expect(page.getByText('첫 번째 조각', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '응답 중단' })).toBeVisible()
  await input.fill('이어 쓸 초안')
  await input.press('Enter')
  await expect(page.locator('.message.user')).toHaveCount(1)
  await page.getByRole('button', { name: '응답 중단' }).click()
  await expect(page.getByText('응답을 중단했습니다.', { exact: true })).toBeVisible()
  await expect(input).toHaveValue('이어 쓸 초안')
  await page.waitForTimeout(1600) // Deliberately exercise a late event from the old stream.
  await expect(page.getByText(/늦게 도착한 조각/)).not.toBeVisible()
  await expect(page.getByRole('button', { name: '다시 시도' })).toBeVisible()
})

test('reading older messages is not interrupted by new streaming text', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.addInitScript(() => {
    const original = window.fetch
    window.fetch = async (url, init) => {
      if (url !== '/api/chat') return original(url, init)
      const { request_id, conversation_id } = JSON.parse(String(init?.body))
      const encoder = new TextEncoder()
      return new Response(new ReadableStream({ start(controller) {
        const push = (event: string, extra = {}) => controller.enqueue(encoder.encode(`event: ${event}\ndata: ${JSON.stringify({ request_id, conversation_id, user_message_id: 'long-user', message_id: 'long-reply', ...extra })}\n\n`))
        push('start'); push('delta', { text: '확인할 내용입니다.\n'.repeat(80) })
        Object.assign(window, { appendTestReply: () => { push('delta', { text: '\n마지막으로 추가된 내용입니다.' }); push('done'); controller.close() } })
      } }), { headers: { 'Content-Type': 'text/event-stream' } })
    }
  })
  await page.goto('/')
  await page.getByRole('textbox', { name: '메시지' }).fill('길게 답해 줘')
  await page.getByRole('textbox', { name: '메시지' }).press('Enter')
  await expect(page.getByText('답변 작성 중', { exact: true })).toBeVisible()
  await page.locator('.conversation-scroll').evaluate(element => { element.scrollTop = 0 })
  await expect(page.getByRole('button', { name: '최신 메시지로' })).toBeVisible()
  await page.evaluate(() => (window as unknown as { appendTestReply: () => void }).appendTestReply())
  await expect(page.getByText('답변이 완료되었습니다.', { exact: true })).toBeAttached()
  expect(await page.locator('.conversation-scroll').evaluate(element => element.scrollTop)).toBeLessThan(10)
  await page.getByRole('button', { name: '최신 메시지로' }).click()
  await expect(page.getByRole('button', { name: '최신 메시지로' })).not.toBeVisible()
  const distance = await page.locator('.conversation-scroll').evaluate(element => element.scrollHeight - element.scrollTop - element.clientHeight)
  expect(distance).toBeLessThan(64)
})

test('configuration errors preserve the request and never auto-retry', async ({ page }) => {
  let calls = 0
  await page.route('**/api/chat', route => {
    calls++
    return route.fulfill({ status: 503, json: { error: { code: 'not_configured', message: 'private URL' } } })
  })
  await page.goto('/')
  await page.getByRole('textbox', { name: '메시지' }).fill('내 메시지')
  await page.getByRole('textbox', { name: '메시지' }).press('Enter')
  await expect(page.getByText('서버의 모델 설정을 확인해 주세요.', { exact: true })).toBeVisible()
  await expect(page.getByText('내 메시지', { exact: true })).toBeVisible()
  await expect(page.getByText('private URL', { exact: true })).not.toBeVisible()
  await expect(page.getByRole('button', { name: '다시 시도' })).not.toBeVisible()
  expect(calls).toBe(1)
})
