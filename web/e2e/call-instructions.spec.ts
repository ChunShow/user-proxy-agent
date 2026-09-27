import { expect, test, type Page } from '@playwright/test'
import type { PhoneCall } from '../src/calls/calls'
import { mockConversations } from './fixtures'

const cid = '00000000-0000-4000-8000-000000000801'
const other = '00000000-0000-4000-8000-000000000802'
async function fixture(page: Page) {
  const mock = await mockConversations(page)
  mock.conversations.set(cid, { id: cid, title: '일정 문의', updated_at: new Date().toISOString(), messages: [
    { id: 'user-call', role: 'user', text: '테스트 번호로 일정 문의해 줘', status: 'completed', retryable: false },
    { id: 'assistant-call', role: 'assistant', text: '통화 중이에요.', status: 'completed', retryable: false },
  ] })
  mock.conversations.set(other, { id: other, title: '다른 대화', updated_at: new Date().toISOString(), messages: [] })
  let call: PhoneCall = { id: '00000000-0000-4000-8000-000000000803', conversation_id: cid, source_user_message_id: 'user-call',
    destination: '01000000001', subject: '일정 문의', purpose: '가능한 시간 확인하기', status: 'connected', outcome: 'pending',
    reported_summary: '', error_code: null, stop_requested: false, version: 1, instructions: [] }
  let stops = 0
  await page.route('**/api/conversations/*/calls*', route => route.fulfill({ json: { items: route.request().url().includes(cid) ? [call] : [], next_cursor: null } }))
  await page.route('**/api/calls/**', route => {
    const url = route.request().url()
    if (url.includes('/activity')) return route.fulfill({ json: { events: [], questions: [], next_after: 0, has_more: false, terminal: false } })
    if (url.endsWith('/active')) return route.fulfill({ json: { items: [call] } })
    if (url.endsWith('/stop')) { stops++; call = { ...call, status: 'ending', stop_requested: true, version: call.version + 1 } }
    return route.fulfill({ json: call })
  })
  return { mock, get: () => call, set: (patch: Partial<PhoneCall>) => { call = { ...call, ...patch, version: call.version + 1 } }, stops: () => stops }
}

test('instructions progress honestly, survive reload and remain in their conversation', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 1000 })
  const f = await fixture(page)
  await page.goto(`/?conversation=${cid}`)
  const card = page.getByRole('region', { name: '일정 문의 통화' })
  const input = page.getByRole('textbox', { name: '메시지' })
  await input.fill('안녕'); await input.press('Enter')
  await expect(page.getByText('테스트 응답입니다.', { exact: true })).toBeVisible()
  await expect(card.getByText('통화에 추가한 요청')).toHaveCount(0)
  const instruction = { id: 'instruction-1', text: '오후 2시는 어렵고, 오후 6시로 확인해 주세요.', status: 'pending' as const, error_code: null }
  f.set({ instructions: [instruction] })
  await expect(card.getByText('전달 대기 중', { exact: true })).toBeVisible()
  f.set({ instructions: [{ ...instruction, status: 'sending' }] })
  await expect(card.getByText('전달 중', { exact: true })).toBeVisible()
  await expect(card.getByText('통화 도우미에게 전달됨', { exact: true })).toHaveCount(0)
  f.set({ instructions: [{ ...instruction, status: 'delivered' }] })
  await expect(card.getByText('통화 도우미에게 전달됨', { exact: true })).toBeVisible()
  await page.reload()
  await expect(card.getByText(instruction.text)).toBeVisible()
  await expect(card.getByText('통화 도우미에게 전달됨', { exact: true })).toBeVisible()
  await card.getByRole('heading', { name: '일정 문의' }).scrollIntoViewIfNeeded()
  await page.screenshot({ path: test.info().outputPath('step-08-instructions-desktop.png'), fullPage: true })
  await page.getByRole('button', { name: '다른 대화', exact: true }).click()
  await expect(page.getByText(instruction.text)).toHaveCount(0)
  await page.getByRole('button', { name: '대화로 이동' }).click()
  await expect(card.getByText(instruction.text)).toBeVisible()
  // An older poll cannot replace the confirmed state.
  await page.route('**/api/calls/active', route => route.fulfill({ json: { items: [{ ...f.get(), version: 1, instructions: [{ ...instruction, status: 'pending' }] }] } }))
  await page.waitForResponse('**/api/calls/active')
  await expect(card.getByText('통화 도우미에게 전달됨', { exact: true })).toBeVisible()
})

test('uncertain delivery stays explicit on mobile and direct stop remains usable', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const f = await fixture(page)
  const text = '<script>실행하지 않는 요청 텍스트</script> ' + '일정과 장소를 다시 확인해 주세요. '.repeat(12)
  f.set({ instructions: [
    { id: 'one', text, status: 'delivery_unknown', error_code: 'service_restarted' },
    { id: 'two', text: '새 요청입니다.', status: 'not_applied', error_code: 'call_ended' },
  ] })
  await page.goto(`/?conversation=${cid}`)
  const card = page.getByRole('region', { name: '일정 문의 통화' })
  await expect(card.getByText('전달 여부 확인 필요', { exact: true })).toBeVisible()
  await expect(card.getByText('전달하지 못함', { exact: true })).toBeVisible()
  await expect(card.locator('script')).toHaveCount(0)
  await expect(card.getByText('통화 도우미에게 전달됨', { exact: true })).toHaveCount(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  await page.reload()
  await expect(card.getByText('전달 여부 확인 필요', { exact: true })).toBeVisible()
  await card.getByRole('button', { name: '통화 종료', exact: true }).scrollIntoViewIfNeeded()
  await page.screenshot({ path: test.info().outputPath('step-08-instructions-mobile.png'), fullPage: true })
  await card.getByRole('button', { name: '통화 종료', exact: true }).click()
  await expect(card.getByText('종료 확인 중', { exact: true })).toBeVisible()
  expect(f.stops()).toBe(1)
})
