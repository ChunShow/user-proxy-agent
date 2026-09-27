import { ApiError } from './errors'

export async function request<T>(path: string, body?: object, signal?: AbortSignal): Promise<T> {
  try {
    const response = await fetch(path, {
      method: body ? 'POST' : 'GET', cache: 'no-store', credentials: 'same-origin',
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(15_000)]) : AbortSignal.timeout(15_000),
    })
    if (!response.ok) {
      const data = await response.json().catch(() => ({}))
      throw new ApiError(data.error?.code ?? 'load_failed')
    }
    return response.status === 204 ? undefined as T : await response.json() as T
  } catch (error) {
    if (error instanceof ApiError) throw error
    throw new ApiError('load_failed')
  }
}
