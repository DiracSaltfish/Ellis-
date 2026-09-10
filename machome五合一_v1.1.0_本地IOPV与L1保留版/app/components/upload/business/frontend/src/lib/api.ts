import type {
  Branch,
  BranchSnapshot,
  ContactMessageListResponse,
  DebugAuthStatus,
  DebugStatus,
  EffectiveRatioFitHistoryResponse,
  FundSnapshot,
  HomeSnapshot,
  IntradayRebuildJob,
  IntradayRebuildStatus,
  MinuteHistoryDatesResponse,
  MinuteHistoryDeleteDayResult,
  MinuteHistoryRecomputeResult,
  MinuteHistoryResponse,
  NavSettingsStatus,
  ShareHistoryResponse,
  VisitCountResponse,
  YesterdayRedemptionBoardResponse,
} from './types'

const dataViewTokenStorageKey = 'newnavnav:data_view_token'
let dataViewToken = readStoredDataViewToken()
const messageAdminTokenStorageKey = 'newnavnav:message_admin_token'
let messageAdminToken = readStoredMessageAdminToken()

function readStoredDataViewToken() {
  if (typeof window === 'undefined') return ''
  try {
    return window.localStorage.getItem(dataViewTokenStorageKey) || ''
  } catch {
    return ''
  }
}

function storeDataViewToken(token: string) {
  dataViewToken = token.trim()
  if (typeof window === 'undefined') return
  try {
    if (dataViewToken) {
      window.localStorage.setItem(dataViewTokenStorageKey, dataViewToken)
    } else {
      window.localStorage.removeItem(dataViewTokenStorageKey)
    }
  } catch {
    // Storage can be unavailable in hardened browser modes; the in-memory token still works for this page load.
  }
}

function readStoredMessageAdminToken() {
  if (typeof window === 'undefined') return ''
  try {
    return window.sessionStorage.getItem(messageAdminTokenStorageKey) || ''
  } catch {
    return ''
  }
}

function storeMessageAdminToken(token: string) {
  messageAdminToken = token.trim()
  if (typeof window === 'undefined') return
  try {
    if (messageAdminToken) {
      window.sessionStorage.setItem(messageAdminTokenStorageKey, messageAdminToken)
    } else {
      window.sessionStorage.removeItem(messageAdminTokenStorageKey)
    }
  } catch {
    // Session storage can be unavailable in hardened browser modes; keep best-effort in-memory access.
  }
}

export function clearStoredDebugAccess() {
  storeDataViewToken('')
  storeMessageAdminToken('')
}

export function syncDataViewAccessFromURL() {
  if (typeof window === 'undefined') return
  const url = new URL(window.location.href)
  const key = url.searchParams.get('view_key') || ''
  const lock = url.searchParams.get('view_lock') === '1'
  if (!key && !lock) return
  if (lock) {
    storeDataViewToken('')
  } else {
    storeDataViewToken(key)
  }
  url.searchParams.delete('view_key')
  url.searchParams.delete('view_lock')
  window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`)
}

export function syncMessageAdminAccessFromURL() {
  if (typeof window === 'undefined') return
  const url = new URL(window.location.href)
  const key = url.searchParams.get('message_key') || ''
  const lock = url.searchParams.get('message_lock') === '1'
  if (!key && !lock) return
  if (lock) {
    storeMessageAdminToken('')
  } else {
    storeMessageAdminToken(key)
  }
  url.searchParams.delete('message_key')
  url.searchParams.delete('message_lock')
  window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`)
}

function jsonHeaders() {
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (dataViewToken) {
    headers['X-Data-View-Token'] = dataViewToken
  }
  if (messageAdminToken) {
    headers['X-Message-Admin-Token'] = messageAdminToken
  }
  return headers
}

interface MinuteHistoryTransportRow {
  // `min`/`mkp`/`estnav`/`pmp` are compact transport keys for minute history rows.
  min: string
  mkp: number
  estnav: number
  pmp: number
}

interface MinuteHistoryTransportResponse {
  symbol: string
  days: number
  date?: string
  rows: MinuteHistoryTransportRow[]
}

async function getJSON<T>(url: string): Promise<T> {
  const res = await fetch(url, { headers: jsonHeaders() })
  if (!res.ok) {
    let detail = ''
    try {
      const payload = await res.json()
      if (payload?.code === 'data_view_locked') {
        detail = payload.error || '当前估值数据正在校验，普通入口暂不展示。请使用调试入口打开。'
      } else if (payload?.error) {
        detail = payload.error
      }
    } catch {
      detail = ''
    }
    throw new Error(detail || `${res.status} ${res.statusText}`)
  }
  return (await res.json()) as T
}

