import { test } from 'node:test'
import assert from 'node:assert/strict'
import { decodeMulaw } from './callAudio.ts'

test('G.711 silence, maximum amplitudes and sign decode to finite PCM', () => {
  const pcm = decodeMulaw(new Uint8Array([255, 127, 0, 128, 170]))
  assert.equal(pcm[0], 0)
  assert.equal(Math.abs(pcm[1]), 0)
  assert.equal(pcm[2], -32124 / 32768)
  assert.equal(pcm[3], 32124 / 32768)
  assert.ok([...pcm].every(x => Number.isFinite(x) && Math.abs(x) <= 1))
})
