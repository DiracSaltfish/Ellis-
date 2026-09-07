// Private funds are registered server-side. Keeping the client type open
// avoids a code deployment merely to make a newly audited private fund route
// reachable; the API remains the authority for which symbols exist.
export type PrivateFundSymbol = string

export type HKConnectFXFlow = {
  market: 'shanghai' | 'shenzhen'
  trade_date: string
  buy_amount_hkd_100m: number
  sell_amount_hkd_100m: number
  total_amount_hkd_100m: number
  published_at: string
  fetched_at: string
  first_seen_at: string
  last_changed_at: string
  samples: number
  changes: number
  source: string
}

export type HKConnectFXReference = {
  valid_date: string
  published_date: string
  buy_rate: number
  sell_rate: number
  mid_rate: number
  fetched_at: string
  source: string
}

export type HKConnectFXSettlement = {
  valid_date: string
  buy_rate: number
  sell_rate: number
  fetched_at: string
  source: string
}

export type HKConnectFXQuote = {
  pair: string
  bid?: number | null
  ask?: number | null
  healthy: boolean
  observed_at?: string
  received_at: string
  source: string
  error?: string
}

export type HKConnectFXCentralParity = {
  pair: 'HKD/CNY'
  trade_date: string
  rate: number
  fetched_at: string
  source: string
}

export type HKConnectFXModel = {
  version: string
  quote_target_time: string
  rolling_window_days: number
  net_ratio_threshold: number
  separate_direction: boolean
  minimum_samples: number
  residual_hkd_cny: number
  residual_samples: number
  residual_calibrated_to: string
  oos_mean_absolute_error_bp: number
  oos_root_mean_square_error_bp: number
  oos_maximum_absolute_error_bp: number
  calibration_status: 'validated_shanghai' | 'pending_shenzhen'
}

export type HKConnectFXEstimate = {
  net_ratio: number
  quote_direction: string
  selected_hkd_cny: number
  adjusted_hkd_cny: number
  market_hkd_cny: number
  half_spread: number
  predicted_buy_settlement: number
  predicted_sell_settlement: number
  buy_live_vs_estimate?: number | null
  sell_live_vs_estimate?: number | null
  buy_change_vs_previous?: number | null
  sell_change_vs_previous?: number | null
  absolute_error_bp?: number | null
}

export type HKConnectFXStorage = {
  kind: string
  data_dir: string
  minute_file: string
  shenzhen_minute_file: string
  last_write_at?: string
  last_error?: string
}

export type HKConnectFXSnapshot = {
  schema_version: string
  trade_date: string
  generated_at: string
  status: string
  status_text: string
  actionable: boolean
  decision_window: string
  public_poll_started_at?: string
  reference?: HKConnectFXReference
  flow?: HKConnectFXFlow
  fx: HKConnectFXQuote
  central_parity?: HKConnectFXCentralParity
  previous_settlement?: HKConnectFXSettlement
  actual_settlement?: HKConnectFXSettlement
  model: HKConnectFXModel
  estimate?: HKConnectFXEstimate
  shenzhen_status: string
  shenzhen_status_text: string
  shenzhen_actionable: boolean
  shenzhen_flow?: HKConnectFXFlow
  shenzhen_previous_settlement?: HKConnectFXSettlement
  shenzhen_actual_settlement?: HKConnectFXSettlement
  shenzhen_model: HKConnectFXModel
  shenzhen_estimate?: HKConnectFXEstimate
  messages: string[]
  storage: HKConnectFXStorage
}

export type HKConnectFXMinutePoint = {
  timestamp: string
  trade_date: string
  status: string
  actionable: boolean
  reference_mid?: number | null
  flow_buy_amount_hkd_100m?: number | null
  flow_sell_amount_hkd_100m?: number | null
  flow_total_amount_hkd_100m?: number | null
  net_ratio?: number | null
  hkd_cny_bid?: number | null
  hkd_cny_ask?: number | null
  selected_hkd_cny?: number | null
  predicted_buy_settlement?: number | null
  predicted_sell_settlement?: number | null
  market_hkd_cny?: number | null
  buy_live_vs_estimate?: number | null
  sell_live_vs_estimate?: number | null
  buy_change_vs_previous?: number | null
  sell_change_vs_previous?: number | null
  flow_fetched_at?: string
  flow_last_changed_at?: string
  fx_observed_at?: string
}

