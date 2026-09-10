export interface Branch {
  key: string
  name_cn: string
  old_path: string
  new_path: string
  sort_order: number
  symbols: string[]
  stale_after_seconds: number
}

export interface Quote {
  symbol: string
  name: string
  price: number
  prev_close: number
  open: number
  high: number
  low: number
  volume: number
  amount: number
  change_pct: number
  limit_up?: number
  limit_down?: number
  bid_levels?: QuoteLevel[]
  ask_levels?: QuoteLevel[]
  quote_date: string
  quote_time: string
  source: string
  source_symbol?: string
  quote_session?: string
  is_realtime: boolean
  realtime_status?: string
  stale_reason?: string
  fetched_at: string
  error?: string
}

export interface QuoteLevel {
  level: number
  price: number
  volume: number
}

export interface EstimateRow {
  symbol: string
  name: string
  market_price: number
  official_est: number
  fair_est: number
  realtime_est: number | null
  official_premium: number
  fair_premium: number
  realtime_premium: number | null
  estimate_date: string
  model_version: string
  effective_ratio?: number
  reference_symbol?: string
  purchase_limit?: number | null
  purchase_status?: string
  note?: string
}

export interface BranchEstimateRow {
  symbol: string
  name: string
  official_est: number
  fair_est: number
  realtime_est: number | null
  official_premium: number
  fair_premium: number
  realtime_premium: number | null
  model_version: string
  purchase_limit?: number | null
  purchase_status?: string
  note?: string
}

export interface BranchSnapshot {
  snapshot_key: string
  schema_version: string
  as_of: string
  ttl_seconds: number
  stale_after_seconds: number
  branch: Branch
  estimate_rows: BranchEstimateRow[]
  warnings: string[]
}

export interface HomeSnapshot {
  snapshot_key: string
  schema_version: string
  as_of: string
  ttl_seconds: number
  branches: BranchSummary[]
  warnings: string[]
}

export interface BranchSummary {
  branch: Branch
  as_of: string
  reference_count: number
  estimate_count: number
  realtime_count: number
  stale_count: number
  unsupported_count: number
  demo_count: number
  max_fair_premium: number
  max_fair_premium_symbol: string
  max_abs_fair_premium: number
  avg_abs_fair_premium: number
  model_counts: Record<string, number>
  warnings: string[]
}

export interface FundSnapshot {
  symbol: string
  branches: Branch[]
  quote: Quote
  estimate?: EstimateRow
  valuation_reference?: Quote
  valuation_inputs?: ValuationInput[]
  as_of: string
}

export interface BranchQuoteRow {
  symbol: string
  name: string
  price: number
  change_pct: number
  quote_date: string
  quote_time: string
  source: string
  source_symbol?: string
  is_realtime: boolean
  realtime_status?: string
  stale_reason?: string
}

export type QuoteTableRow = BranchQuoteRow | Quote

export interface MinuteHistoryPoint {
  minute: string
  market_price: number
  estimated_nav: number
  premium_pct: number
}

export interface MinuteHistoryResponse {
  symbol: string
  days: number
  date?: string
  rows: MinuteHistoryPoint[]
}

export interface MinuteHistoryDatesResponse {
  symbol: string
  dates: string[]
}

export interface MinuteHistoryRecomputeResult {
  ok: boolean
  day: string
  total_rows: number
  updated_rows: number
  skipped_rows: number
  symbols: string[]
  warnings?: string[]
}

export interface EffectiveRatioFitHistoryRow {
  symbol: string
  name?: string
  target_date: string
  base_date?: string
  window_start_date?: string
  window_end_date?: string
  window_size: number
  base_lag_trading_days: number
  ok_days: number
  model_version: string
  reference_symbol?: string
  configured_effective_ratio: number
  configured_static_ratio: number
  fitted_effective_ratio?: number | null
  fitted_static_ratio?: number | null
  official_nav: number
  base_nav: number
  t2_estimated_nav?: number | null
  nav_error?: number | null
  nav_error_pct?: number | null
  closing_realtime_minute?: string
  closing_realtime_premium_pct?: number | null
  window_mape_pct?: number | null
  window_mae_abs?: number | null
  status: string
  note?: string
  created_at?: string
  updated_at?: string
}

export interface EffectiveRatioFitHistoryResponse {
  symbol: string
  name?: string
  window_size: number
  base_lag_trading_days: number
  rows: EffectiveRatioFitHistoryRow[]
}

export interface ShareHistoryRecord {
  symbol: string
  name?: string
  share_date: string
  shares_10k: number
  previous_shares_10k?: number | null
  share_change_10k?: number | null
  share_change_pct?: number | null
  created_at?: string
  updated_at?: string
}

export interface ShareHistoryResponse {
  symbol: string
  name?: string
  unit: string
  rows: ShareHistoryRecord[]
}

export interface YesterdayRedemptionBoardRow {
  symbol: string
  name?: string
  share_date: string
  shares_10k: number
  previous_shares_10k?: number | null
  share_change_10k?: number | null
  share_change_pct?: number | null
  close_premium_pct?: number | null
  branch_names?: string[]
}

export interface YesterdayRedemptionBoardResponse {
  share_date: string
  unit: string
  tracked_symbols: number
  included_symbols: number
  stale_symbols: number
  missing_change_rows: number
  redemption_count: number
  subscription_count: number
  flat_count: number
  rows: YesterdayRedemptionBoardRow[]
}

