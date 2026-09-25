export interface HealthResponse {
  status: 'ok'
  service: 'agent-service'
}

export async function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const timeout = new AbortController()
  const timer = setTimeout(() => {
    timeout.abort(new DOMException('Connection timed out', 'TimeoutError'))
  }, 5000)
  const requestSignal = signal
    ? AbortSignal.any([signal, timeout.signal])
    : timeout.signal
  try {
    const response = await fetch('/api/health', { signal: requestSignal, cache: 'no-store' })
    if (!response.ok) throw new Error('Service unavailable')
    const data: unknown = await response.json()
    if (
      typeof data !== 'object' || data === null ||
      !('status' in data) || data.status !== 'ok' ||
      !('service' in data) || data.service !== 'agent-service'
    ) {
      throw new Error('Unexpected service response')
    }
    return { status: data.status, service: data.service }
  } finally {
    clearTimeout(timer)
  }
}
