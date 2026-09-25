import { expect, test } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.route('**/api/health', route => route.fulfill({
    json: { status: 'ok', service: 'agent-service' },
  }))
})

test('adds local messages, rejects blanks, preserves multiline input and focus', async ({ page }) => {
  await page.goto('/')
  const input = page.getByRole('textbox', { name: '메시지' })
  const add = page.getByRole('button', { name: '메시지 추가', exact: true })
  await input.fill('   ')
  await expect(add).toBeDisabled()
  await input.press('Enter')
  await expect(page.getByRole('listitem')).toHaveCount(0)
  await input.fill('내일')
  await input.press('Shift+Enter')
  await input.pressSequentially('일정')
  await expect(input).toHaveValue('내일\n일정')
  await input.press('Enter')
  await expect(page.getByRole('listitem')).toHaveCount(1)
  await expect(page.getByRole('listitem')).toHaveText(/내일\s+일정/)
  await expect(input).toHaveValue('')
  await expect(input).toBeFocused()
  await page.reload()
  await expect(page.getByRole('listitem')).toHaveCount(0)
})

test('does not submit Korean text while IME composition is active', async ({ page }) => {
  await page.goto('/')
  const input = page.getByRole('textbox', { name: '메시지' })
  await input.fill('병원')
  await input.dispatchEvent('compositionstart')
  await input.dispatchEvent('keydown', { key: 'Enter', isComposing: true, keyCode: 229 })
  await expect(page.getByRole('listitem')).toHaveCount(0)
  await expect(input).toHaveValue('병원')
  await input.dispatchEvent('compositionend')
  await input.press('Enter')
  await expect(page.getByRole('listitem')).toHaveCount(1)
})

test('health failure and retry do not discard a draft', async ({ page }) => {
  await page.route('**/api/health', route => route.fulfill({ status: 503, body: 'unavailable' }))
  await page.goto('/')
  await page.getByRole('textbox', { name: '메시지' }).fill('나중에 보낼 내용')
  await expect(page.getByText('서버 연결 실패', { exact: true })).toBeVisible()
  await page.route('**/api/health', route => route.fulfill({
    json: { status: 'ok', service: 'agent-service' },
  }))
  await page.getByRole('button', { name: '서버 연결 다시 확인' }).click()
  await expect(page.getByText('서버 연결됨', { exact: true })).toBeVisible()
  await expect(page.getByRole('textbox', { name: '메시지' })).toHaveValue('나중에 보낼 내용')
})

test('mobile menu supports Escape and restores focus', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/')
  const menu = page.getByRole('button', { name: '메뉴 열기', exact: true })
  await menu.click()
  await expect(page.getByRole('dialog', { name: '탐색 메뉴' })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).not.toBeVisible()
  await expect(menu).toBeFocused()
})
