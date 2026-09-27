import { expect, test, type Page } from '@playwright/test'
import { mockConversations } from './fixtures'

const cid = '00000000-0000-4000-8000-000000000501'
const other = '00000000-0000-4000-8000-000000000502'
async function phoneFixture(page: Page, status = 'connected') {
  const mock = await mockConversations(page)
  mock.conversations.set(cid, { id: cid, title: '통화 테스트', updated_at: new Date().toISOString(), messages: [
    { id: 'user-call', role: 'user', text: '테스트 번호로 전화해 줘', status: 'completed', retryable: false },
    { id: 'assistant-call', role: 'assistant', text: '전화를 연결하고 있습니다.', status: 'completed', retryable: false },
  ] })
  mock.conversations.set(other, { id: other, title: '다른 대화', updated_at: new Date().toISOString(), messages: [] })
  let call = { id: '00000000-0000-4000-8000-000000000503', conversation_id: cid, source_user_message_id: 'user-call',
    destination: '01000000001', subject: '통화 기능 테스트', purpose: '지금 통화가 가능한지 확인하기',
    status, end_report: '{}', outcome: 'pending', reported_summary: '', error_code: null as string | null, stop_requested: false, version: 1 }
  let stops = 0
  await page.route('**/api/conversations/*/calls*', route => route.fulfill({ json: { items: route.request().url().includes(cid) ? [call] : [], next_cursor: null } }))
  await page.route('**/api/calls/**', route => {
    const url = route.request().url()
    if (url.includes('/activity')) return route.fulfill({ json: { events: [], questions: [],
      next_after: Number(new URL(url).searchParams.get('after')), has_more: false,
      terminal: ['ended', 'failed', 'canceled'].includes(call.status) } })
    if (url.endsWith('/active')) return route.fulfill({ json: { items: ['ended', 'failed', 'canceled'].includes(call.status) ? [] : [call] } })
    if (url.endsWith('/stop')) { stops++; call = { ...call, stop_requested: true, status: 'ending', version: call.version + 1 } }
    return route.fulfill({ json: call })
  })
  return { mock, get: () => call, set: (patch: Partial<typeof call>) => { call = { ...call, ...patch, version: call.version + 1 } }, stops: () => stops }
}

test('real call card restores, allows chat, and ends only after confirmation', async ({ page }) => {
  const fixture = await phoneFixture(page)
  await page.goto(`/?conversation=${cid}`)
  const card = page.getByRole('region', { name: '통화 기능 테스트 통화' })
  await expect(card.getByText('통화 중', { exact: true })).toBeVisible()
  await expect(card.getByRole('button', { name: '통화 종료', exact: true })).toBeVisible()
  await expect(card.getByRole('button', { name: '통화 듣기' })).toBeVisible()
  const input = page.getByRole('textbox', { name: '메시지' })
  await input.fill('전화하는 동안 질문할게'); await input.press('Enter')
  await expect(page.getByText('테스트 응답입니다.', { exact: true })).toBeVisible()
  await page.reload(); await expect(card).toBeVisible()
  await card.getByRole('button', { name: '통화 종료', exact: true }).dblclick()
  await expect(card.getByText('종료 확인 중', { exact: true })).toBeVisible()
  expect(fixture.stops()).toBe(1)
  fixture.set({ status: 'ended', outcome: 'canceled' })
  await expect(card.getByText('종료됨', { exact: true })).toBeVisible()
  expect(fixture.mock.calls).toHaveLength(1)
})

test('other conversation retains a direct stop control and link to ongoing call', async ({ page }) => {
  const fixture = await phoneFixture(page)
  await page.goto(`/?conversation=${other}`)
  const active = page.getByRole('region', { name: '진행 중인 통화' })
  await expect(active.getByText('통화 기능 테스트')).toBeVisible()
  await active.getByRole('button', { name: '대화로 이동' }).click()
  await expect(page.getByRole('region', { name: '통화 기능 테스트 통화' })).toBeVisible()
  await page.getByRole('button', { name: '다른 대화', exact: true }).click()
  await active.getByRole('button', { name: '통화 종료', exact: true }).click()
  await expect(active.getByText('종료 확인 중', { exact: true })).toBeVisible()
  expect(fixture.stops()).toBe(1)
})

