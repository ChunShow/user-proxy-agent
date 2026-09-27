import { ApiError } from '../api/errors'
export interface ChatRequest {
  request_id: string
  conversation_id: string
  content?: string
  retry_message_id?: string
}
export interface StreamEvent {
  type: 'start' | 'delta' | 'done'
  request_id: string
  message_id: string
  conversation_id: string
  user_message_id?: string
  text?: string
}
export async function streamChat(request: ChatRequest, signal: AbortSignal, onEvent: (event: StreamEvent) => void): Promise<void> {
  const timeout = new AbortController()
  const timer = setTimeout(() => timeout.abort(new ApiError('timeout')), 140_000)
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
      throw new ApiError(code)
    }
    if (!response.body || !response.headers.get('content-type')?.includes('text/event-stream')) throw new ApiError('invalid_stream')
    reader = response.body.getReader()
    combined.addEventListener('abort', cancel, { once: true })
    const decoder = new TextDecoder('utf-8', { fatal: true })
    let buffer = '', messageId = '', finished = false, total = 0
    while (!finished) {
      combined.throwIfAborted()
      const { value, done } = await reader.read()
      combined.throwIfAborted()
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true })
      if (buffer.length > 131072) throw new ApiError('invalid_stream')
      let boundary: RegExpExecArray | null
      while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
        const block = buffer.slice(0, boundary.index)
        buffer = buffer.slice(boundary.index + boundary[0].length)
        const lines = block.split(/\r?\n/).filter(line => !line.startsWith(':'))
        if (lines.every(line => !line)) continue
        const type = lines.find(line => line.startsWith('event:'))?.slice(6).trim()
        let data: Record<string, unknown>
        try { data = JSON.parse(lines.filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n')) } catch { throw new ApiError('invalid_stream') }
        if (!data || data.request_id !== request.request_id || data.conversation_id !== request.conversation_id || typeof data.message_id !== 'string') throw new ApiError('invalid_stream')
        if (type === 'start' && !messageId) {
          if (typeof data.user_message_id !== 'string') throw new ApiError('invalid_stream')
          messageId = data.message_id
        }
        else if (!messageId || data.message_id !== messageId || type === 'start') throw new ApiError('invalid_stream')
        if (type === 'error') throw new ApiError(typeof data.code === 'string' ? data.code : 'provider_unavailable')
        if (type !== 'start' && type !== 'delta' && type !== 'done') throw new ApiError('invalid_stream')
        if (type === 'delta' && typeof data.text !== 'string') throw new ApiError('invalid_stream')
        total += typeof data.text === 'string' ? data.text.length : 0
        if (total > 1_048_576) throw new ApiError('invalid_stream')
        combined.throwIfAborted()
        onEvent({ type, request_id: request.request_id, message_id: messageId, conversation_id: request.conversation_id, user_message_id: typeof data.user_message_id === 'string' ? data.user_message_id : undefined, text: typeof data.text === 'string' ? data.text : undefined })
        if (type === 'done') { finished = true; break }
      }
      if (done && !finished) throw new ApiError('invalid_stream')
    }
  } catch (error) {
    if (combined.aborted) throw combined.reason
    if (error instanceof ApiError) throw error
    throw new ApiError('provider_unavailable')
  } finally {
    clearTimeout(timer)
    combined.removeEventListener('abort', cancel)
    if (reader) { await reader.cancel().catch(() => {}); reader.releaseLock() }
  }
}
