export function decodeMulaw(raw: Uint8Array) {
  const pcm = new Float32Array(raw.length)
  for (let i = 0; i < raw.length; i++) {
    const code = ~raw[i] & 255
    const magnitude = ((((code & 15) << 3) + 132) << ((code >> 4) & 7)) - 132
    pcm[i] = ((code & 128) ? -magnitude : magnitude) / 32768
  }
  return pcm
}

export class CallAudioPlayer {
  context: AudioContext
  private tails = { caller: 0, assistant: 0 }
  private sources = new Map<AudioBufferSourceNode, 'caller' | 'assistant'>()
  // Join telephone PCM in its native rate. Resampling each tiny source at the
  // device rate creates boundary artifacts; the browser resamples this complete
  // output stream once when sending it to the device instead.
  constructor() { this.context = new AudioContext({ sampleRate: 8000 }) }
  async resume() { await this.context.resume() }
  play(track: 'caller' | 'assistant', payload: string) {
    if (payload.length > 11000 || this.context.state !== 'running') return
    const raw = Uint8Array.from(atob(payload), c => c.charCodeAt(0))
    if (!raw.length || raw.length > 8000) return
    // Do not accumulate stale speech when a tab or network stalls.
    if (this.tails[track] - this.context.currentTime > 0.8) this.clear(track)
    const buffer = this.context.createBuffer(1, raw.length, 8000)
    buffer.copyToChannel(decodeMulaw(raw), 0)
    const source = this.context.createBufferSource()
    source.buffer = buffer; source.connect(this.context.destination)
    // Reserve jitter headroom only at startup / a true underrun. Reapplying a
    // minimum delay to every packet inserts gaps even when audio is queued.
    const start = this.tails[track] >= this.context.currentTime + 0.02
      ? this.tails[track] : Math.ceil((this.context.currentTime + 0.12) * 8000) / 8000
    this.sources.set(source, track)
    source.onended = () => { source.disconnect(); this.sources.delete(source) }
    source.start(start); this.tails[track] = (Math.round(start * 8000) + raw.length) / 8000
  }
  clear(track: 'caller' | 'assistant') {
    for (const [source, channel] of this.sources) if (channel === track) {
      source.stop(); source.disconnect(); this.sources.delete(source)
    }
    this.tails[track] = 0
  }
  close() {
    this.clear('caller'); this.clear('assistant')
    void this.context.close().catch(() => {})
  }
}
