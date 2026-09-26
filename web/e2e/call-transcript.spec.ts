import { expect, test, type Page } from '@playwright/test'
import { mockConversations } from './fixtures'
import type { CallActivityEvent } from '../src/chat/calls'

const cid = '00000000-0000-4000-8000-000000000701'
const other = '00000000-0000-4000-8000-000000000702'
const callId = '00000000-0000-4000-8000-000000000703'
function transcript(id: number, text: string, role = 'caller') {
  return { id, call_id: callId, kind: 'transcript', content: { role, text }, created_at: id * 3 }
}
async function fixture(page: Page, initial = 'connected') {
  const mock = await mockConversations(page)
  mock.conversations.set(cid, { id: cid, title: '전사 확인', updated_at: new Date().toISOString(), messages: [
    { id: 'user-transcript', role: 'user', text: '테스트 전화 부탁해', status: 'completed', retryable: false },
  ] })
  mock.conversations.set(other, { id: other, title: '다른 대화', updated_at: new Date().toISOString(), messages: [] })
  let status = initial, version = 1, failure = false, requests = 0, stops = 0
  const events: CallActivityEvent[] = [transcript(1, '내일 3시 괜찮으세요?'), transcript(2, '확인해 보겠습니다.', 'assistant')]
  const call = () => ({ id: callId, conversation_id: cid, source_user_message_id: 'user-transcript',
    destination: '01000000001', subject: '전사 테스트', purpose: '가상 일정 확인', status, version,
    outcome: 'pending', reported_summary: '', error_code: null, stop_requested: false, confirmations: [] })
  await page.route('**/api/conversations/*/calls*', route => route.fulfill({ json: {
    items: route.request().url().includes(cid) ? [call()] : [], next_cursor: null,
  } }))
  await page.route('**/api/calls/**', route => {
    const url = route.request().url()
    if (url.includes('/activity')) {
      requests++
      if (failure) return route.fulfill({ status: 503, json: {} })
      const after = Number(new URL(url).searchParams.get('after'))
      const remaining = events.filter(e => e.id > after), batch = remaining.slice(0, 100)
      return route.fulfill({ json: { events: batch, questions: [], next_after: batch.at(-1)?.id ?? after,
        has_more: remaining.length > 100, terminal: status === 'ended' } })
    }
    if (url.endsWith('/active')) return route.fulfill({ json: { items: status === 'ended' ? [] : [call()] } })
    if (url.endsWith('/stop')) { stops++; status = 'ended'; version++ }
    return route.fulfill({ json: call() })
  })
  return { events, requests: () => requests, stops: () => stops, mock,
    fail: (value: boolean) => { failure = value }, end: () => { status = 'ended'; version++ } }
}
const transcriptRegion = (page: Page) => page.getByRole('region', { name: '통화 내용', exact: true })

test('restores every page, renders external text literally, and drains the final page after end', async ({ page }) => {
  const f = await fixture(page)
  for (let i = 3; i <= 205; i++) f.events.push(transcript(i, `테스트 발화 ${i}`, i % 2 ? 'caller' : 'assistant'))
  f.events.push(transcript(206, '<img src=x onerror=alert(1)>'))
  await page.goto(`/?conversation=${cid}`)
  const region = transcriptRegion(page)
  await expect(region.getByText('테스트 발화 205', { exact: true })).toBeVisible()
  await expect(region.getByText('<img src=x onerror=alert(1)>', { exact: true })).toBeVisible()
  await expect(region.locator('img')).toHaveCount(0)
  await expect(region.getByText('내일 3시 괜찮으세요?', { exact: true })).toHaveCount(1)
  f.events.push(transcript(207, '마지막 인사입니다.', 'assistant')); f.end()
  await expect(region.getByText('마지막 인사입니다.', { exact: true })).toBeVisible()
  const settled = f.requests()
  await page.waitForTimeout(1200)
  expect(f.requests()).toBe(settled)
  await page.reload()
  await expect(page.getByRole('button', { name: '통화 내용 보기' })).toHaveAttribute('aria-expanded', 'false')
  await page.getByRole('button', { name: '통화 내용 보기' }).click()
  await expect(transcriptRegion(page).getByText('마지막 인사입니다.', { exact: true })).toBeVisible()
  expect(f.stops()).toBe(0); expect(f.mock.calls).toHaveLength(0)
})

test('keeps old text on a failed refresh and recovers without redial', async ({ page }) => {
  const f = await fixture(page)
  await page.goto(`/?conversation=${cid}`)
  await expect(transcriptRegion(page).getByText('내일 3시 괜찮으세요?')).toBeVisible()
  f.fail(true)
  await expect(page.getByText('내용을 갱신하지 못했어요.')).toBeVisible()
  await expect(transcriptRegion(page).getByText('내일 3시 괜찮으세요?')).toBeVisible()
  f.events.push(transcript(3, '새로 전달된 내용')); f.fail(false)
  await page.getByRole('button', { name: '통화 내용 다시 불러오기' }).click()
  await expect(transcriptRegion(page).getByText('새로 전달된 내용')).toBeVisible()
  await expect(page.getByText('내용을 갱신하지 못했어요.')).toHaveCount(0)
  expect(f.mock.calls).toHaveLength(0)
})

