import assert from 'node:assert/strict'
import test from 'node:test'
import { sidebarWidth, storedSidebarWidth, keyboardSidebarWidth } from './sidebarWidth'

test('resizing keeps navigation usable and leaves room for chat', () => {
  assert.equal(sidebarWidth(100, 1440), 232)
  assert.equal(sidebarWidth(700, 1440), 440)
  assert.equal(sidebarWidth(400, 800), 360)
  assert.equal(sidebarWidth(312, 1440), 312)
})
test('missing or malformed preferences fall back without breaking layout', () => {
  for (const raw of [null, '', 'oops', 'Infinity', 'NaN']) assert.equal(storedSidebarWidth(raw), 272)
  assert.equal(storedSidebarWidth('320'), 320)
  assert.equal(storedSidebarWidth('-500'), 232)
  assert.equal(storedSidebarWidth('900'), 440)
})
test('keyboard resizing supports limits and reset without consuming unrelated keys', () => {
  assert.equal(keyboardSidebarWidth('ArrowRight', 300, 1440), 316)
  assert.equal(keyboardSidebarWidth('ArrowLeft', 240, 1440), 232)
  assert.equal(keyboardSidebarWidth('Home', 300, 1440), 232)
  assert.equal(keyboardSidebarWidth('End', 300, 800), 360)
  assert.equal(keyboardSidebarWidth('Enter', 360, 1440), 272)
  assert.equal(keyboardSidebarWidth('Tab', 300, 1440), null)
})
