export interface ChatRequest {
  request_id: string
  messages: { role: 'user' | 'assistant'; content: string }[]
}
export interface StreamEvent {
  type: 'start' | 'delta' | 'done'
  request_id: string
  message_id: string
  text?: string
}
const errors: Record<string, [string, boolean]> = {
  not_configured: ['서버의 모델 설정을 확인해 주세요.', false],
  invalid_request: ['메시지 길이를 확인해 주세요. 대화가 길면 새로고침해 주세요.', false],
  provider_auth: ['모델 인증에 실패했습니다. 서버 설정을 확인해 주세요.', false],
  rate_limited: ['사용량 한도에 도달했습니다. 잠시 후 다시 시도해 주세요.', true],
  provider_unavailable: ['응답을 받지 못했습니다. 다시 시도해 주세요.', true],
  timeout: ['응답 시간이 초과되었습니다. 다시 시도해 주세요.', true],
  invalid_stream: ['응답이 정상적으로 완료되지 않았습니다. 다시 시도해 주세요.', true],
}
export class ChatError extends Error {
  retryable: boolean
  constructor(code: string) {
    const [message, retryable] = errors[code] ?? errors.provider_unavailable
    super(message)
    this.retryable = retryable
  }
}

export async function streamChat(request: ChatRequest, signal: AbortSignal, onEvent: (event: StreamEvent) => void): Promise<void> {
  const timeout = new AbortController()
  const timer = setTimeout(() => timeout.abort(new ChatError('timeout')), 140_000)
  const combined = AbortSignal.any([signal, timeout.signal])
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined
  const cancel = () => { void reader?.cancel().catch(() => {}) }
  try {
    const response = await fetch('/api/chat', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request), signal: combined, cache: 'no-store',
    })
    combined.throwIfAborted()
    if (!response.ok) {
      let code = 'provider_unavailable'
      try { code = (await response.json()).error?.code ?? code } catch { /* generic HTTP error */ }
      throw new ChatError(code)
    }
    if (!response.body || !response.headers.get('content-type')?.includes('text/event-stream')) throw new ChatError('invalid_stream')
    reader = response.body.getReader()
    combined.addEventListener('abort', cancel, { once: true })
    const decoder = new TextDecoder('utf-8', { fatal: true })
    let buffer = '', messageId = '', finished = false, total = 0
    while (!finished) {
      combined.throwIfAborted()
      const { value, done } = await reader.read()
      combined.throwIfAborted()
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true })
      if (buffer.length > 131072) throw new ChatError('invalid_stream')
      let boundary: RegExpExecArray | null
      while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
        const block = buffer.slice(0, boundary.index)
        buffer = buffer.slice(boundary.index + boundary[0].length)
        const lines = block.split(/\r?\n/).filter(line => !line.startsWith(':'))
        if (lines.every(line => !line)) continue
        const type = lines.find(line => line.startsWith('event:'))?.slice(6).trim()
        let data: Record<string, unknown>
        try { data = JSON.parse(lines.filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n')) } catch { throw new ChatError('invalid_stream') }
        if (!data || data.request_id !== request.request_id || typeof data.message_id !== 'string') throw new ChatError('invalid_stream')
        if (type === 'start' && !messageId) messageId = data.message_id
        else if (!messageId || data.message_id !== messageId || type === 'start') throw new ChatError('invalid_stream')
        if (type === 'error') throw new ChatError(typeof data.code === 'string' ? data.code : 'provider_unavailable')
        if (type !== 'start' && type !== 'delta' && type !== 'done') throw new ChatError('invalid_stream')
        if (type === 'delta' && typeof data.text !== 'string') throw new ChatError('invalid_stream')
        total += typeof data.text === 'string' ? data.text.length : 0
        if (total > 1_048_576) throw new ChatError('invalid_stream')
        combined.throwIfAborted()
        onEvent({ type, request_id: request.request_id, message_id: messageId, text: typeof data.text === 'string' ? data.text : undefined })
        if (type === 'done') { finished = true; break }
      }
      if (done && !finished) throw new ChatError('invalid_stream')
    }
  } catch (error) {
    if (combined.aborted) throw combined.reason
    if (error instanceof ChatError) throw error
    throw new ChatError('provider_unavailable')
  } finally {
    clearTimeout(timer)
    combined.removeEventListener('abort', cancel)
    if (reader) { await reader.cancel().catch(() => {}); reader.releaseLock() }
  }
}
