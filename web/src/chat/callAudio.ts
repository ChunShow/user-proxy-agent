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
  constructor() { this.context = new AudioContext() }
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
    const start = Math.max(this.context.currentTime + 0.025, this.tails[track])
    this.sources.set(source, track)
    source.onended = () => { source.disconnect(); this.sources.delete(source) }
    source.start(start); this.tails[track] = start + buffer.duration
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
