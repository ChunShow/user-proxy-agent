import { expect, test } from '@playwright/test'
import { mockConversations } from './fixtures'
const cid = '00000000-0000-4000-8000-000000000701'
const callId = '00000000-0000-4000-8000-000000000702'
test('explicit listening plays synthetic audio, releases resources, and leaves call active', async ({ page }) => {
  const errors: string[] = []
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
  const mock = await mockConversations(page)
  mock.conversations.set(cid, { id: cid, title: '듣기 시험', updated_at: new Date().toISOString(), messages: [{ id: 'user-audio', role: 'user', text: '합성 통화', status: 'completed', retryable: false }] })
  const call = { id: callId, conversation_id: cid, source_user_message_id: 'user-audio', destination: '01000000001', subject: '듣기 시험', purpose: '합성 음성 전달', status: 'connected', outcome: 'pending', reported_summary: '', error_code: null, stop_requested: false, version: 1 }
  await page.route('**/api/conversations/*/calls*', r => r.fulfill({ json: { items: r.request().url().includes(cid) ? [call] : [], next_cursor: null } }))
  await page.route('**/api/conversations/*/actions', r => r.fulfill({ json: { items: [] } }))
  await page.route('**/api/calls/**', r => r.fulfill({ json: r.request().url().includes('/activity') ? { events: [], questions: [], next_after: 0, terminal: false } : { items: [call] } }))
  let opened = 0, closed = 0
  await page.routeWebSocket(`**/api/calls/${callId}/listen`, ws => {
    opened++
    ws.onClose(() => closed++)
    ws.send(JSON.stringify({ type: 'ready', encoding: 'mulaw', sample_rate: 8000 }))
    ws.send(JSON.stringify({ type: 'audio', track: 'caller', payload: Buffer.alloc(1600, 170).toString('base64') }))
    ws.send(JSON.stringify({ type: 'audio', track: 'assistant', payload: Buffer.alloc(1600, 170).toString('base64') }))
  })
  await page.addInitScript(() => {
    const start = AudioBufferSourceNode.prototype.start
    AudioBufferSourceNode.prototype.start = function (...args) {
      document.documentElement.dataset.audioPlays = String(Number(document.documentElement.dataset.audioPlays ?? 0) + 1)
      return start.apply(this, args)
    }
  })
  await page.goto(`/?conversation=${cid}`)
  await expect(page.getByRole('button', { name: '통화 듣기', exact: true })).toBeVisible()
  expect(opened).toBe(0)
  await page.getByRole('button', { name: '통화 듣기', exact: true }).click()
  await expect(page.getByText('통화 소리를 듣고 있어요 · 마이크 꺼짐')).toBeVisible()
  await expect(page.locator('html')).toHaveAttribute('data-audio-plays', '2')
  await page.getByRole('button', { name: '듣기 중지', exact: true }).click()
  await expect.poll(() => closed).toBe(1)
  await expect(page.getByRole('button', { name: '통화 종료', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: '통화 듣기', exact: true }).click()
  await expect.poll(() => opened).toBe(2)
  await page.getByRole('button', { name: '새 대화', exact: true }).click()
  await expect.poll(() => closed).toBe(2)
  expect(errors.filter(text => text.includes('same key'))).toEqual([])
})
