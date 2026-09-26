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
  await page.screenshot({ path: '../docs/verification/step-12-reviewed-action-mobile.png', fullPage: true })
})
