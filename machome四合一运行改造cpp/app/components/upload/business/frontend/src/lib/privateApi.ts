import type {
  ChinaBroadMarketETFListResponse,
  HKConnectFXCloseAuditHistoryResponse,
  HKConnectFXDailyHistoryResponse,
  HKConnectFXHistoryResponse,
  HKConnectFXSnapshot,
  PrivateFundListResponse,
  PrivateFundSnapshot,
  PrivateFundSymbol,
  PrivateIndiaHistoryReviewResponse,
  PrivateIndiaNiftyReviewResponse,
  PrivateMinuteHistoryDatesResponse,
  PrivateMinuteHistoryResponse,
  PrivateSilverCloseHistoryResponse,
} from './privateTypes'
import type { ShareHistoryResponse } from './types'

const privateAPIBase = '/api/v1/private'

async function requestPrivateJSON<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${privateAPIBase}${path}`, {
    method: 'GET',
    cache: 'no-store',
    credentials: 'same-origin',
    headers: {
      Accept: 'application/json',
    },
    signal,
  })

  if (!response.ok) {
    let detail = ''
    try {
      const body = (await response.json()) as { error?: unknown }
      if (typeof body.error === 'string') detail = body.error
    } catch {
      // The status line remains useful when an upstream proxy returns HTML.
    }
    throw new Error(detail || `Private API 请求失败（HTTP ${response.status}）`)
  }

  return (await response.json()) as T
}

export function getPrivateFundList(signal?: AbortSignal): Promise<PrivateFundListResponse> {
  return requestPrivateJSON<PrivateFundListResponse>('/funds', signal)
}

export function getHKConnectFX(signal?: AbortSignal): Promise<HKConnectFXSnapshot> {
  return requestPrivateJSON<HKConnectFXSnapshot>('/hk-connect-fx', signal)
}

export function getHKConnectFXHistory(
  date: string,
  market: 'shanghai' | 'shenzhen' = 'shanghai',
  signal?: AbortSignal,
): Promise<HKConnectFXHistoryResponse> {
  return requestPrivateJSON<HKConnectFXHistoryResponse>(
    `/hk-connect-fx/history?date=${encodeURIComponent(date)}&market=${encodeURIComponent(market)}`,
    signal,
  )
}

export function getHKConnectFXDailyHistory(
  days = 7,
  market: 'shanghai' | 'shenzhen' = 'shanghai',
  signal?: AbortSignal,
): Promise<HKConnectFXDailyHistoryResponse> {
  return requestPrivateJSON<HKConnectFXDailyHistoryResponse>(
    `/hk-connect-fx/daily-history?days=${encodeURIComponent(days)}&market=${encodeURIComponent(market)}`,
    signal,
  )
}

export function getHKConnectFXCloseHistory(
  days = 60,
  market: 'shanghai' | 'shenzhen' = 'shanghai',
  signal?: AbortSignal,
): Promise<HKConnectFXCloseAuditHistoryResponse> {
  return requestPrivateJSON<HKConnectFXCloseAuditHistoryResponse>(
    `/hk-connect-fx/close-history?days=${encodeURIComponent(days)}&market=${encodeURIComponent(market)}`,
    signal,
  )
}

export function getPrivateChinaBroadMarketFunds(signal?: AbortSignal): Promise<ChinaBroadMarketETFListResponse> {
  return requestPrivateJSON<ChinaBroadMarketETFListResponse>('/a-share-broad-market/funds', signal)
}

export function getPrivateChinaBroadMarketShareHistory(
  symbol: string,
  days = 60,
  signal?: AbortSignal,
): Promise<ShareHistoryResponse> {
  return requestPrivateJSON<ShareHistoryResponse>(
    `/a-share-broad-market/funds/${encodeURIComponent(symbol)}/share-history?days=${days}`,
    signal,
  )
}

export function getPrivateFund(
  symbol: PrivateFundSymbol,
  signal?: AbortSignal,
): Promise<PrivateFundSnapshot> {
  return requestPrivateJSON<PrivateFundSnapshot>(`/funds/${encodeURIComponent(symbol)}`, signal)
}

export function getPrivateFundMinuteHistory(
  symbol: PrivateFundSymbol,
  days: 1 | 3 | 5,
  signal?: AbortSignal,
): Promise<PrivateMinuteHistoryResponse> {
  return requestPrivateJSON<PrivateMinuteHistoryResponse>(
    `/funds/${encodeURIComponent(symbol)}/minute-history?days=${days}`,
    signal,
  )
}

export function getPrivateFundMinuteHistoryDates(
  symbol: PrivateFundSymbol,
  signal?: AbortSignal,
): Promise<PrivateMinuteHistoryDatesResponse> {
  return requestPrivateJSON<PrivateMinuteHistoryDatesResponse>(
    `/funds/${encodeURIComponent(symbol)}/minute-history/dates`,
    signal,
  )
}

export function getPrivateFundMinuteHistoryByDate(
  symbol: PrivateFundSymbol,
  date: string,
  signal?: AbortSignal,
): Promise<PrivateMinuteHistoryResponse> {
  return requestPrivateJSON<PrivateMinuteHistoryResponse>(
    `/funds/${encodeURIComponent(symbol)}/minute-history?date=${encodeURIComponent(date)}`,
    signal,
  )
}

export function getPrivateSilverCloseHistory(
  symbol: PrivateFundSymbol,
  days = 365,
  signal?: AbortSignal,
): Promise<PrivateSilverCloseHistoryResponse> {
  return requestPrivateJSON<PrivateSilverCloseHistoryResponse>(
    `/funds/${encodeURIComponent(symbol)}/close-history?days=${days}`,
    signal,
  )
}

export function getPrivateIndiaHistoryReview(
  symbol: PrivateFundSymbol,
  days = 120,
  signal?: AbortSignal,
): Promise<PrivateIndiaHistoryReviewResponse> {
  return requestPrivateJSON<PrivateIndiaHistoryReviewResponse>(
    `/funds/${encodeURIComponent(symbol)}/india-history-review?days=${days}`,
    signal,
  )
}

export function getPrivateIndiaNiftyReview(
  symbol: PrivateFundSymbol,
  signal?: AbortSignal,
): Promise<PrivateIndiaNiftyReviewResponse> {
  return requestPrivateJSON<PrivateIndiaNiftyReviewResponse>(
    `/funds/${encodeURIComponent(symbol)}/nifty-bridge-history-review`,
    signal,
  )
}