test('pauses closed and hidden transcripts and resumes from the existing cursor', async ({ page }) => {
  const f = await fixture(page)
  await page.goto(`/?conversation=${cid}`)
  await expect(transcriptRegion(page).getByText('내일 3시 괜찮으세요?')).toBeVisible()
  await page.getByRole('button', { name: '통화 내용 접기' }).click()
  const closed = f.requests()
  await page.waitForTimeout(1200); expect(f.requests()).toBe(closed)
  f.events.push(transcript(3, '접힌 동안 받은 내용'))
  await page.getByRole('button', { name: '통화 내용 보기' }).click()
  await expect(transcriptRegion(page).getByText('접힌 동안 받은 내용')).toBeVisible()
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', { configurable: true, value: true })
    document.dispatchEvent(new Event('visibilitychange'))
  })
  const hidden = f.requests()
  f.events.push(transcript(4, '숨겨진 동안 받은 내용'))
  await page.waitForTimeout(1200); expect(f.requests()).toBe(hidden)
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', { configurable: true, value: false })
    document.dispatchEvent(new Event('visibilitychange'))
  })
  await expect(transcriptRegion(page).getByText('숨겨진 동안 받은 내용')).toBeVisible()
})

test('new text respects reading position on mobile while stop stays available', async ({ page }) => {
  const f = await fixture(page)
  for (let i = 3; i <= 30; i++) f.events.push(transcript(i, `스크롤 확인 문장 ${i}`))
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(`/?conversation=${cid}`)
  const region = transcriptRegion(page)
  await expect(region.getByText('스크롤 확인 문장 30')).toBeVisible()
  await region.evaluate(el => { el.scrollTop = 0; el.dispatchEvent(new Event('scroll')) })
  f.events.push(transcript(31, '새로 도착한 마지막 문장'))
  await expect(page.getByRole('button', { name: '새 내용 보기' })).toBeVisible()
  expect(await region.evaluate(el => el.scrollTop)).toBe(0)
  await page.getByRole('button', { name: '새 내용 보기' }).click()
  await expect(region.getByText('새로 도착한 마지막 문장')).toBeInViewport()
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false)
  await page.getByRole('button', { name: '통화 종료', exact: true }).click()
  await expect(page.getByText('종료됨', { exact: true })).toBeVisible()
  expect(f.stops()).toBe(1)
})

test('a delayed response cannot inject the prior calls text into another conversation', async ({ page }) => {
  const f = await fixture(page)
  await page.goto(`/?conversation=${cid}`)
  await expect(transcriptRegion(page).getByText('내일 3시 괜찮으세요?')).toBeVisible()
  let waiting = false, release = () => {}
  const gate = new Promise<void>(resolve => { release = resolve })
  await page.route(`**/api/calls/${callId}/activity?*`, async route => {
    waiting = true
    await gate
    await route.fulfill({ json: { events: [transcript(3, '이전 요청의 늦은 결과')], questions: [],
      next_after: 3, has_more: false, terminal: false } })
  }, { times: 1 })
  await expect.poll(() => waiting).toBe(true)
  await page.getByRole('button', { name: '다른 대화', exact: true }).click()
  release()
  await expect(transcriptRegion(page)).toHaveCount(0)
  await expect(page.getByText('이전 요청의 늦은 결과')).toHaveCount(0)
  f.events.push(transcript(3, '현재 저장된 최신 결과'))
  await page.getByRole('button', { name: '대화로 이동', exact: true }).click()
  await expect(transcriptRegion(page).getByText('현재 저장된 최신 결과')).toBeVisible()
  await expect(page.getByText('이전 요청의 늦은 결과')).toHaveCount(0)
})

test('ended records without transcripts stay empty and keyboard controls work', async ({ page }) => {
  const f = await fixture(page, 'ended'); f.events.length = 0
  await page.goto(`/?conversation=${cid}`)
  const toggle = page.getByRole('button', { name: '통화 내용 보기' })
  await toggle.focus(); await page.keyboard.press('Enter')
  await expect(transcriptRegion(page).getByText('저장된 통화 내용이 없습니다.')).toBeVisible()
  await expect(page.getByText('음성 전송 기록 없음')).toBeVisible()
})

test('synthetic transcript desktop and mobile visual evidence', async ({ page }) => {
  const f = await fixture(page)
  f.events.push(transcript(3, '웹에서 확인한 답변을 전달드리겠습니다.', 'assistant'))
  f.events.push({ id: 4, call_id: callId, kind: 'audio_progress', created_at: Date.now() / 1000,
    content: { generated_bytes: 800, sent_bytes: 800, playback_acked_bytes: 800, interrupted: false } })
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto(`/?conversation=${cid}`)
  await expect(page.getByText('음성 재생 확인 수신')).toBeVisible()
  await page.screenshot({ path: '../docs/verification/step-06b-desktop.png' })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))))
  await expect(page.getByRole('button', { name: '통화 종료', exact: true })).toBeInViewport()
  await page.screenshot({ path: '../docs/verification/step-06b-mobile.png' })
})