test('uncertain calls have no redial or false completion; errors can be refreshed', async ({ page }) => {
  const fixture = await phoneFixture(page, 'unknown')
  fixture.set({ error_code: 'call_delivery_unknown' })
  await page.goto(`/?conversation=${cid}`)
  const card = page.getByRole('region', { name: '통화 기능 테스트 통화' })
  await expect(card.getByText('연결 확인 필요', { exact: true })).toBeVisible()
  await expect(card.getByText(/ClawOps에서/)).toBeVisible()
  await expect(card.getByRole('button', { name: '통화 종료', exact: true })).toHaveCount(0)
  await card.getByRole('button', { name: '다시 확인' }).click()
  await expect(card.getByText('연결 확인 필요', { exact: true })).toBeVisible()
  expect(fixture.stops()).toBe(0)
})

test('model reported result restores on mobile and does not masquerade as transcript', async ({ page }) => {
  const fixture = await phoneFixture(page, 'ended')
  fixture.set({ outcome: 'model_reported_success', reported_summary: '통화 테스트가 가능하다고 재확인했습니다.' })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(`/?conversation=${cid}`)
  const card = page.getByRole('region', { name: '통화 기능 테스트 통화' })
  await expect(card.getByText('통화 테스트가 가능하다고 재확인했습니다.')).toBeVisible()
  await expect(card.getByText('종료 요청 시점의 요약')).toBeVisible()
  await page.reload(); await expect(card).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false)
})

test('late polling response cannot undo stop, and mobile controls remain reachable', async ({ page }) => {
  const fixture = await phoneFixture(page)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(`/?conversation=${cid}`)
  const card = page.getByRole('region', { name: '통화 기능 테스트 통화' })
  await expect(card).toBeVisible()
  const old = fixture.get()
  let release: () => void = () => {}
  let waiting = false
  const gate = new Promise<void>(resolve => { release = resolve })
  await page.route(`**/api/conversations/${cid}/calls`, async route => {
    waiting = true; await gate
    await route.fulfill({ json: { items: [old], next_cursor: null } })
  })
  await expect.poll(() => waiting).toBe(true)
  const stop = card.getByRole('button', { name: '통화 종료', exact: true })
  expect((await stop.boundingBox())!.height).toBeGreaterThanOrEqual(44)
  await stop.focus(); await page.keyboard.press('Enter')
  await expect(card.getByText('종료 확인 중', { exact: true })).toBeVisible()
  const late = page.waitForResponse(r => r.url().endsWith(`${cid}/calls`))
  release(); await late
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => resolve())))
  await expect(card.getByText('종료 확인 중', { exact: true })).toBeVisible()
  expect(fixture.stops()).toBe(1)
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false)
})

test('preparation, dialing and failure states render without claiming completion', async ({ page }) => {
  const fixture = await phoneFixture(page, 'preparing')
  await page.goto(`/?conversation=${cid}`)
  const card = page.getByRole('region', { name: '통화 기능 테스트 통화' })
  await expect(card.getByText('통화 준비 중', { exact: true })).toBeVisible()
  fixture.set({ status: 'dialing' })
  await expect(card.getByText('연결 중', { exact: true })).toBeVisible()
  fixture.set({ status: 'failed', outcome: 'incomplete' })
  await expect(card.getByText('연결하지 못함', { exact: true })).toBeVisible()
  await expect(card.getByRole('button', { name: '통화 종료' })).toHaveCount(0)
  await expect(card.getByText('종료됨', { exact: true })).toHaveCount(0)
})

for (const [error, message] of [
  ['call_no_answer', '상대가 전화를 받지 않았습니다.'],
  ['call_busy', '상대가 통화 중이어서 연결하지 못했습니다.'],
  ['call_failed', '회선 연결에 실패했습니다.'],
  ['call_canceled', '통화 연결이 취소됐습니다.'],
  ['call_audio_failed', '통화 음성을 연결하거나 전달하는 중 문제가 발생했습니다.'],
]) {
  test(`ended call explains ${error} without offering a redial`, async ({ page }) => {
    const fixture = await phoneFixture(page, 'ended')
    fixture.set({ outcome: 'incomplete', error_code: error })
    await page.goto(`/?conversation=${cid}`)
    const card = page.getByRole('region', { name: '통화 기능 테스트 통화' })
    await expect(card.getByText(message, { exact: true })).toBeVisible()
    await expect(card.getByText('확인하지 못한 내용이 남아 있습니다.')).toHaveCount(0)
    await expect(card.getByRole('button', { name: '통화 종료', exact: true })).toHaveCount(0)
    await expect(card.getByRole('button', { name: /재발신|다시 걸기/ })).toHaveCount(0)
    await page.reload()
    await expect(card.getByText(message, { exact: true })).toBeVisible()
    expect(fixture.mock.calls).toHaveLength(0)
  })
}

