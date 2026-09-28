import { test } from 'node:test'
import type { TestContext } from 'node:test'
import assert from 'node:assert/strict'
import { CallAudioPlayer, decodeMulaw } from './callAudio.ts'

test('G.711 silence, maximum amplitudes and sign decode to finite PCM', () => {
  const pcm = decodeMulaw(new Uint8Array([255, 127, 0, 128, 170]))
  assert.equal(pcm[0], 0)
  assert.equal(Math.abs(pcm[1]), 0)
  assert.equal(pcm[2], -32124 / 32768)
  assert.equal(pcm[3], 32124 / 32768)
  assert.ok([...pcm].every(x => Number.isFinite(x) && Math.abs(x) <= 1))
})

// A controllable audio clock exposes the schedule sent to Web Audio. The decoder
// and production scheduling execute unchanged; no wall-clock sleeps or network.
class AudioClock {
  currentTime = 0
  state = 'running'
  destination = {}
  sources: { startAt: number; buffer: { duration: number; samples: Float32Array }; stopped: boolean }[] = []
  createBuffer(_channels: number, length: number, rate: number) {
    return { duration: length / rate, samples: new Float32Array(length),
      copyToChannel(data: Float32Array) { this.samples.set(data) } }
  }
  createBufferSource() {
    const source = { startAt: 0, buffer: undefined as unknown as ReturnType<AudioClock['createBuffer']>,
      stopped: false, onended: () => {}, connect() {}, disconnect() {},
      start(at: number) { this.startAt = at }, stop() { this.stopped = true } }
    this.sources.push(source)
    return source
  }
  async resume() { this.state = 'running' }
  async close() { this.state = 'closed' }
}

function playerWithClock(t: TestContext) {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'AudioContext')
  Object.defineProperty(globalThis, 'AudioContext', { value: AudioClock, configurable: true })
  t.after(() => {
    if (previous) Object.defineProperty(globalThis, 'AudioContext', previous)
    else Reflect.deleteProperty(globalThis, 'AudioContext')
  })
  const player = new CallAudioPlayer()
  return { player, clock: player.context as unknown as AudioClock }
}

test('20ms packets with short delivery jitter play contiguously without dropping samples', t => {
  const { player, clock } = playerWithClock(t)
  const payload = Buffer.alloc(160, 170).toString('base64')
  // Ordered arrivals, including two delayed batches. All lateness <= 50ms.
  for (const time of [0, .035, .04, .11, .11, .11, .12, .155, .16, .2]) {
    clock.currentTime = time
    player.play('caller', payload)
  }
  const sources = clock.sources
  assert.equal(sources.length, 10)
  assert.ok(sources[0].startAt < .2, 'listening should not add a long initial delay')
  for (let i = 1; i < sources.length; i++) {
    assert.ok(Math.abs(sources[i].startAt - sources[i - 1].startAt - .02) < 1e-8,
      `packet ${i} introduces a playback gap`)
  }
  assert.equal(sources.reduce((n, s) => n + s.buffer.samples.length, 0), 1600)
  assert.ok(sources.every(s => !s.stopped && s.buffer.samples.every(x => x === 5372 / 32768)))
})

test('interrupting assistant discards its queued audio but preserves caller playback', t => {
  const { player, clock } = playerWithClock(t)
  const payload = Buffer.alloc(160, 170).toString('base64')
  player.play('assistant', payload)
  player.play('caller', payload)
  player.play('assistant', payload)
  player.clear('assistant')
  assert.deepEqual(clock.sources.map(s => s.stopped), [true, false, true])
  clock.currentTime = .4
  player.play('assistant', payload)
  assert.ok(clock.sources[3].startAt > .4 && clock.sources[3].startAt < .6)
  player.close()
  assert.ok(clock.sources.every(s => s.stopped))
  assert.equal(clock.state, 'closed')
})

test('a long stall restarts promptly and a delivery burst cannot queue unbounded stale audio', t => {
  const { player, clock } = playerWithClock(t)
  const payload = Buffer.alloc(160, 170).toString('base64')
  player.play('caller', payload)
  clock.currentTime = 3
  player.play('caller', payload)
  assert.ok(clock.sources[1].startAt > 3 && clock.sources[1].startAt < 3.2)
  for (let i = 0; i < 100; i++) player.play('caller', payload)
  const retained = clock.sources.filter(s => !s.stopped)
  assert.ok(retained.length > 0 && retained.length < 45)
  assert.ok(retained.every(s => s.startAt + s.buffer.duration < 3.85))
})
