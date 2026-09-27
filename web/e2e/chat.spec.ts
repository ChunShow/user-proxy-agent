import { expect, test } from '@playwright/test'
import { mockConversations } from './fixtures'

test.beforeEach(async ({ page }) => { await mockConversations(page) })

test('adds local messages, rejects blanks, preserves multiline input and focus', async ({ page }) => {
  await page.goto('/')
  const input = page.getByRole('textbox', { name: '메시지' })
  const add = page.getByRole('button', { name: '메시지 추가', exact: true })
  await input.fill('   ')
  await expect(add).toBeDisabled()
  await input.press('Enter')
  await expect(page.locator('.message.user')).toHaveCount(0)
  await input.fill('내일')
  await input.press('Shift+Enter')
  await input.pressSequentially('일정')
  await expect(input).toHaveValue('내일\n일정')
  await input.press('Enter')
  await expect(page.locator('.message.user')).toHaveCount(1)
  await expect(page.locator('.message.user')).toHaveText(/내일\s+일정/)
  await expect(input).toHaveValue('')
  await expect(input).toBeFocused()
  await expect(page.getByText('테스트 응답입니다.', { exact: true })).toBeVisible()
  await page.reload()
  await expect(page.locator('.message.user')).toHaveCount(1)
})

test('does not submit Korean text while IME composition is active', async ({ page }) => {
  await page.goto('/')
  const input = page.getByRole('textbox', { name: '메시지' })
  await input.fill('병원')
  await input.dispatchEvent('compositionstart')
  await input.dispatchEvent('keydown', { key: 'Enter', isComposing: true, keyCode: 229 })
  await expect(page.locator('.message.user')).toHaveCount(0)
  await expect(input).toHaveValue('병원')
  await input.dispatchEvent('compositionend')
  await input.press('Enter')
  await expect(page.locator('.message.user')).toHaveCount(1)
})

for (const [width, height] of [[1440, 900], [390, 844], [320, 420]]) {
  test(`starting a conversation keeps input close and preserves focus at ${width}x${height}`, async ({ page }) => {
    await page.setViewportSize({ width, height })
    await page.goto('/')
    const input = page.getByRole('textbox', { name: '메시지' })
    await expect(input).toBeInViewport({ ratio: 1 })
    const gap = await page.evaluate(() => {
      const intro = document.querySelector('.empty-chat')!.getBoundingClientRect()
      const composer = document.querySelector('.composer')!.getBoundingClientRect()
      return composer.top - intro.bottom
    })
    expect(gap).toBeGreaterThanOrEqual(0)
    expect(gap).toBeLessThanOrEqual(56)
    await input.fill('병원 진료 시간을 확인해 줘')
    await input.press('Enter')
    await expect(input).toBeFocused()
    await expect(input).toBeInViewport({ ratio: 1 })
    await expect(page.locator('.message.user').getByText('병원 진료 시간을 확인해 줘', { exact: true })).toBeInViewport({ ratio: 1 })
    const bottomGap = await input.evaluate(element => innerHeight - element.getBoundingClientRect().bottom)
    expect(bottomGap).toBeLessThan(120)
  })
}

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

for (const [width, height] of [[1440, 900], [768, 1024], [390, 844], [320, 568]]) {
  test(`layout stays usable at ${width}x${height}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height })
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    const apiRequests: string[] = []
    page.on('request', request => {
      if (['fetch', 'xhr'].includes(request.resourceType())) apiRequests.push(new URL(request.url()).pathname)
    })
    await page.goto('/')
    await page.screenshot({ path: testInfo.outputPath('empty.png') })
    const input = page.getByRole('textbox', { name: '메시지' })
    await input.fill('긴 한국어 문장입니다. '.repeat(20) + 'https://example.test/' + 'longsegment'.repeat(50))
    await input.press('Enter')
    await expect(page.locator('.message.user')).toHaveCount(1)
    const geometry = await page.evaluate(() => {
      const message = document.querySelector('.message-bubble')!.getBoundingClientRect()
      const composer = document.querySelector('.composer')!.getBoundingClientRect()
      return { overflow: document.documentElement.scrollWidth > innerWidth, bottom: message.bottom, composerTop: composer.top }
    })
    expect(geometry.overflow).toBe(false)
    expect(geometry.bottom).toBeLessThanOrEqual(geometry.composerTop)
    await page.screenshot({ path: testInfo.outputPath('long-message.png') })
    const undersized = await page.locator('button, select').evaluateAll(elements => elements.filter(element => {
      const bounds = element.getBoundingClientRect()
      return bounds.width > 0 && bounds.height > 0 && (bounds.width < 44 || bounds.height < 44)
    }).map(element => element.getAttribute('aria-label') || element.textContent))
    expect(undersized).toEqual([])
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false)
    expect(errors).toEqual([])
    expect(apiRequests.length).toBeGreaterThan(0)
    expect(apiRequests.every(path => ['/api/health', '/api/chat', '/api/session', '/api/conversations', '/api/calls/active'].includes(path) || path.startsWith('/api/conversations/'))).toBe(true)
  })
}

test('legacy preview URLs show the normal chat without examples', async ({ page }) => {
  await page.goto('/?preview=call')
  await expect(page.getByRole('button', { name: '화면 예시', exact: true })).toHaveCount(0)
  await expect(page.getByRole('combobox', { name: '통화 예시 상태' })).toHaveCount(0)
  await expect(page.getByRole('textbox', { name: '메시지' })).toBeVisible()
})

test('conversation names can be edited and deleted without a trash screen', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('textbox', { name: '메시지' }).fill('대화 관리 검증')
  await page.getByRole('button', { name: '메시지 추가', exact: true }).click()
  await expect(page.getByText('테스트 응답입니다.', { exact: true })).toBeVisible()
  const sidebar = page.locator('.sidebar')
  await sidebar.locator('summary').first().click()
  await sidebar.getByRole('button', { name: '이름 변경', exact: true }).click()
  await page.getByRole('textbox', { name: '대화 이름', exact: true }).fill('새 제목')
  await page.getByRole('button', { name: '저장', exact: true }).click()
  await expect(sidebar.getByRole('button', { name: '새 제목', exact: true })).toBeVisible()
  await page.reload()
  await expect(sidebar.getByRole('button', { name: '새 제목', exact: true })).toBeVisible()
  await sidebar.locator('summary').first().click()
  await sidebar.getByRole('button', { name: '삭제', exact: true }).click()
  await page.getByRole('dialog', { name: '대화 삭제', exact: true }).getByRole('button', { name: '삭제', exact: true }).click()
  await expect(sidebar.getByRole('button', { name: '새 제목', exact: true })).toHaveCount(0)
  await expect(sidebar.getByRole('button', { name: '삭제한 대화', exact: true })).toHaveCount(0)
  await page.reload()
  await expect(sidebar.getByRole('button', { name: '새 제목', exact: true })).toHaveCount(0)
})
