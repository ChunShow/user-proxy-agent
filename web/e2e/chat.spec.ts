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

test('call controls keep listening, call lifetime and goal outcome separate', async ({ page }) => {
  const mutations: string[] = []
  page.on('request', request => { if (request.method() !== 'GET') mutations.push(request.url()) })
  await page.goto('/?preview=call')
  const card = page.getByRole('article', { name: '예시 병원 통화' })
  await expect(page.getByText('예시 데이터 · 실제 전화가 연결되지 않습니다')).toBeVisible()
  await expect(card.getByText('통화 중', { exact: true })).toBeVisible()
  await card.getByRole('button', { name: '듣기', exact: true }).click()
  await expect(card.getByText('듣기 켜짐 (예시)', { exact: true })).toBeVisible()
  await card.getByRole('button', { name: '듣기 끄기', exact: true }).click()
  await expect(card.getByText('통화 중', { exact: true })).toBeVisible()
  await card.getByRole('button', { name: '통화 내역 보기' }).click()
  await expect(card.getByText('발화 중 · 예시')).toBeVisible()
  await card.getByRole('button', { name: '통화 내역 접기' }).click()
  await expect(card.getByText('발화 중 · 예시')).not.toBeVisible()
  await card.getByRole('button', { name: '통화 종료', exact: true }).click()
  await expect(card.getByText('종료 확인 중', { exact: true })).toBeVisible()
  await expect(card.getByText('통화 종료됨', { exact: true })).not.toBeVisible()
  await expect(card.getByText('목표 달성', { exact: true })).not.toBeVisible()
  await page.getByRole('button', { name: '종료 확인 (예시)' }).click()
  await expect(card.getByText('통화 종료됨', { exact: true })).toBeVisible()
  await expect(card.getByText('미완료', { exact: true })).toBeVisible()
  await expect(card.getByRole('button', { name: '추가 지시' })).toBeDisabled()
  expect(mutations).toEqual([])
})

test('steering targets the call, clears after submission, and keeps normal chat usable', async ({ page }) => {
  await page.goto('/?preview=call')
  const input = page.getByRole('textbox', { name: '메시지' })
  await page.getByRole('button', { name: '추가 지시' }).click()
  await expect(input).toBeFocused()
  await expect(page.getByText('예시 병원에 추가 지시 · 예시')).toBeVisible()
  await input.fill('점심시간도 확인해줘')
  await input.press('Enter')
  await expect(page.getByText('지시 예시 · 예시 병원\n점심시간도 확인해줘')).toBeVisible()
  await expect(page.getByRole('button', { name: '지시 대상 해제' })).not.toBeVisible()
  await input.fill('이건 일반 메시지야')
  await input.press('Enter')
  await expect(page.getByText('이건 일반 메시지야', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '추가 지시' }).click()
  await page.getByRole('button', { name: '지시 대상 해제' }).click()
  await expect(page.getByRole('button', { name: '지시 대상 해제' })).not.toBeVisible()
})

test('preview navigation preserves drafts and six states have honest controls', async ({ page }) => {
  await page.goto('/')
  const input = page.getByRole('textbox', { name: '메시지' })
  await input.fill('작성 중인 내용')
  await page.getByRole('button', { name: '화면 예시', exact: true }).click()
  await expect(page).toHaveURL(/preview=call/)
  await expect(input).toHaveValue('작성 중인 내용')
  const state = page.getByRole('combobox', { name: '통화 예시 상태' })
  const card = page.getByRole('article', { name: '예시 병원 통화' })
  for (const [option, call, goal, active] of [
    ['dialing', '연결 중', '확인 중', false],
    ['connected', '통화 중', '확인 중', true],
    ['needs_input', '통화 중', '추가 확인 필요', true],
    ['succeeded', '통화 종료됨', '목표 달성', false],
    ['incomplete', '통화 종료됨', '미완료', false],
    ['failed', '연결 실패', '미완료', false],
  ] as const) {
    await state.selectOption(option)
    await expect(card.getByText(call, { exact: true })).toBeVisible()
    await expect(card.getByText(goal, { exact: true })).toBeVisible()
    await expect(card.getByRole('button', { name: '듣기', exact: true })).toBeEnabled({ enabled: active })
  }
  await page.getByRole('button', { name: '메인 대화', exact: true }).click()
  await expect(input).toHaveValue('작성 중인 내용')
  await page.getByRole('button', { name: '화면 예시', exact: true }).click()
  await page.reload()
  await expect(input).toHaveValue('')
  await expect(state).toHaveValue('connected')
})