function normalizeMinuteHistoryResponse(response: MinuteHistoryTransportResponse): MinuteHistoryResponse {
  return {
    symbol: response.symbol,
    days: response.days,
    date: response.date,
    rows: response.rows.map((row) => ({
      minute: row.min,
      market_price: row.mkp,
      estimated_nav: row.estnav,
      premium_pct: row.pmp,
    })),
  }
}

export function getBranches(): Promise<Branch[]> {
  return getJSON<Branch[]>('/api/v1/branches')
}

export function getHomeSnapshot(): Promise<HomeSnapshot> {
  return getJSON<HomeSnapshot>('/api/v1/home')
}

export function getBranchSnapshot(key: string): Promise<BranchSnapshot> {
  return getJSON<BranchSnapshot>(`/api/v1/branches/${encodeURIComponent(key)}`)
}

export function getFundSnapshot(symbol: string): Promise<FundSnapshot> {
  return getJSON<FundSnapshot>(`/api/v1/funds/${encodeURIComponent(symbol.toUpperCase())}`)
}

export async function getFundMinuteHistory(
  symbol: string,
  options: number | { days?: number; date?: string } = 2,
): Promise<MinuteHistoryResponse> {
  const clean = encodeURIComponent(symbol.toUpperCase())
  const params = new URLSearchParams()
  if (typeof options === 'number') {
    params.set('days', String(options))
  } else if (options.date) {
    params.set('date', options.date)
  } else if (options.days) {
    params.set('days', String(options.days))
  }
  const query = params.toString()
  const response = await getJSON<MinuteHistoryTransportResponse>(
    `/api/v1/funds/${clean}/minute-history${query ? `?${query}` : ''}`,
  )
  return normalizeMinuteHistoryResponse(response)
}

export function getFundMinuteHistoryDates(symbol: string, limit = 45): Promise<MinuteHistoryDatesResponse> {
  const clean = encodeURIComponent(symbol.toUpperCase())
  return getJSON<MinuteHistoryDatesResponse>(`/api/v1/funds/${clean}/minute-history/dates?limit=${limit}`)
}

export function getFundEffectiveRatioHistory(symbol: string, days = 120): Promise<EffectiveRatioFitHistoryResponse> {
  const clean = encodeURIComponent(symbol.toUpperCase())
  return getJSON<EffectiveRatioFitHistoryResponse>(`/api/v1/funds/${clean}/effective-ratio-history?days=${days}`)
}

export function getFundShareHistory(symbol: string, days = 60): Promise<ShareHistoryResponse> {
  const clean = encodeURIComponent(symbol.toUpperCase())
  return getJSON<ShareHistoryResponse>(`/api/v1/funds/${clean}/share-history?days=${days}`)
}

export function getYesterdayRedemptionBoard(): Promise<YesterdayRedemptionBoardResponse> {
  return getJSON<YesterdayRedemptionBoardResponse>('/api/v1/funds/yesterday-redemption-board')
}

export function getDebugStatus(): Promise<DebugStatus> {
  return getJSON<DebugStatus>('/api/v1/debug/status')
}

export function getIntradayRebuildStatus(): Promise<IntradayRebuildStatus> {
  return getJSON<IntradayRebuildStatus>('/api/v1/debug/private-intraday-rebuild/jobs')
}

