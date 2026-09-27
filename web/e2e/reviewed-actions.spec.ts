import { expect, test } from '@playwright/test'
import { mockConversations } from './fixtures'
const cid = '00000000-0000-4000-8000-000000001201'
test('review exact email, approve once, and preserve uncertain result after reload', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const mock = await mockConversations(page)
  mock.conversations.set(cid, { id: cid, title: '메일 보내기', updated_at: new Date().toISOString(), messages: [
    { id: 'user-write', role: 'user', text: '확인 메일 작성해줘', status: 'completed', retryable: false },
  ] })
  let action = { id: 'review-1', source_user_message_id: 'user-write', kind: 'email', status: 'pending', version: 1,
    account_email: 'person@example.test', payload: { to: ['friend@example.test'], subject: '시간 확인', body: '오후 6시에 만나요. <script>실행 안 됨</script>' } }
  let sent = 0
  await page.route('**/api/conversations/*/actions', r => r.fulfill({ json: { items: [action] } }))
  await page.route('**/api/actions/*/approve', async r => {
    sent++; expect(r.request().postDataJSON()).toEqual({ expected_version: 1 })
    action = { ...action, status: 'unknown', version: 3 }
    await r.fulfill({ json: action })
  })
  await page.goto(`/?conversation=${cid}`)
  const card = page.getByRole('region', { name: '메일 발송 확인' })
  await expect(card.getByText('friend@example.test', { exact: true })).toBeVisible()
  await expect(card.getByText(action.payload.body, { exact: true })).toBeVisible()
  await expect(card.locator('script')).toHaveCount(0)
  expect(sent).toBe(0)
  await card.getByRole('button', { name: '확인하고 발송' }).click()
  await expect(card.getByText('실행 여부 확인 필요', { exact: true })).toBeVisible()
  await expect(card.getByRole('button', { name: '확인하고 발송' })).toHaveCount(0)
  await page.reload()
  await expect(card.getByText('실행 여부 확인 필요', { exact: true })).toBeVisible()
  expect(sent).toBe(1)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  await page.screenshot({ path: test.info().outputPath('step-12-reviewed-action-mobile.png'), fullPage: true })
})

test('calendar review shows Korean time and cancellation never executes', async ({ page }) => {
  const mock = await mockConversations(page)
  mock.conversations.set(cid, { id: cid, title: '일정 등록', updated_at: new Date().toISOString(), messages: [
    { id: 'user-event', role: 'user', text: '일정 만들어줘', status: 'completed', retryable: false },
  ] })
  let action = { id: 'event-1', source_user_message_id: 'user-event', kind: 'calendar_event', status: 'pending', version: 1,
    account_email: 'person@example.test', payload: { title: '저녁 약속', start: '2026-09-28T18:00:00+09:00', end: '2026-09-28T19:00:00+09:00', location: '서울', description: '시간을 확인하세요.' } }
  let approvals = 0, rejections = 0
  await page.route('**/api/conversations/*/actions', r => r.fulfill({ json: { items: [action] } }))
  await page.route('**/api/actions/*/approve', r => { approvals++; return r.fulfill({ status: 409, json: { error: { code: 'integration_permission_required' } } }) })
  await page.route('**/api/actions/*/reject', r => { rejections++; action = { ...action, version: 2, status: 'rejected' }; return r.fulfill({ json: action }) })
  await page.goto(`/?conversation=${cid}`)
  const card = page.getByRole('region', { name: '일정 등록 확인' })
  await expect(card.getByText('한국 시간')).toBeVisible()
  await expect(card.locator('time').first()).toHaveAttribute('datetime', action.payload.start)
  await expect(card.locator('time').first()).toContainText('오후 6:00')
  await card.getByRole('button', { name: '확인하고 등록' }).click()
  await expect(card.getByRole('alert')).toContainText('권한을 먼저 허용')
  await card.getByRole('button', { name: '취소', exact: true }).click()
  await expect(card.getByText('취소됨', { exact: true })).toBeVisible()
  await page.reload()
  await expect(card.getByText('취소됨', { exact: true })).toBeVisible()
  expect(approvals).toBe(1); expect(rejections).toBe(1)
})

test('new review card follows the latest message without taking focus', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const mock = await mockConversations(page)
  mock.conversations.set(cid, { id: cid, title: '확인 카드', updated_at: new Date().toISOString(), messages: Array.from({ length: 5 }, (_, i) => ({ id: `m${i}`, role: 'assistant', text: '앞서 확인한 내용입니다.\n'.repeat(8), status: 'completed', retryable: false })) })
  let show = false
  await page.route('**/api/conversations/*/actions', r => r.fulfill({ json: { items: show ? [{ id: 'new-action', source_user_message_id: 'm0', kind: 'email', status: 'pending', version: 1, account_email: 'person@example.test', payload: { to: ['friend@example.test'], subject: '확인', body: '새 요청' } }] : [] } }))
  await page.goto(`/?conversation=${cid}`)
  const input = page.getByRole('textbox', { name: '메시지' })
  await input.focus()
  await expect(page.locator('.message').last()).toBeInViewport()
  show = true
  await expect(page.getByRole('button', { name: '확인하고 발송' })).toBeInViewport({ ratio: 1, timeout: 6000 })
  await expect(input).toBeFocused()
})
