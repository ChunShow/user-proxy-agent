export const DEFAULT_SIDEBAR_WIDTH = 272
export const MIN_SIDEBAR_WIDTH = 232
export const SIDEBAR_WIDTH_KEY = 'user-proxy-agent.sidebar-width'

export function sidebarWidth(preferred: number, viewport: number) {
  return Math.round(Math.max(MIN_SIDEBAR_WIDTH, Math.min(preferred, 440, viewport * .45)))
}
export function storedSidebarWidth(raw: string | null) {
  const parsed = Number(raw)
  return !raw?.trim() || !Number.isFinite(parsed) ? DEFAULT_SIDEBAR_WIDTH : sidebarWidth(parsed, 1920)
}
export function keyboardSidebarWidth(key: string, width: number, viewport: number) {
  const next = ({ ArrowLeft: width - 16, ArrowRight: width + 16, Home: MIN_SIDEBAR_WIDTH, End: 440, Enter: DEFAULT_SIDEBAR_WIDTH } as Record<string, number>)[key]
  return next === undefined ? null : sidebarWidth(next, viewport)
}
