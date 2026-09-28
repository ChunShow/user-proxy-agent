import { test, expect } from '@playwright/test'
import { readFile, writeFile } from 'node:fs/promises'

function wav(samples: number[], rate: number) {
  const out = Buffer.alloc(44 + samples.length * 2)
  out.write('RIFF'); out.writeUInt32LE(out.length - 8, 4); out.write('WAVEfmt ', 8)
  out.writeUInt32LE(16, 16); out.writeUInt16LE(1, 20); out.writeUInt16LE(1, 22)
  out.writeUInt32LE(rate, 24); out.writeUInt32LE(rate * 2, 28)
  out.writeUInt16LE(2, 32); out.writeUInt16LE(16, 34); out.write('data', 36)
  out.writeUInt32LE(samples.length * 2, 40)
  samples.forEach((x, i) => out.writeInt16LE(Math.round(Math.max(-1, Math.min(1, x)) * 32767), 44 + i * 2))
  return out
}

for (const rate of [44100, 48000]) test(`listening renders a continuous waveform under jitter at ${rate}Hz`, async ({ page }, info) => {
  // Optional local synthetic ARS clip for the reproducible, phone-free evaluation.
  const raw = process.env.LISTENING_EVAL_CLIP
    ? await readFile(process.env.LISTENING_EVAL_CLIP)
    : Buffer.from(Array.from({ length: 16000 }, (_, i) => [255, 143, 136, 143, 255, 15, 8, 15][i % 8]))
  await page.goto('/')
  const result = await page.evaluate(async ({ payload, rate }) => {
    const modulePath = '/src/calls/callAudio.ts'
    const { CallAudioPlayer, decodeMulaw } = await import(/* @vite-ignore */ modulePath)
    const bytes = Uint8Array.from(atob(payload), c => c.charCodeAt(0))
    const duration = bytes.length / 8000
    async function render(jitter: boolean) {
      const player = new CallAudioPlayer()
      const graphRate = player.context.sampleRate
      const context = new OfflineAudioContext(1, Math.ceil((duration + 1) * graphRate), graphRate)
      await player.context.close()
      player.context = context
      let arrival = 0, previousArrival = 0
      Object.defineProperty(context, 'currentTime', { get: () => arrival })
      Object.defineProperty(context, 'state', { get: () => 'running' })
      const scheduled: { start: number; duration: number }[] = []
      const create = context.createBufferSource.bind(context)
      context.createBufferSource = () => {
        const source = create(), start = source.start.bind(source)
        source.start = (time = 0) => {
          scheduled.push({ start: time, duration: source.buffer!.duration })
          start(time)
        }
        return source
      }
      for (let offset = 0, i = 0; offset < bytes.length; offset += 160, i++) {
        const delay = jitter ? [0, .015, 0, .05, .01, 0, .03, 0][i % 8] : 0
        arrival = Math.max(previousArrival, offset / 8000 + delay)
        previousArrival = arrival
        player.play('caller', btoa(String.fromCharCode(...bytes.subarray(offset, offset + 160))))
      }
      const rendered = await context.startRendering()
      // Model the continuous final device resampling, not a resampler per packet.
      const device = new OfflineAudioContext(1, Math.ceil((duration + 1) * rate), rate)
      const output = device.createBufferSource()
      output.buffer = rendered; output.connect(device.destination); output.start()
      const deviceAudio = await device.startRendering()
      const gaps = scheduled.slice(1).map((s, i) => Math.max(0, s.start - scheduled[i].start - scheduled[i].duration))
      return { samples: Array.from(deviceAudio.getChannelData(0)), first: scheduled[0].start, graphRate,
        gaps: gaps.filter(x => x > 1e-6).length, gapMs: gaps.reduce((a, b) => a + b, 0) * 1000 }
    }
    const steady = await render(false), jitter = await render(true)
    const referenceContext = new OfflineAudioContext(1, Math.ceil((duration + 1) * rate), rate)
    const source = referenceContext.createBufferSource()
    source.buffer = referenceContext.createBuffer(1, bytes.length, 8000)
    source.buffer.copyToChannel(decodeMulaw(bytes), 0)
    source.connect(referenceContext.destination); source.start(steady.first)
    const reference = Array.from((await referenceContext.startRendering()).getChannelData(0))
    function snr(samples: number[]) {
      let signal = 0, error = 0
      for (let i = 0; i < reference.length; i++) {
        signal += reference[i] ** 2; error += (samples[i] - reference[i]) ** 2
      }
      return 10 * Math.log10(signal / Math.max(error, 1e-20))
    }
    return { reference, steady, jitter, steadySnr: snr(steady.samples), jitterSnr: snr(jitter.samples) }
  }, { payload: raw.toString('base64'), rate })
  const metrics = { rate, graphRate: result.steady.graphRate, steadyGaps: result.steady.gaps, jitterGaps: result.jitter.gaps,
    jitterGapMs: result.jitter.gapMs, steadySnr: result.steadySnr, jitterSnr: result.jitterSnr }
  console.log(JSON.stringify(metrics))
  await writeFile(info.outputPath('metrics.json'), JSON.stringify(metrics, null, 2))
  for (const [name, samples] of [['reference', result.reference], ['steady', result.steady.samples], ['jitter', result.jitter.samples]] as const) {
    await writeFile(info.outputPath(`${name}.wav`), wav(samples, rate))
  }
  expect(result.jitter.gaps).toBe(0)
  // Allow the final device resampler's edge transients on the abrupt test tone;
  // the old per-packet resampling path measured only ~28dB on the Korean clip.
  expect(result.steadySnr).toBeGreaterThan(45)
  expect(result.jitterSnr).toBeGreaterThan(45)
})
