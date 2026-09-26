import { expect, test } from '@playwright/test'
import { mockConversations } from './fixtures'

test('setup state is honest and dialog restores keyboard focus', async ({ page }) => {
  await mockConversations(page)
  await page.route('**/api/integrations/google', r => r.fulfill({ json: { status: 'not_configured', email: null, calendar_read: false, gmail_read: false } }))
  await page.goto('/')
  await page.getByRole('button', { name: '앱 연결', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '앱 연결' })
  await expect(dialog.getByText('연결 설정이 필요해요', { exact: true })).toBeVisible()
  await expect(dialog.getByRole('button', { name: 'Google 계정 연결' })).toBeDisabled()
  await dialog.getByText('설정 방법 보기').click()
  await expect(dialog.getByText('GOOGLE_CLIENT_ID', { exact: true })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(dialog).not.toBeVisible()
  await expect(page.getByRole('button', { name: '앱 연결', exact: true })).toBeFocused()
})

test('partial grants, disconnect and failed provider revocation are distinct', async ({ page }) => {
  await mockConversations(page)
  let status = { status: 'connected', email: 'person@example.test', calendar_read: true, gmail_read: false }
  await page.route('**/api/integrations/google', r => r.fulfill({ json: status }))
  await page.route('**/api/integrations/google/disconnect', r => {
    status = { ...status, status: 'disconnected', email: '', calendar_read: false }
    return r.fulfill({ json: { status: 'disconnected', revoked: false } })
  })
  await page.goto('/?apps=google&result=connected')
  const dialog = page.getByRole('dialog', { name: '앱 연결' })
  await expect(dialog.getByText('person@example.test')).toBeVisible()
  await expect(dialog.getByText('Gmail 권한이 필요해요')).toBeVisible()
  await dialog.getByRole('button', { name: '연결 해제', exact: true }).click()
  await expect(dialog.getByText('이 서비스의 연결은 해제했어요. Google 계정에서도 접근 권한을 확인해 주세요.')).toBeVisible()
  await expect(dialog.getByText('person@example.test')).toHaveCount(0)
})

test('mobile error recovers and connect uses only explicit navigation', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await mockConversations(page)
  let reads = 0, starts = 0
  await page.route('**/api/integrations/google', r => {
    reads++
    return reads === 1 ? r.fulfill({ status: 503, json: { error: { code: 'integration_unavailable' } } }) : r.fulfill({ json: { status: 'disconnected', email: null, calendar_read: false, gmail_read: false } })
  })
  await page.route('**/api/integrations/google/connect', r => { starts++; return r.fulfill({ json: { url: 'https://accounts.google.com/o/oauth2/v2/auth?test=1' } }) })
  await page.route('https://accounts.google.com/**', r => r.fulfill({ contentType: 'text/html; charset=utf-8', body: '<p>합성 Google 로그인</p>' }))
  await page.goto('/')
  await page.getByRole('button', { name: '앱 연결', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '앱 연결' })
  await expect(dialog.getByText('연결 상태를 불러오지 못했어요.')).toBeVisible()
  await dialog.getByRole('button', { name: '다시 확인' }).click()
  await expect(dialog.getByText('아직 연결하지 않았어요')).toBeVisible()
  expect(starts).toBe(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  await page.screenshot({ path: '../docs/verification/step-09-apps-mobile.png', fullPage: true })
  await dialog.getByRole('button', { name: 'Google 계정 연결' }).click()
  await expect(page.getByText('합성 Google 로그인')).toBeVisible()
  expect(starts).toBe(1)
})