test('phone surface desktop and mobile visual evidence', async ({ page }) => {
  await phoneFixture(page)
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto(`/?conversation=${cid}`)
  await expect(page.getByRole('region', { name: '통화 기능 테스트 통화' })).toBeVisible()
  await page.screenshot({ path: '../docs/verification/step-05-desktop.png' })
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.getByRole('textbox', { name: '메시지' })).toBeInViewport({ ratio: 1 })
  await page.screenshot({ path: '../docs/verification/step-05-mobile.png' })
})

test('hidden page pauses polling and resumes when visible', async ({ page }) => {
  await phoneFixture(page)
  let reads = 0
  page.on('request', request => { if (request.url().endsWith('/api/calls/active')) reads++ })
  await page.goto(`/?conversation=${cid}`)
  await expect(page.getByRole('region', { name: '통화 기능 테스트 통화' })).toBeVisible()
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', { configurable: true, value: true })
    document.dispatchEvent(new Event('visibilitychange'))
  })
  const before = reads
  // Deliberately cross the 2-second polling boundary while the page is hidden.
  await page.waitForTimeout(2300)
  expect(reads).toBe(before)
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', { configurable: true, value: false })
    document.dispatchEvent(new Event('visibilitychange'))
  })
  await expect.poll(() => reads).toBeGreaterThan(before)
})

test('live question restores and a targeted answer is sent without starting chat or dialing', async ({ page }) => {
  const fixture = await phoneFixture(page)
  const q = { id: '00000000-0000-4000-8000-000000000601', call_id: fixture.get().id,
    delegation_id: 'd1', revision: 1, question: '화요일 오후 3시에 가능한가요?', options: ['가능해요', '어려워요'],
    status: 'pending', answer: null as string | null, expires_at: Date.now() / 1000 + 60 }
  let answers = 0
  fixture.set({ confirmations: [q] } as Partial<ReturnType<typeof fixture.get>>)
  await page.route('**/api/calls/*/confirmations/*/answer', async route => {
    answers++
    q.answer = route.request().postDataJSON().answer
    q.status = 'answered'
    fixture.set({ confirmations: [{ ...q }] } as Partial<ReturnType<typeof fixture.get>>)
    await route.fulfill({ json: q })
  })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(`/?conversation=${cid}`)
  await expect(page.getByText(q.question, { exact: true })).toBeVisible()
  await page.screenshot({ path: '../docs/verification/step-06b-question-mobile.png', fullPage: true })
  await page.reload()
  await page.getByRole('button', { name: '직접 답변하기', exact: true }).click()
  const input = page.getByRole('textbox', { name: '메시지' })
  await input.fill('화요일은 어렵고 수요일 오후가 좋아요')
  await input.press('Enter')
  await expect(page.getByText('통화에 전달 중', { exact: true })).toBeVisible()
  expect(answers).toBe(1)
  expect(fixture.mock.calls).toHaveLength(0)
  expect(fixture.stops()).toBe(0)
  q.status = 'applied'
  fixture.set({ confirmations: [{ ...q }] } as Partial<ReturnType<typeof fixture.get>>)
  await expect(page.getByText('통화 도우미에게 전달됨', { exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByText(q.answer!, { exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false)
})

for (const [status, message] of [
  ['audio_drained', '요청을 마쳤다고 보고했습니다. 마지막 음성 전송과 무음을 확인한 뒤 통화를 종료했습니다. 실제 청취 여부는 확인할 수 없습니다.'],
  ['playback_unconfirmed', '요청을 마쳤다고 보고했습니다. 음성 재생 확인을 기다리다 통화를 마쳤습니다.'],
]) {
  test(`live ${status} explains playback separately from task outcome`, async ({ page }) => {
    const fixture = await phoneFixture(page, 'ended')
    fixture.set({ outcome: 'incomplete', reported_summary: '합성 테스트 결과',
      end_report: JSON.stringify({ status, reason: 'goal_achieved' }) })
    await page.goto(`/?conversation=${cid}`)
    const card = page.getByRole('region', { name: '통화 기능 테스트 통화' })
    await expect(card.getByText(message)).toBeVisible()
    await expect(card.getByText('확인하지 못한 내용이 남아 있습니다.')).toHaveCount(0)
    await expect(card.getByText('종료 요청 시점의 요약')).toBeVisible()
    await page.reload()
    await expect(card.getByText(message)).toBeVisible()
  })
}