export async function postIntradayRebuildJob(payload: {
  trade_date: string
  symbols: string[]
  fx_policy: 'final_cfets'
}): Promise<IntradayRebuildJob> {
  const res = await fetch('/api/v1/debug/private-intraday-rebuild/jobs', {
    method: 'POST',
    headers: {
      ...jsonHeaders(),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    let detail = ''
    try {
      const body = await res.json()
      detail = body?.error || ''
    } catch {
      detail = ''
    }
    throw new Error(detail || `${res.status} ${res.statusText}`)
  }
  return (await res.json()) as IntradayRebuildJob
}

export function getDebugAuthStatus(): Promise<DebugAuthStatus> {
  return getJSON<DebugAuthStatus>('/api/v1/debug/auth/status')
}

export async function postDebugLogin(payload: { username: string; password: string }): Promise<DebugAuthStatus> {
  const res = await fetch('/api/v1/debug/auth/login', {
    method: 'POST',
    headers: {
      ...jsonHeaders(),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    let detail = ''
    try {
      const body = await res.json()
      detail = body?.error || ''
    } catch {
      detail = ''
    }
    throw new Error(detail || `${res.status} ${res.statusText}`)
  }
  return (await res.json()) as DebugAuthStatus
}

export async function postDebugLogout(): Promise<DebugAuthStatus> {
  const res = await fetch('/api/v1/debug/auth/logout', {
    method: 'POST',
    headers: jsonHeaders(),
  })
  if (!res.ok) {
    let detail = ''
    try {
      const body = await res.json()
      detail = body?.error || ''
    } catch {
      detail = ''
    }
    throw new Error(detail || `${res.status} ${res.statusText}`)
  }
  return (await res.json()) as DebugAuthStatus
}

export function getNavSettingsStatus(): Promise<NavSettingsStatus> {
  return getJSON<NavSettingsStatus>('/api/v1/navsettings/status')
}

export async function postNavSettingsRefresh() {
  const res = await fetch('/api/v1/navsettings/refresh', {
    method: 'POST',
    headers: jsonHeaders(),
  })
  if (!res.ok) {
    let detail = ''
    try {
      const payload = await res.json()
      detail = payload?.error || ''
    } catch {
      detail = ''
    }
    throw new Error(detail || `${res.status} ${res.statusText}`)
  }
  return (await res.json()) as { ok: boolean; error?: string }
}

export async function postNavSettingsRecomputeToday(): Promise<MinuteHistoryRecomputeResult> {
  const res = await fetch('/api/v1/navsettings/recompute-today', {
    method: 'POST',
    headers: jsonHeaders(),
  })
  if (!res.ok) {
    let detail = ''
    try {
      const payload = await res.json()
      detail = payload?.error || ''
    } catch {
      detail = ''
    }
    throw new Error(detail || `${res.status} ${res.statusText}`)
  }
  return (await res.json()) as MinuteHistoryRecomputeResult
}

export async function postNavSettingsDeleteMinuteHistoryDay(payload: { date: string }): Promise<MinuteHistoryDeleteDayResult> {
  const res = await fetch('/api/v1/navsettings/minute-history/delete-day', {
    method: 'POST',
    headers: {
      ...jsonHeaders(),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    let detail = ''
    try {
      const body = await res.json()
      detail = body?.error || ''
    } catch {
      detail = ''
    }
    throw new Error(detail || `${res.status} ${res.statusText}`)
  }
  return (await res.json()) as MinuteHistoryDeleteDayResult
}

export async function postNavSettingsValuationPosition(payload: { symbol: string; ratio: number }): Promise<{
  ok: boolean
  message?: string
  error?: string
  refresh_error?: string
  status?: NavSettingsStatus
}> {
  const res = await fetch('/api/v1/navsettings/valuation-positions', {
    method: 'POST',
    headers: {
      ...jsonHeaders(),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    let detail = ''
    try {
      const body = await res.json()
      detail = body?.error || ''
    } catch {
      detail = ''
    }
    throw new Error(detail || `${res.status} ${res.statusText}`)
  }
  return (await res.json()) as {
    ok: boolean
    message?: string
    error?: string
    refresh_error?: string
    status?: NavSettingsStatus
  }
}

export function getVisitCounts(days = 30): Promise<VisitCountResponse> {
  return getJSON<VisitCountResponse>(`/api/v1/visits?days=${encodeURIComponent(String(days))}`)
}

export function getContactMessages(limit = 100): Promise<ContactMessageListResponse> {
  return getJSON<ContactMessageListResponse>(`/api/v1/admin/contact-messages?limit=${encodeURIComponent(String(limit))}`)
}

export async function postContactMessage(payload: { email: string; message: string }) {
  const res = await fetch('/api/v1/contact-messages', {
    method: 'POST',
    headers: {
      ...jsonHeaders(),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    let detail = ''
    try {
      const body = await res.json()
      detail = body?.error || ''
    } catch {
      detail = ''
    }
    throw new Error(detail || `${res.status} ${res.statusText}`)
  }
  return (await res.json()) as { ok: boolean }
}

export function trackPageView(target: string) {
  const clean = target.trim().slice(0, 255)
  if (!clean) return
  const body = JSON.stringify({ kind: 'page', target: clean })
  if (navigator.sendBeacon) {
    const sent = navigator.sendBeacon('/api/v1/visits/track', new Blob([body], { type: 'application/json' }))
    if (sent) return
  }
  void fetch('/api/v1/visits/track', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body,
    keepalive: true,
  }).catch(() => {})
}