export type HKConnectFXHistoryResponse = {
  schema_version: string
  trade_date: string
  market: 'shanghai' | 'shenzhen'
  rows: HKConnectFXMinutePoint[]
}

export type HKConnectFXDailySettlementPoint = {
  trade_date: string
  actual_buy_settlement: number
  actual_sell_settlement: number
  cfets_hkd_cny_1600?: number | null
  hkd_cny_central_parity?: number | null
}

export type HKConnectFXDailyHistoryResponse = {
  schema_version: string
  trade_date: string
  market: 'shanghai' | 'shenzhen'
  requested_days: number
  rows: HKConnectFXDailySettlementPoint[]
}

export type HKConnectFXCloseAuditRow = {
  market: 'shanghai' | 'shenzhen'
  trade_date: string
  captured_at: string
  capture_kind: string
  status: string
  model_version: string
  calibration_status: string
  residual_hkd_cny: number
  residual_samples: number
  reference_mid: number
  buy_amount_hkd_100m: number
  sell_amount_hkd_100m: number
  net_ratio: number
  hkd_cny_bid?: number | null
  hkd_cny_ask?: number | null
  cfets_hkd_cny_1600?: number | null
  selected_hkd_cny: number
  predicted_buy_settlement: number
  predicted_sell_settlement: number
  actual_buy_settlement?: number | null
  actual_sell_settlement?: number | null
  official_source?: string
  official_fetched_at?: string
  buy_error_bp?: number | null
  sell_error_bp?: number | null
  mean_absolute_error_bp?: number | null
}

export type HKConnectFXCloseAuditHistoryResponse = {
  schema_version: string
  market: 'shanghai' | 'shenzhen'
  requested_days: number
  decision_window: string
  methodology_note: string
  rows: HKConnectFXCloseAuditRow[]
}

export type PrivateLevel = {
  level: number
  price: number
  volume: number
}