export interface NavSettingsRatioStatus {
  symbol: string
  name: string
  model_version: string
  reference_symbol?: string
  effective_ratio: number
  default_effective_ratio: number
  effective_ratio_source?: string
  manual_override_ratio?: number | null
  manual_override_source?: string
  manual_override_updated_at?: string
  manual_override_updated_by?: string
  uploader_local_ratio?: number | null
  uploader_local_source?: string
  uploader_local_updated_at?: string
  uploader_sync_status?: string
}

export interface NavSettingsUploaderStatus {
  connected: boolean
  client_id?: string
  source?: string
  last_seen_at?: string
  known_states: number
}

export interface NavSettingsStatus {
  server_time: string
  minute_history_day: string
  ratios: NavSettingsRatioStatus[]
  operations: string[]
  uploader: NavSettingsUploaderStatus
}

export interface MinuteHistoryDeleteDayResult {
  ok: boolean
  day: string
  deleted: boolean
  symbol_count: number
  row_count: number
  message?: string
  warnings?: string[]
}

export interface DebugAuthStatus {
  authenticated: boolean
  auth_mode?: string
  username?: string
  expires_at?: string
}

export type IntradayRebuildState = 'queued' | 'dispatched' | 'capturing' | 'awaiting_fx' | 'completed' | 'failed' | 'cancelled'

export interface IntradayRebuildJob {
  id: string
  trade_date: string
  symbols: string[]
  fx_policy: string
  as_of?: string
  requested_by: string
  state: IntradayRebuildState
  progress: number
  message?: string
  error?: string
  agent_id?: string
  created_at: string
  updated_at: string
  started_at?: string
  finished_at?: string
}

export interface IntradayRebuildAgentStatus {
  connected: boolean
  agent_id?: string
  version?: string
  capabilities?: string[]
  last_seen_at?: string
}

export interface IntradayRebuildStatus {
  agent: IntradayRebuildAgentStatus
  jobs: IntradayRebuildJob[]
}

export interface VisitCount {
  date: string
  kind: string
  target: string
  method?: string
  count: number
}

export interface VisitCountResponse {
  days: number
  rows: VisitCount[]
}

export interface ContactMessage {
  id: number
  email: string
  message: string
  created_at: string
}

export interface ContactMessageListResponse {
  limit: number
  rows: ContactMessage[]
}

export interface ValuationInput {
  role: string
  symbol: string
  name?: string
  market?: string
  timezone?: string
  weight_ratio?: number
  position?: number
  holding_date?: string
  base_date?: string
  base_price?: number
  base_source?: string
  current_price?: number
  official_price?: number
  change_pct?: number
  quote_date?: string
  quote_time?: string
  fetched_at?: string
  source?: string
  source_symbol?: string
  quote_session?: string
  realtime_status?: string
  stale_reason?: string
  fx_adjust?: number
  currency?: string
  reference_symbol?: string
  used: boolean
  note?: string
}

export interface DebugStatus {
  server_time: string
  snapshot_status: Record<string, unknown>
  nav_sync_status?: NAVSyncStatus
  upload_status: {
    enabled: boolean
    count: number
    symbols: string[]
    quotes?: Quote[]
  }
  required: {
    count: number
    symbols: string[]
  }
  ws_clients: WSClientStatus[]
  upload_sources: UploadSourceStatus[]
  quote_sources: QuoteSourceSummary[]
  runtime_status?: RuntimeTaskStatus[]
  freshness: {
    realtime: number
    stale: number
    unsupported: number
    demo: number
  }
  recent_events: SystemEvent[]
  sample_quotes?: Quote[]
}

export interface NAVSyncStatus {
  enabled: boolean
  running: boolean
  run_id: number
  total_symbols: number
  current_index: number
  current_symbol?: string
  current_start_date?: string
  current_end_date?: string
  next_run_at?: string
  last_started_at?: string
  last_finished_at?: string
  last_duration_seconds?: number
  last_success: boolean
  last_error?: string
  requests: number
  written: number
  skipped: number
  failed: number
  no_rows: number
  last_written_at?: string
  last_written_symbol?: string
  last_written_date?: string
  last_written_rows?: number
  recent_symbol_results: NAVSyncSymbolResult[]
}

export interface NAVSyncSymbolResult {
  at: string
  symbol: string
  fund_code?: string
  status: string
  start_date?: string
  end_date?: string
  rows?: number
  error?: string
}

export interface WSClientStatus {
  id: string
  source: string
  remote_addr: string
  connected_at: string
  last_seen_at: string
  last_upload_at?: string
  upload_count: number
  quote_count: number
  error_count: number
  last_error?: string
  active: boolean
}

export interface UploadSourceStatus {
  source: string
  last_upload_at: string
  upload_count: number
  quote_count: number
  last_accepted: number
  last_warnings?: string[]
}

export interface QuoteSourceSummary {
  source: string
  count: number
}

export interface SystemEvent {
  at: string
  level: string
  source: string
  message: string
  details?: Record<string, unknown>
}

export interface RuntimeTaskStatus {
  key: string
  name: string
  enabled: boolean
  status: string
  schedule?: string
  running: boolean
  next_run_at?: string
  last_started_at?: string
  last_finished_at?: string
  last_duration_seconds?: number
  last_success: boolean
  last_error?: string
  run_count: number
  success_count: number
  failure_count: number
}
