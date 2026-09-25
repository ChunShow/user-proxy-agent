import { expect, test } from '@playwright/test'
import { mockConversations } from './fixtures'

test('restores two conversations after reload and keeps drafts separate', async ({ page }) => {
  const mock = await mockConversations(page)
  await page.goto('/')
  const input = page.getByRole('textbox', { name: '메시지' })
  await input.fill('첫 대화의 기억')
  await input.press('Enter')
  await expect(page.getByText('테스트 응답입니다.', { exact: true })).toBeVisible()
  const firstUrl = page.url()
  await input.fill('첫 대화 초안')
  await page.getByRole('button', { name: '새 대화', exact: true }).click()
  await expect(input).toHaveValue('')
  await input.fill('둘째 대화')
  await input.press('Enter')
  await expect(page.getByText('테스트 응답입니다.', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '첫 대화의 기억', exact: true }).click()
  await expect(input).toHaveValue('첫 대화 초안')
  await expect(page.locator('.message.user')).toHaveText(/첫 대화의 기억/)
  await page.reload()
  await expect(page.locator('.message.user')).toHaveText(/첫 대화의 기억/)
  expect(page.url()).toBe(firstUrl)
  expect(mock.calls.length).toBe(2)
  expect(mock.calls.every(body => !('messages' in body))).toBe(true)
  await page.goBack()
  await expect(page.locator('.message.user')).toHaveText(/둘째 대화/)
})

test('missing conversation and expired session do not become a blank writable chat', async ({ page }) => {
  await mockConversations(page)
  await page.goto('/?conversation=00000000-0000-4000-8000-000000000099')
  await expect(page.getByText('대화를 찾을 수 없습니다.', { exact: true })).toBeVisible()
  const input = page.getByRole('textbox', { name: '메시지' })
  await input.fill('보내지 않을 입력')
  await expect(page.getByRole('button', { name: '메시지 추가' })).toBeDisabled()
  await page.getByRole('button', { name: '새 대화', exact: true }).click()
  await page.route('**/api/chat', route => route.fulfill({ status: 401, json: { error: { code: 'session_expired' } } }))
  await input.fill('만료 확인')
  await input.press('Enter')
  await expect(page.getByText('세션이 만료되었습니다. 화면을 새로 불러와 주세요.', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '다시 시도', exact: true })).not.toBeVisible()
})

test('mobile previous conversation selection closes menu and restores focus', async ({ page }) => {
  await mockConversations(page)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/')
  const input = page.getByRole('textbox', { name: '메시지' })
  await input.fill('모바일 대화')
  await input.press('Enter')
  await expect(page.getByText('테스트 응답입니다.', { exact: true })).toBeVisible()
  const menu = page.getByRole('button', { name: '메뉴 열기' })
  await menu.click()
  await page.getByRole('dialog').getByRole('button', { name: '새 대화', exact: true }).click()
  await menu.click()
  await page.getByRole('dialog').getByRole('button', { name: '모바일 대화', exact: true }).click()
  await expect(page.getByRole('dialog')).not.toBeVisible()
  await expect(menu).toBeFocused()
  await expect(page.locator('.message.user')).toHaveText(/모바일 대화/)
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false)
})

test('failed initial session can recover through the displayed reload action', async ({ page }) => {
  await mockConversations(page)
  let failed = true
  await page.route('**/api/session', route => failed ? route.fulfill({ status: 503, json: { error: { code: 'load_failed' } } }) : route.fulfill({ status: 204 }))
  await page.goto('/')
  await expect(page.getByText('대화를 불러오지 못했습니다. 다시 불러와 주세요.', { exact: true })).toBeVisible()
  failed = false
  await page.getByRole('button', { name: '다시 불러오기', exact: true }).click()
  await page.getByRole('textbox', { name: '메시지' }).fill('복구 후 전송')
  await expect(page.getByRole('button', { name: '메시지 추가' })).toBeEnabled()
  await page.getByRole('textbox', { name: '메시지' }).press('Enter')
  await expect(page.getByText('테스트 응답입니다.', { exact: true })).toBeVisible()
})

test('late conversation loads cannot replace the newly selected conversation', async ({ page }) => {
  const mock = await mockConversations(page)
  const a = '00000000-0000-4000-8000-000000000001', b = '00000000-0000-4000-8000-000000000002'
  for (const [id, text] of [[a, '첫 저장 내용'], [b, '둘째 저장 내용']]) mock.conversations.set(id, {
    id, title: text, updated_at: new Date().toISOString(), messages: [{ id: `m-${id}`, role: 'user', text, status: 'completed', retryable: false }],
  })
  await page.goto(`/?conversation=${b}`)
  await expect(page.locator('.message.user')).toHaveText(/둘째 저장 내용/)
  let release: () => void = () => {}
  const gate = new Promise<void>(resolve => { release = resolve })
  let started = false
  await page.route(`**/api/conversations/${a}`, async route => {
    started = true
    await gate
    await route.fulfill({ json: { conversation: mock.conversations.get(a), messages: mock.conversations.get(a)!.messages, next_cursor: null } })
  })
  await page.getByRole('button', { name: '첫 저장 내용', exact: true }).click()
  await expect.poll(() => started).toBe(true)
  await page.getByRole('button', { name: '둘째 저장 내용', exact: true }).click()
  await expect(page.locator('.message.user')).toHaveText(/둘째 저장 내용/)
  const lateResponse = page.waitForResponse(response => response.url().endsWith(a))
  release()
  await lateResponse
  await page.evaluate(() => new Promise(requestAnimationFrame))
  await expect(page.getByText('대화를 불러오고 있습니다.', { exact: true })).not.toBeVisible()
  await expect(page.locator('.message.user')).toHaveText(/둘째 저장 내용/)
})

test('older message pagination preserves reading position and deduplicates the boundary', async ({ page }) => {
  const mock = await mockConversations(page)
  const id = '00000000-0000-4000-8000-000000000003'
  const messages = Array.from({length: 70}, (_, i) => ({id: `message-${i}`,role:'user',text:`저장 메시지 ${i}`,status:'completed',retryable:false}))
  const conversation = {id,title:'긴 대화',updated_at:new Date().toISOString(),messages}
  mock.conversations.set(id,conversation)
  await page.route(`**/api/conversations/${id}**`, route => {
    const before = new URL(route.request().url()).searchParams.get('before')
    return route.fulfill({json:{conversation,messages: before ? messages.slice(0,21) : messages.slice(20),next_cursor:before ? null : '21'}})
  })
  await page.goto(`/?conversation=${id}`)
  await expect(page.locator('.message.user')).toHaveCount(50)
  await page.locator('.conversation-scroll').evaluate(element=>{element.scrollTop=0})
  const before = await page.getByText('저장 메시지 20',{exact:true}).boundingBox()
  await page.getByRole('button',{name:'이전 메시지 더 보기',exact:true}).click()
  await expect(page.locator('.message.user')).toHaveCount(70)
  await expect(page.getByRole('button',{name:'이전 메시지 더 보기',exact:true})).not.toBeVisible()
  await expect.poll(async()=>Math.abs((await page.getByText('저장 메시지 20',{exact:true}).boundingBox())!.y-before!.y)).toBeLessThan(3)
})