export type PrivateDomesticQuote = {
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
  bid_levels?: PrivateLevel[]
  ask_levels?: PrivateLevel[]
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

export type PrivatePCFInput = {
  security_id: string
  trading_day: string
  pre_trading_day?: string
  creation?: string
  redemption: string
  creation_redemption_unit: number | null
  estimate_cash_component_cny: number | null
  nav_per_cu?: number | null
  component_count?: number
  xop_equivalent_shares?: number | null
  components?: PrivatePCFComponent[]
  source_url?: string
  sha256?: string
}

export type PrivatePCFComponent = {
  symbol: string
  name: string
  market: 'HK' | 'US' | string
  currency: 'HKD' | 'USD' | string
  quantity: number
}

export type PrivateFXInput = {
  source_observed_at?: string
  fallback_reason?: string
  pair: string
  rate: number | null
  trading_day: string
  quote_time: string
  source: string
  fetched_at: string
}

export type PrivateIBQuoteInput = {
  symbol: string
  contract?: string
  bid: number | null
  ask: number | null
  last?: number | null
  market_data_type?: string
  quote_session?: string
  source: string
  observed_at: string
}

export type PrivateIndiaAnchor = {
  key: string
  label: string
  weight: number
  price: number
  target_at: string
  observed_at: string
  source: string
  capture_status: string
}

export type PrivateIndiaT2Input = {
  base_nav: number
  base_nav_date: string
  base_fx: PrivateFXInput
  current_fx: PrivateFXInput
  investment_ratio: number
  static_ratio: number
  anchors: PrivateIndiaAnchor[]
  portfolio_as_of: string
  portfolio_source: string
  nifty_bridge?: PrivateIndiaNiftyBridgeInput
}

export type PrivateLOFReferencePrice = {
  symbol: string
  price: number
  price_basis: 'regular_session_close' | string
  target_at: string
  observed_at: string
  source: string
  capture_status: string
}

export type PrivateLOFEffectiveRatio = {
  value: number
  source: string
  default_value: number
  default_source: string
  override_source?: string
  override_updated_at?: string
  override_updated_by?: string
}

/**
 * Independent QDII LOF input.  It intentionally contains no PCF, redemption
 * unit, cash-substitution amount or CFETS intraday quote: SZ162411 propagates a
 * released NAV from its dated XOP regular-session close and SAFE central parity.
 */
export type PrivateLOFWeightedAnchorInput = {
  base_nav: number
  base_nav_date: string
  base_nav_source: string
  base_nav_fetched_at: string
  base_reference: PrivateLOFReferencePrice
  base_fx: PrivateFXInput
  current_fx: PrivateFXInput
  effective_ratio: PrivateLOFEffectiveRatio
}

export type PrivateLOFWeightedAnchorValuation = {
  base_nav: number
  base_nav_date: string
  base_nav_source: string
  base_nav_fetched_at: string
  base_reference_symbol: string
  base_reference_price: number
  base_reference_price_basis: string
  base_reference_target_at: string
  base_reference_observed_at: string
  base_reference_source: string
  base_reference_capture_status: string
  base_fx: number
  base_fx_trading_day: string
  base_fx_source: string
  current_fx: number
  current_fx_trading_day: string
  current_fx_source: string
  fx_multiplier: number
  effective_ratio: number
  effective_ratio_source: string
  default_effective_ratio: number
  default_effective_ratio_source: string
  static_ratio: number
  current_reference_bid: number
  current_reference_ask: number
  current_reference_last?: number | null
  current_reference_observed_at: string
  current_reference_source: string
  current_reference_market_data_type: string
  current_reference_quote_session: string
  reference_bid_multiplier: number
  reference_ask_multiplier: number
  nav_bid: number
  nav_ask: number
}

export type PrivateSilverSettlementInput = {
  base_nav: number
  base_nav_date: string
  base_nav_source: string
  base_nav_fetched_at: string
  trading_day: string
  contract: string
  contract_selection_version: string
  previous_settlement: number
  previous_settlement_date: string
  previous_settlement_source: string
  futures_price: number
  intraday_average: number
  intraday_average_basis: string
  observed_at: string
  source: string
}

export type PrivateSilverSettlementValuation = PrivateSilverSettlementInput & {
  settlement_nav: number
  trading_nav: number
  settlement_premium_rate: number
  trading_premium_rate: number
}

export type PrivateIndiaNiftyBridgeInput = {
  nifty: PrivateIBQuoteInput
  inda_reference: PrivateIBQuoteInput
  nifty_reference: PrivateIBQuoteInput
  reference_at: string
  beta: number
  contract_selection_version?: string
  roll_adjustment?: PrivateIndiaNiftyRollAdjustment
}

export type PrivateIndiaNiftyRollAdjustment = {
  roll_date: string
  captured_at: string
  old_contract: string
  new_contract: string
  direction: 'new_to_old' | 'old_to_new' | string
  bid_factor: number
  ask_factor: number
  source: string
}

export type PrivateMarketQuoteInput = {
  symbol: string
  market: 'HK' | 'US' | string
  currency: 'HKD' | 'USD' | string
  bid: number | null
  ask: number | null
  last?: number | null
  market_data_type?: string
  source: string
  observed_at: string
}

export type PrivateValuationInput = {
  schema_version: number
  symbol: string
  model_version: string
  valuation_kind?: 'lof_weighted_anchor' | string
  pcf: PrivatePCFInput
  fx: PrivateFXInput
  ib: PrivateIBQuoteInput
  fx_rates?: PrivateFXInput[]
  market_quotes?: PrivateMarketQuoteInput[]
  india?: PrivateIndiaT2Input
  lof?: PrivateLOFWeightedAnchorInput
  silver?: PrivateSilverSettlementInput
  source: string
  generated_at: string
  received_at: string
}

export type PrivateBasketValuation = {
  redemption_unit: number
  xop_equivalent_shares: number
  estimate_cash_component_cny: number
  stock_component_bid_cny: number
  stock_component_ask_cny: number
  basket_bid_cny: number
  basket_ask_cny: number
  nav_bid: number
  nav_ask: number
  buy_direction_premium_rate: number
  sell_direction_premium_rate: number
  formula?: string
}

export type PrivateIndiaValuationVariant = {
  key: 'direct_inda' | 'nifty_bridge' | string
  label: string
  description: string
  quote_symbol: string
  valuation: PrivateBasketValuation
  order_book: PrivateOrderBookValuation[]
}

export type PrivateIndiaValuations = {
  default_key: 'direct_inda' | 'nifty_bridge' | string
  direct_inda: PrivateIndiaValuationVariant
  nifty_bridge?: PrivateIndiaValuationVariant
}

export type PrivateComponentValuation = {
  symbol: string
  name: string
  market: 'HK' | 'US' | string
  currency: 'HKD' | 'USD' | string
  quantity: number
  bid: number
  ask: number
  fx_pair: string
  fx_rate: number
  bid_value_cny: number
  ask_value_cny: number
  source: string
  observed_at: string
}

export type PrivateOrderBookValuation = {
  side: 'ask' | 'bid' | string
  level: number
  price: number
  volume: number
  premium_rate_vs_basket_bid_nav: number
  premium_rate_vs_basket_ask_nav: number
}

export type PrivateFundSnapshot = {
  calculation_state?: string
  schema_version: string
  symbol: string
  name: string
  model_version: string
  valuation_kind?: 'lof_weighted_anchor' | string
  ready: boolean
  actionable: boolean
  as_of: string
  input?: PrivateValuationInput
  domestic_quote?: PrivateDomesticQuote
  valuation?: PrivateBasketValuation
  india_valuations?: PrivateIndiaValuations
  lof_valuation?: PrivateLOFWeightedAnchorValuation
  silver_valuation?: PrivateSilverSettlementValuation
  components?: PrivateComponentValuation[]
  order_book: PrivateOrderBookValuation[]
  warnings: string[]
}

export type PrivateFundListItem = {
  symbol: string
  name: string
  model_version: string
  valuation_kind?: 'lof_weighted_anchor' | string
  ready: boolean
  actionable: boolean
  as_of: string
  market_bid: number | null
  market_ask: number | null
  basket_bid_nav: number | null
  basket_ask_nav: number | null
  buy_direction_premium_rate: number | null
  sell_direction_premium_rate: number | null
  settlement_nav?: number | null
  trading_nav?: number | null
  settlement_premium_rate?: number | null
  trading_premium_rate?: number | null
  active_contract?: string
  share_date?: string
  shares_10k?: number | null
  share_change_10k?: number | null
  share_change_pct?: number | null
  warnings: string[]
}

export type PrivateFundListResponse = {
  schema_version: string
  as_of: string
  funds: PrivateFundListItem[]
}

// Research-only A-share broad-market ETF. These instruments deliberately have
// no valuation fields: the Private area exposes their exchange share history
// without adding them to the live valuation universe.
export type ChinaBroadMarketETF = {
  symbol: string
  name: string
  index_name: string
}

export type ChinaBroadMarketETFListResponse = {
  funds: ChinaBroadMarketETF[]
}

export type PrivateMinuteHistoryPoint = {
  minute: string
  market_price: number
  basket_bid_nav: number
  basket_ask_nav: number
  /** China-session NIFTY bridge IOPV; absent for direct/final-NAV replays. */
  nifty_bridge_bid_nav?: number | null
  nifty_bridge_ask_nav?: number | null
  nifty_bridge_selection_version?: string
  settlement_nav?: number | null
  trading_nav?: number | null
  active_contract?: string
  futures_price?: number | null
  intraday_average?: number | null
  buy_direction_premium_rate: number
  sell_direction_premium_rate: number
  pcf_trading_day?: string
  xop_equivalent_shares?: number | null
}

export type PrivateMinuteHistoryResponse = {
  symbol: string
  days: 1 | 3 | 5
  rows: PrivateMinuteHistoryPoint[]
}

export type PrivateMinuteHistoryDatesResponse = {
  symbol: string
  dates: string[]
}

export type PrivateSilverCloseHistoryRow = {
  trading_day: string
  close_minute: string
  official_nav?: number | null
  official_nav_source?: string
  market_price: number
  settlement_nav: number
  settlement_premium_rate: number
  trading_nav: number
  trading_premium_rate: number
  settlement_deviation_rate?: number | null
  active_contract?: string
  futures_price?: number | null
  intraday_average?: number | null
}

export type PrivateSilverCloseHistoryResponse = {
  schema_version: string
  symbol: string
  name: string
  model_version: string
  methodology_note: string
  rows: PrivateSilverCloseHistoryRow[]
}

export type PrivateIndiaHistoryReviewRow = {
  target_date: string
  official_nav?: number | null
  base_nav_date?: string
  base_nav?: number | null
  final_estimate_nav?: number | null
  final_deviation_pct?: number | null
  fitted_exposure?: number | null
  window_mape_pct?: number | null
  investment_ratio?: number | null
  static_ratio?: number | null
  base_anchor_price?: number | null
  target_anchor_price?: number | null
  base_fx?: number | null
  target_fx?: number | null
  source?: string
  fit_window_size: number
  status: string
  note?: string
}

export type PrivateIndiaHistoryReviewResponse = {
  symbol: string
  name: string
  model_version: string
  fit_window: number
  rows: PrivateIndiaHistoryReviewRow[]
  methodology_note: string
}

export type PrivateIndiaNiftyReviewCheckpoint = {
  minute: string
  label: string
  kind: 'normal' | 'close_observation' | 'roll_open'
}

export type PrivateIndiaNiftyReviewTiming = {
  china_sessions: string[]
  nifty_active_window: string
  reference_window_et: string
  reference_center_et: string
  roll_rule: string
  roll_basis_window_bjt: string
  roll_basis_center_bjt: string
  selection_version: string
}

export type PrivateIndiaNiftyReviewSummary = {
  trading_days: number
  minute_samples: number
  paired_samples: number
  bridge_bid_premium_mae_bps: number | null
  bridge_ask_premium_mae_bps: number | null
  direct_bid_premium_mae_bps: number | null
  direct_ask_premium_mae_bps: number | null
  paired_mae_delta_bps: number | null
  bridge_win_rate_pct: number | null
  bridge_interval_coverage_pct: number | null
  ordinary_days: number
  calendar_roll_days: number
  cross_contract_adjusted_days: number
}

export type PrivateIndiaNiftyReviewRollAdjustment = {
  roll_date: string
  captured_at: string
  old_contract: string
  new_contract: string
  direction: 'new_to_old' | 'old_to_new' | string
  bid_factor: number
  ask_factor: number
  source: string
}

export type PrivateIndiaNiftyReviewRow = {
  minute: string
  trading_day: string
  checkpoint: string
  state: string
  market_price: number | null
  official_nav: number | null
  direct_nav_bid: number | null
  direct_nav_ask: number | null
  bridge_nav_bid: number | null
  bridge_nav_ask: number | null
  official_premium_pct: number | null
  direct_premium_vs_bid_pct: number | null
  direct_premium_vs_ask_pct: number | null
  bridge_premium_vs_bid_pct: number | null
  bridge_premium_vs_ask_pct: number | null
  direct_error_vs_bid_bps: number | null
  direct_error_vs_ask_bps: number | null
  bridge_error_vs_bid_bps: number | null
  bridge_error_vs_ask_bps: number | null
  paired_abs_error_delta_bps: number | null
  bridge_closer: boolean | null
  bridge_contains_official: boolean | null
  base_nav_date: string
  base_nav: number | null
  investment_ratio: number | null
  static_ratio: number | null
  anchor_price: number | null
  base_fx: number | null
  current_fx: number | null
  synthetic_inda_bid: number | null
  synthetic_inda_ask: number | null
  nifty_bid: number | null
  nifty_ask: number | null
  nifty_contract: string
  nifty_observed_at: string
  inda_reference_bid: number | null
  inda_reference_ask: number | null
  inda_reference_contract: string
  nifty_reference_bid: number | null
  nifty_reference_ask: number | null
  nifty_reference_contract: string
  reference_at: string
  beta: number | null
  selection_version: string
  roll_adjustment?: PrivateIndiaNiftyReviewRollAdjustment | null
}

export type PrivateIndiaNiftyReviewResponse = {
  schema_version: string
  symbol: string
  name: string
  model_version: string
  as_of: string
  methodology_note: string
  checkpoints: PrivateIndiaNiftyReviewCheckpoint[]
  timing: PrivateIndiaNiftyReviewTiming
  summary: PrivateIndiaNiftyReviewSummary
  rows: PrivateIndiaNiftyReviewRow[]
}
