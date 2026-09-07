package db

import (
	"context"
	"database/sql"
	"encoding/json"
	"regexp"
	"strings"
	"time"

	"newnavnav/internal/domain"
)

type Repository struct {
	db *sql.DB
}

func NewRepository(db *sql.DB) *Repository {
	return &Repository{db: db}
}

func (r *Repository) IncrementVisitCount(ctx context.Context, when time.Time, kind string, target string, method string) error {
	return r.AddVisitCounts(ctx, []domain.VisitCount{{
		Date:   when.Format("2006-01-02"),
		Kind:   kind,
		Target: target,
		Method: method,
		Count:  1,
	}})
}

func (r *Repository) AddVisitCounts(ctx context.Context, rows []domain.VisitCount) error {
	if len(rows) == 0 {
		return nil
	}
	stmt, err := r.db.PrepareContext(ctx, `
INSERT INTO daily_visit_counts (visit_date, event_kind, target, request_method, visit_count)
VALUES (?, ?, ?, ?, ?)
ON DUPLICATE KEY UPDATE
  visit_count = visit_count + VALUES(visit_count),
  updated_at = CURRENT_TIMESTAMP(3)`)
	if err != nil {
		return err
	}
	defer stmt.Close()
	for _, row := range rows {
		date := strings.TrimSpace(row.Date)
		kind := strings.TrimSpace(row.Kind)
		target := strings.TrimSpace(row.Target)
		method := strings.TrimSpace(row.Method)
		if date == "" || kind == "" || target == "" || row.Count <= 0 {
			continue
		}
		if method == "" {
			method = "-"
		}
		if _, err := stmt.ExecContext(ctx, date, kind, target, method, row.Count); err != nil {
			return err
		}
	}
	return nil
}

func (r *Repository) LoadVisitCounts(ctx context.Context, days int) ([]domain.VisitCount, error) {
	if days <= 0 {
		days = 30
	}
	if days > 366 {
		days = 366
	}
	rows, err := r.db.QueryContext(ctx, `
SELECT DATE_FORMAT(visit_date, '%Y-%m-%d'), event_kind, target, request_method, visit_count
FROM daily_visit_counts
WHERE visit_date >= DATE_SUB(CURRENT_DATE(), INTERVAL ? DAY)
ORDER BY visit_date DESC, event_kind ASC, visit_count DESC, target ASC`,
		days-1,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	out := []domain.VisitCount{}
	for rows.Next() {
		var row domain.VisitCount
		if err := rows.Scan(&row.Date, &row.Kind, &row.Target, &row.Method, &row.Count); err != nil {
			return nil, err
		}
		if row.Method == "-" {
			row.Method = ""
		}
		out = append(out, row)
	}
	return out, rows.Err()
}

func (r *Repository) EnsureSymbols(ctx context.Context, symbols []string) error {
	if len(symbols) == 0 {
		return nil
	}
	stmt, err := r.db.PrepareContext(ctx, `
INSERT INTO symbols (symbol, market, asset_type, name_cn, sina_symbol, currency, timezone, tradable, active)
VALUES (?, ?, ?, NULL, ?, ?, ?, 1, 1)
ON DUPLICATE KEY UPDATE
  sina_symbol = COALESCE(VALUES(sina_symbol), sina_symbol),
  active = 1`)
	if err != nil {
		return err
	}
	defer stmt.Close()

	seen := make(map[string]bool)
	for _, symbol := range symbols {
		symbol = strings.TrimSpace(symbol)
		if symbol == "" || seen[symbol] {
			continue
		}
		seen[symbol] = true
		if _, err := stmt.ExecContext(ctx, symbol, inferMarket(symbol), inferAssetType(symbol), inferSinaSymbol(symbol), inferCurrency(symbol), inferTimezone(symbol)); err != nil {
			return err
		}
	}
	return nil
}

func (r *Repository) UpsertLatestQuotes(ctx context.Context, quotes map[string]domain.Quote) error {
	symbols := make([]string, 0, len(quotes))
	for symbol := range quotes {
		symbols = append(symbols, symbol)
	}
	if err := r.EnsureSymbols(ctx, symbols); err != nil {
		return err
	}

	stmt, err := r.db.PrepareContext(ctx, `
INSERT INTO latest_quotes (
  symbol_id, price, prev_close, open_price, high_price, low_price, volume, amount,
  change_pct, limit_up, limit_down, quote_date, quote_time, source, raw_symbol,
  bid_levels_json, ask_levels_json, fetched_at
)
SELECT id, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ? FROM symbols WHERE symbol = ?
ON DUPLICATE KEY UPDATE
  price = VALUES(price),
  prev_close = VALUES(prev_close),
  open_price = VALUES(open_price),
  high_price = VALUES(high_price),
  low_price = VALUES(low_price),
  volume = VALUES(volume),
  amount = VALUES(amount),
  change_pct = VALUES(change_pct),
  limit_up = VALUES(limit_up),
  limit_down = VALUES(limit_down),
  quote_date = VALUES(quote_date),
  quote_time = VALUES(quote_time),
  source = VALUES(source),
  raw_symbol = VALUES(raw_symbol),
  bid_levels_json = VALUES(bid_levels_json),
  ask_levels_json = VALUES(ask_levels_json),
  fetched_at = VALUES(fetched_at)`)
	if err != nil {
		return err
	}
	defer stmt.Close()

	nameStmt, err := r.db.PrepareContext(ctx, `
UPDATE symbols
SET name_cn = CASE WHEN ? <> '' THEN ? ELSE name_cn END,
    updated_at = CURRENT_TIMESTAMP(3)
WHERE symbol = ?`)
	if err != nil {
		return err
	}
	defer nameStmt.Close()

	for symbol, quote := range quotes {
		source := quote.Source
		if source == "" {
			source = "unknown"
		}
		rawSymbol := quote.SourceSymbol
		if rawSymbol == "" {
			rawSymbol = inferSinaSymbol(symbol)
		}
		if _, err := stmt.ExecContext(ctx,
			nullableFloat(quote.Price),
			nullableFloat(quote.PrevClose),
			nullableFloat(quote.Open),
			nullableFloat(quote.High),
			nullableFloat(quote.Low),
			nullableFloat(quote.Volume),
			nullableFloat(quote.Amount),
			nullableFloat(quote.ChangePct),
			nullableFloat(quote.LimitUp),
			nullableFloat(quote.LimitDown),
			nullableDate(quote.QuoteDate),
			nullableTime(quote.QuoteTime),
			source,
			rawSymbol,
			nullableLevelsJSON(quote.BidLevels),
			nullableLevelsJSON(quote.AskLevels),
			quote.FetchedAt,
			symbol,
		); err != nil {
			return err
		}
		if _, err := nameStmt.ExecContext(ctx, quote.Name, quote.Name, symbol); err != nil {
			return err
		}
		if strings.HasPrefix(strings.ToLower(symbol), "fx_") && quote.Price > 0 {
			if err := r.upsertLatestFXQuote(ctx, symbol, quote); err != nil {
				return err
			}
		}
	}
	return nil
}

func (r *Repository) LoadLatestQuotes(ctx context.Context) (map[string]domain.Quote, error) {
	rows, err := r.db.QueryContext(ctx, `
SELECT s.symbol, COALESCE(s.name_cn, ''), COALESCE(lq.price, 0), COALESCE(lq.prev_close, 0),
       COALESCE(lq.open_price, 0), COALESCE(lq.high_price, 0), COALESCE(lq.low_price, 0),
       COALESCE(lq.volume, 0), COALESCE(lq.amount, 0), COALESCE(lq.change_pct, 0),
       COALESCE(lq.limit_up, 0), COALESCE(lq.limit_down, 0),
       COALESCE(DATE_FORMAT(lq.quote_date, '%Y-%m-%d'), ''),
       COALESCE(TIME_FORMAT(lq.quote_time, '%H:%i:%s'), ''),
       lq.source, COALESCE(lq.raw_symbol, ''), COALESCE(lq.bid_levels_json, ''),
       COALESCE(lq.ask_levels_json, ''), lq.fetched_at
FROM latest_quotes lq
JOIN symbols s ON s.id = lq.symbol_id
WHERE lq.price > 0 AND lq.source NOT IN ('demo', 'missing')`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	out := map[string]domain.Quote{}
	for rows.Next() {
		var quote domain.Quote
		var bidLevelsJSON string
		var askLevelsJSON string
		if err := rows.Scan(
			&quote.Symbol,
			&quote.Name,
			&quote.Price,
			&quote.PrevClose,
			&quote.Open,
			&quote.High,
			&quote.Low,
			&quote.Volume,
			&quote.Amount,
			&quote.ChangePct,
			&quote.LimitUp,
			&quote.LimitDown,
			&quote.QuoteDate,
			&quote.QuoteTime,
			&quote.Source,
			&quote.SourceSymbol,
			&bidLevelsJSON,
			&askLevelsJSON,
			&quote.FetchedAt,
		); err != nil {
			return nil, err
		}
		quote.BidLevels = parseStoredLevels(bidLevelsJSON)
		quote.AskLevels = parseStoredLevels(askLevelsJSON)
		if quote.SourceSymbol == "" {
			quote.SourceSymbol = inferSinaSymbol(quote.Symbol)
		}
		quote.QuoteSession = inferQuoteSession(quote.Symbol, quote.Source, quote.SourceSymbol)
		quote.IsRealtime = false
		quote.RealtimeStatus = "stale"
		quote.StaleReason = "latest stored quote fallback"
		out[quote.Symbol] = quote
	}
	return out, rows.Err()
}

func (r *Repository) upsertLatestFXQuote(ctx context.Context, symbol string, quote domain.Quote) error {
	pair := strings.ToUpper(strings.TrimPrefix(strings.ToLower(symbol), "fx_s"))
	if pair == "" {
		pair = strings.ToUpper(symbol)
	}
	_, err := r.db.ExecContext(ctx, `
INSERT INTO latest_fx_quotes (pair, rate, quote_date, quote_time, source)
VALUES (?, ?, ?, ?, ?)
ON DUPLICATE KEY UPDATE
  rate = VALUES(rate),
  quote_date = VALUES(quote_date),
  quote_time = VALUES(quote_time),
  source = VALUES(source)`,
		pair,
		quote.Price,
		nullableDate(quote.QuoteDate),
		nullableTime(quote.QuoteTime),
		quote.Source,
	)
	return err
}

func (r *Repository) UpsertFXCentralParity(ctx context.Context, rates []domain.FXCentralParity) error {
	if len(rates) == 0 {
		return nil
	}
	stmt, err := r.db.PrepareContext(ctx, `
INSERT INTO fx_central_parity (pair, rate_date, rate, source)
VALUES (?, ?, ?, ?)
ON DUPLICATE KEY UPDATE
  rate = VALUES(rate),
  source = VALUES(source)`)
	if err != nil {
		return err
	}
	defer stmt.Close()

	for _, item := range rates {
		pair := strings.ToUpper(strings.TrimSpace(item.Pair))
		rateDate := strings.TrimSpace(item.Date)
		source := strings.TrimSpace(item.Source)
		if pair == "" || rateDate == "" || item.Rate <= 0 {
			continue
		}
		if source == "" {
			source = "safe"
		}
		if _, err := stmt.ExecContext(ctx, pair, rateDate, item.Rate, source); err != nil {
			return err
		}
	}
	return nil
}

func (r *Repository) HasFXCentralParity(ctx context.Context, pair string, rateDate string) (bool, error) {
	pair = strings.ToUpper(strings.TrimSpace(pair))
	rateDate = strings.TrimSpace(rateDate)
	if pair == "" || rateDate == "" {
		return false, nil
	}
	var exists int
	err := r.db.QueryRowContext(ctx, `
SELECT 1
FROM fx_central_parity
WHERE pair = ? AND rate_date = ?
LIMIT 1`, pair, rateDate).Scan(&exists)
	if err == sql.ErrNoRows {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	return exists == 1, nil
}

func inferQuoteSession(symbol string, source string, sourceSymbol string) string {
	lowerSource := strings.ToLower(strings.TrimSpace(source))
	lowerRaw := strings.ToLower(strings.TrimSpace(sourceSymbol))
	lowerSymbol := strings.ToLower(strings.TrimSpace(symbol))
	switch {
	case lowerSource == "sina_hk" || strings.HasPrefix(lowerRaw, "rt_hk") || regexp.MustCompile(`^\d{5}$`).MatchString(symbol):
		return "hk_regular"
	case lowerSource == "sina_us_after_hours":
		return "extended"
	case lowerSource == "sina_us" || strings.HasPrefix(lowerRaw, "gb_"):
		return "regular"
	case lowerSource == "sina_hf" || strings.HasPrefix(lowerRaw, "hf_") || strings.HasPrefix(lowerSymbol, "hf_"):
		return "global_future"
	case lowerSource == "sina_nf" || strings.HasPrefix(lowerRaw, "nf_") || strings.HasPrefix(lowerSymbol, "nf_"):
		return "cn_future"
	case lowerSource == "sina_fx" || strings.HasPrefix(lowerRaw, "fx_"):
		return "fx_weekday"
	case lowerSource == "sina_znb" || strings.HasPrefix(lowerRaw, "znb_"):
		return "global_index"
	case lowerSource == "sina":
		return "cn_regular"
	default:
		return "stored"
	}
}

func (r *Repository) LoadValuationData(ctx context.Context) (domain.ValuationData, error) {
	data := domain.EmptyValuationData()
	loaders := []func(context.Context, *sql.DB, *domain.ValuationData) error{
		loadFundPairs,
		loadPositions,
		loadManualValuationPositionOverrides,
		loadLatestCalibrations,
		loadLatestNetValues,
		loadRecentNetValues,
		loadCurrentHoldingDates,
		loadHoldings,
		loadRecentDailyPrices,
		loadRecentValuationAnchorPrices,
		loadLatestFXQuotes,
		loadRecentFXCentralParity,
	}
	for _, load := range loaders {
		if err := load(ctx, r.db, &data); err != nil {
			return data, err
		}
	}
	return data, nil
}

func (r *Repository) LatestNetValueDate(ctx context.Context, symbol string) (string, bool, error) {
	var latest sql.NullString
	err := r.db.QueryRowContext(ctx, `
SELECT DATE_FORMAT(MAX(nv.nav_date), '%Y-%m-%d')
FROM net_values nv
JOIN symbols s ON s.id = nv.symbol_id
WHERE s.symbol = ?`, symbol).Scan(&latest)
	if err != nil {
		return "", false, err
	}
	if !latest.Valid || latest.String == "" {
		return "", false, nil
	}
	return latest.String, true, nil
}

func (r *Repository) EnsureEffectiveRatioFitHistorySchema(ctx context.Context) error {
	_, err := r.db.ExecContext(ctx, `
CREATE TABLE IF NOT EXISTS effective_ratio_fit_history (
  fund_symbol_id BIGINT UNSIGNED NOT NULL,
  target_date DATE NOT NULL,
  base_date DATE NULL,
  window_start_date DATE NULL,
  window_end_date DATE NULL,
  window_size INT NOT NULL,
  base_lag_trading_days INT NOT NULL DEFAULT 2,
  ok_days INT NOT NULL DEFAULT 0,
  model_version VARCHAR(64) NOT NULL,
  reference_symbol VARCHAR(32) NOT NULL DEFAULT '',
  configured_effective_ratio DECIMAL(12, 6) NOT NULL,
  configured_static_ratio DECIMAL(12, 6) NOT NULL,
  fitted_effective_ratio DECIMAL(12, 6) NULL,
  fitted_static_ratio DECIMAL(12, 6) NULL,
  official_nav DECIMAL(18, 6) NOT NULL DEFAULT 0,
  base_nav DECIMAL(18, 6) NOT NULL DEFAULT 0,
  t2_estimated_nav DECIMAL(18, 6) NULL,
  nav_error DECIMAL(18, 6) NULL,
  nav_error_pct DECIMAL(12, 6) NULL,
  closing_realtime_minute VARCHAR(16) NULL,
  closing_realtime_premium_pct DECIMAL(12, 6) NULL,
  window_mape_pct DECIMAL(12, 6) NULL,
  window_mae_abs DECIMAL(18, 6) NULL,
  status VARCHAR(32) NOT NULL,
  note TEXT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (fund_symbol_id, target_date, window_size),
  KEY idx_erfh_target_date (target_date),
  KEY idx_erfh_status (status),
  CONSTRAINT fk_erfh_fund_symbol FOREIGN KEY (fund_symbol_id) REFERENCES symbols(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`)
	if err != nil {
		return err
	}
	if err := r.ensureColumn(ctx, "effective_ratio_fit_history", "closing_realtime_minute", "ADD COLUMN closing_realtime_minute VARCHAR(16) NULL AFTER nav_error_pct"); err != nil {
		return err
	}
	return r.ensureColumn(ctx, "effective_ratio_fit_history", "closing_realtime_premium_pct", "ADD COLUMN closing_realtime_premium_pct DECIMAL(12, 6) NULL AFTER closing_realtime_minute")
}

func (r *Repository) UpsertEffectiveRatioFitHistory(ctx context.Context, rows []domain.EffectiveRatioFitHistoryRow) error {
	if len(rows) == 0 {
		return nil
	}
	symbols := make([]string, 0, len(rows))
	for _, row := range rows {
		symbols = append(symbols, row.Symbol)
	}
	if err := r.EnsureSymbols(ctx, symbols); err != nil {
		return err
	}
	stmt, err := r.db.PrepareContext(ctx, `
INSERT INTO effective_ratio_fit_history (
  fund_symbol_id, target_date, base_date, window_start_date, window_end_date,
  window_size, base_lag_trading_days, ok_days, model_version, reference_symbol,
  configured_effective_ratio, configured_static_ratio, fitted_effective_ratio, fitted_static_ratio,
  official_nav, base_nav, t2_estimated_nav, nav_error, nav_error_pct,
  closing_realtime_minute, closing_realtime_premium_pct, window_mape_pct,
  window_mae_abs, status, note
)
SELECT id, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
FROM symbols WHERE symbol = ?
ON DUPLICATE KEY UPDATE
  base_date = VALUES(base_date),
  window_start_date = VALUES(window_start_date),
  window_end_date = VALUES(window_end_date),
  base_lag_trading_days = VALUES(base_lag_trading_days),
  ok_days = VALUES(ok_days),
  model_version = VALUES(model_version),
  reference_symbol = VALUES(reference_symbol),
  configured_effective_ratio = VALUES(configured_effective_ratio),
  configured_static_ratio = VALUES(configured_static_ratio),
  fitted_effective_ratio = VALUES(fitted_effective_ratio),
  fitted_static_ratio = VALUES(fitted_static_ratio),
  official_nav = VALUES(official_nav),
  base_nav = VALUES(base_nav),
  t2_estimated_nav = VALUES(t2_estimated_nav),
  nav_error = VALUES(nav_error),
  nav_error_pct = VALUES(nav_error_pct),
  closing_realtime_minute = VALUES(closing_realtime_minute),
  closing_realtime_premium_pct = VALUES(closing_realtime_premium_pct),
  window_mape_pct = VALUES(window_mape_pct),
  window_mae_abs = VALUES(window_mae_abs),
  status = VALUES(status),
  note = VALUES(note)`)
	if err != nil {
		return err
	}
	defer stmt.Close()

	for _, row := range rows {
		if strings.TrimSpace(row.Symbol) == "" || strings.TrimSpace(row.TargetDate) == "" || row.WindowSize <= 0 {
			continue
		}
		if _, err := stmt.ExecContext(
			ctx,
			row.TargetDate,
			nullableDate(row.BaseDate),
			nullableDate(row.WindowStartDate),
			nullableDate(row.WindowEndDate),
			row.WindowSize,
			row.BaseLagTradingDays,
			row.OKDays,
			row.ModelVersion,
			row.ReferenceSymbol,
			row.ConfiguredEffectiveRatio,
			row.ConfiguredStaticRatio,
			nullableFloatPointer(row.FittedEffectiveRatio),
			nullableFloatPointer(row.FittedStaticRatio),
			row.OfficialNAV,
			row.BaseNAV,
			nullableFloatPointer(row.T2EstimatedNAV),
			nullableFloatPointer(row.NAVError),
			nullableFloatPointer(row.NAVErrorPct),
			nullableString(row.ClosingRealtimeMinute),
			nullableFloatPointer(row.ClosingRealtimePremiumPct),
			nullableFloatPointer(row.WindowMAPEPct),
			nullableFloatPointer(row.WindowMAEAbs),
			row.Status,
			nullableString(row.Note),
			row.Symbol,
		); err != nil {
			return err
		}
	}
	return nil
}

func (r *Repository) LoadEffectiveRatioFitHistory(ctx context.Context, symbol string, days int) ([]domain.EffectiveRatioFitHistoryRow, error) {
	symbol = strings.ToUpper(strings.TrimSpace(symbol))
	if symbol == "" {
		return nil, nil
	}
	if days <= 0 {
		days = 120
	}
	if days > 3660 {
		days = 3660
	}
	rows, err := r.db.QueryContext(ctx, `
SELECT s.symbol,
       COALESCE(s.name_cn, ''),
       DATE_FORMAT(h.target_date, '%Y-%m-%d'),
       COALESCE(DATE_FORMAT(h.base_date, '%Y-%m-%d'), ''),
       COALESCE(DATE_FORMAT(h.window_start_date, '%Y-%m-%d'), ''),
       COALESCE(DATE_FORMAT(h.window_end_date, '%Y-%m-%d'), ''),
       h.window_size,
       h.base_lag_trading_days,
       h.ok_days,
       h.model_version,
       COALESCE(h.reference_symbol, ''),
       h.configured_effective_ratio,
       h.configured_static_ratio,
       h.fitted_effective_ratio,
       h.fitted_static_ratio,
       h.official_nav,
       h.base_nav,
       h.t2_estimated_nav,
       h.nav_error,
       h.nav_error_pct,
       COALESCE(h.closing_realtime_minute, ''),
       h.closing_realtime_premium_pct,
       h.window_mape_pct,
       h.window_mae_abs,
       h.status,
       COALESCE(h.note, ''),
       DATE_FORMAT(h.created_at, '%Y-%m-%dT%H:%i:%s.%f'),
       DATE_FORMAT(h.updated_at, '%Y-%m-%dT%H:%i:%s.%f')
FROM effective_ratio_fit_history h
JOIN symbols s ON s.id = h.fund_symbol_id
WHERE s.symbol = ?
  AND h.target_date >= DATE_SUB(CURDATE(), INTERVAL ? DAY)
ORDER BY h.target_date DESC, h.window_size DESC`,
		symbol,
		days-1,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	out := make([]domain.EffectiveRatioFitHistoryRow, 0)
	for rows.Next() {
		var row domain.EffectiveRatioFitHistoryRow
		var fittedRatio sql.NullFloat64
		var fittedStatic sql.NullFloat64
		var t2Estimate sql.NullFloat64
		var navError sql.NullFloat64
		var navErrorPct sql.NullFloat64
		var closingRealtimePremiumPct sql.NullFloat64
		var windowMAPE sql.NullFloat64
		var windowMAE sql.NullFloat64
		if err := rows.Scan(
			&row.Symbol,
			&row.Name,
			&row.TargetDate,
			&row.BaseDate,
			&row.WindowStartDate,
			&row.WindowEndDate,
			&row.WindowSize,
			&row.BaseLagTradingDays,
			&row.OKDays,
			&row.ModelVersion,
			&row.ReferenceSymbol,
			&row.ConfiguredEffectiveRatio,
			&row.ConfiguredStaticRatio,
			&fittedRatio,
			&fittedStatic,
			&row.OfficialNAV,
			&row.BaseNAV,
			&t2Estimate,
			&navError,
			&navErrorPct,
			&row.ClosingRealtimeMinute,
			&closingRealtimePremiumPct,
			&windowMAPE,
			&windowMAE,
			&row.Status,
			&row.Note,
			&row.CreatedAt,
			&row.UpdatedAt,
		); err != nil {
			return nil, err
		}
		row.FittedEffectiveRatio = nullableFloat64ToPointer(fittedRatio)
		row.FittedStaticRatio = nullableFloat64ToPointer(fittedStatic)
		row.T2EstimatedNAV = nullableFloat64ToPointer(t2Estimate)
		row.NAVError = nullableFloat64ToPointer(navError)
		row.NAVErrorPct = nullableFloat64ToPointer(navErrorPct)
		row.ClosingRealtimePremiumPct = nullableFloat64ToPointer(closingRealtimePremiumPct)
		row.WindowMAPEPct = nullableFloat64ToPointer(windowMAPE)
		row.WindowMAEAbs = nullableFloat64ToPointer(windowMAE)
		out = append(out, row)
	}
	return out, rows.Err()
}

func (r *Repository) UpsertNetValues(ctx context.Context, values []domain.NetValue) error {
	if len(values) == 0 {
		return nil
	}
	symbols := make([]string, 0, len(values))
	for _, value := range values {
		symbols = append(symbols, value.Symbol)
	}
	if err := r.EnsureSymbols(ctx, symbols); err != nil {
		return err
	}

	stmt, err := r.db.PrepareContext(ctx, `
INSERT INTO net_values (symbol_id, nav_date, nav, source, confidence)
SELECT id, ?, ?, ?, ? FROM symbols WHERE symbol = ?
ON DUPLICATE KEY UPDATE
  nav = VALUES(nav),
  confidence = VALUES(confidence)`)
	if err != nil {
		return err
	}
	defer stmt.Close()

	for _, value := range values {
		if value.Symbol == "" || value.Date == "" || value.NAV <= 0 {
			continue
		}
		source := value.Source
		if source == "" {
			source = "eastmoney"
		}
		confidence := value.Confidence
		if confidence == "" {
			confidence = "official"
		}
		if _, err := stmt.ExecContext(ctx, value.Date, value.NAV, source, confidence, value.Symbol); err != nil {
			return err
		}
	}
	return nil
}

func (r *Repository) UpsertDailyPrices(ctx context.Context, prices []domain.DailyPrice) error {
	if len(prices) == 0 {
		return nil
	}
	symbols := make([]string, 0, len(prices))
	for _, price := range prices {
		symbols = append(symbols, price.Symbol)
	}
	if err := r.EnsureSymbols(ctx, symbols); err != nil {
		return err
	}

	stmt, err := r.db.PrepareContext(ctx, `
INSERT INTO daily_prices (symbol_id, trade_date, close_price, adj_close, source)
SELECT id, ?, ?, ?, ? FROM symbols WHERE symbol = ?
ON DUPLICATE KEY UPDATE
  close_price = VALUES(close_price),
  adj_close = VALUES(adj_close),
  source = VALUES(source)`)
	if err != nil {
		return err
	}
	defer stmt.Close()

	for _, price := range prices {
		if price.Symbol == "" || price.Date == "" || price.Close <= 0 {
			continue
		}
		adjClose := price.AdjClose
		if adjClose <= 0 {
			adjClose = price.Close
		}
		source := price.Source
		if source == "" {
			source = "sina_daily_close"
		}
		if _, err := stmt.ExecContext(ctx, price.Date, price.Close, adjClose, source, price.Symbol); err != nil {
			return err
		}
	}
	return nil
}

func (r *Repository) UpsertHoldingSnapshot(ctx context.Context, fundSymbol string, holdingDate string, position float64, source string, holdings []domain.Holding) (int, error) {
	fundSymbol = strings.ToUpper(strings.TrimSpace(fundSymbol))
	holdingDate = strings.TrimSpace(holdingDate)
	source = strings.TrimSpace(source)
	if source == "" {
		source = "upload_holdings"
	}
	if fundSymbol == "" || holdingDate == "" || len(holdings) == 0 {
		return 0, nil
	}

	symbols := []string{fundSymbol}
	for _, holding := range holdings {
		symbol := strings.ToUpper(strings.TrimSpace(holding.HoldingSymbol))
		if symbol == "" || holding.Ratio <= 0 {
			continue
		}
		symbols = append(symbols, symbol)
	}
	if err := r.EnsureSymbols(ctx, symbols); err != nil {
		return 0, err
	}

	tx, err := r.db.BeginTx(ctx, nil)
	if err != nil {
		return 0, err
	}
	defer func() {
		_ = tx.Rollback()
	}()

	if position > 0 {
		if _, err := tx.ExecContext(ctx, `
INSERT INTO fund_positions (fund_symbol_id, position_ratio, source)
SELECT id, ?, ? FROM symbols WHERE symbol = ?
ON DUPLICATE KEY UPDATE position_ratio = VALUES(position_ratio), source = VALUES(source)`, position, source, fundSymbol); err != nil {
			return 0, err
		}
	}
	if _, err := tx.ExecContext(ctx, `
UPDATE fund_holding_dates fhd
JOIN symbols s ON s.id = fhd.fund_symbol_id
SET fhd.is_current = 0
WHERE s.symbol = ? AND fhd.holding_date <> ?`, fundSymbol, holdingDate); err != nil {
		return 0, err
	}
	if _, err := tx.ExecContext(ctx, `
INSERT INTO fund_holding_dates (fund_symbol_id, holding_date, source, is_current)
SELECT id, ?, ?, 1 FROM symbols WHERE symbol = ?
ON DUPLICATE KEY UPDATE source = VALUES(source), is_current = VALUES(is_current)`, holdingDate, source, fundSymbol); err != nil {
		return 0, err
	}
	if _, err := tx.ExecContext(ctx, `
DELETE fh FROM fund_holdings fh
JOIN symbols f ON f.id = fh.fund_symbol_id
WHERE f.symbol = ? AND fh.holding_date = ?`, fundSymbol, holdingDate); err != nil {
		return 0, err
	}

	nameStmt, err := tx.PrepareContext(ctx, `
UPDATE symbols
SET name_cn = ?, updated_at = CURRENT_TIMESTAMP(3)
WHERE symbol = ? AND ? <> ''`)
	if err != nil {
		return 0, err
	}
	defer nameStmt.Close()

	holdingStmt, err := tx.PrepareContext(ctx, `
INSERT INTO fund_holdings (fund_symbol_id, holding_date, holding_symbol_id, holding_name, ratio, fx_adjust, currency, source)
SELECT f.id, ?, h.id, ?, ?, ?, ?, ?
FROM symbols f
JOIN symbols h ON h.symbol = ?
WHERE f.symbol = ?
ON DUPLICATE KEY UPDATE
  holding_name = VALUES(holding_name),
  ratio = VALUES(ratio),
  fx_adjust = VALUES(fx_adjust),
  currency = VALUES(currency),
  source = VALUES(source)`)
	if err != nil {
		return 0, err
	}
	defer holdingStmt.Close()

	accepted := 0
	for _, holding := range holdings {
		symbol := strings.ToUpper(strings.TrimSpace(holding.HoldingSymbol))
		name := strings.TrimSpace(holding.HoldingName)
		currency := strings.ToUpper(strings.TrimSpace(holding.Currency))
		if symbol == "" || holding.Ratio <= 0 {
			continue
		}
		if _, err := nameStmt.ExecContext(ctx, name, symbol, name); err != nil {
			return accepted, err
		}
		if _, err := holdingStmt.ExecContext(ctx, holdingDate, name, holding.Ratio, holding.FXAdjust, currency, source, symbol, fundSymbol); err != nil {
			return accepted, err
		}
		accepted++
	}

	if err := tx.Commit(); err != nil {
		return accepted, err
	}
	return accepted, nil
}

func (r *Repository) UpsertValuationAnchorPrices(ctx context.Context, prices []domain.ValuationAnchorPrice) error {
	if len(prices) == 0 {
		return nil
	}
	symbols := make([]string, 0, len(prices)*2)
	for _, price := range prices {
		symbols = append(symbols, price.FundSymbol, price.ReferenceSymbol)
	}
	if err := r.EnsureSymbols(ctx, symbols); err != nil {
		return err
	}

	stmt, err := r.db.PrepareContext(ctx, `
INSERT INTO valuation_anchor_prices (
  fund_symbol_id, anchor_date, anchor_key, reference_symbol_id, target_at_utc,
  target_timezone, observed_at_utc, price, weight, source, capture_status
)
SELECT fs.id, ?, ?, rs.id, ?, ?, ?, ?, ?, ?, ?
FROM symbols fs
JOIN symbols rs ON rs.symbol = ?
WHERE fs.symbol = ?
ON DUPLICATE KEY UPDATE
  reference_symbol_id = VALUES(reference_symbol_id),
  target_at_utc = VALUES(target_at_utc),
  target_timezone = VALUES(target_timezone),
  observed_at_utc = VALUES(observed_at_utc),
  price = VALUES(price),
  weight = VALUES(weight),
  source = VALUES(source),
  capture_status = VALUES(capture_status)`)
	if err != nil {
		return err
	}
	defer stmt.Close()

	for _, price := range prices {
		if price.FundSymbol == "" || price.ReferenceSymbol == "" || price.AnchorDate == "" || price.AnchorKey == "" || price.Price <= 0 || price.Weight <= 0 || price.TargetAt.IsZero() {
			continue
		}
		source := price.Source
		if source == "" {
			source = "valuation_anchor_upload"
		}
		status := price.CaptureStatus
		if status == "" {
			status = "captured"
		}
		timezone := price.TargetTimezone
		if timezone == "" {
			timezone = "UTC"
		}
		if _, err := stmt.ExecContext(ctx,
			price.AnchorDate,
			price.AnchorKey,
			sqlTimeUTC(price.TargetAt),
			timezone,
			nullableSQLTimeUTC(price.ObservedAt),
			price.Price,
			price.Weight,
			source,
			status,
			price.ReferenceSymbol,
			price.FundSymbol,
		); err != nil {
			return err
		}
	}
	return nil
}

func (r *Repository) UpsertCalibrations(ctx context.Context, calibrations []domain.Calibration) error {
	if len(calibrations) == 0 {
		return nil
	}
	symbols := make([]string, 0, len(calibrations))
	for _, cal := range calibrations {
		symbols = append(symbols, cal.Symbol)
	}
	if err := r.EnsureSymbols(ctx, symbols); err != nil {
		return err
	}

	stmt, err := r.db.PrepareContext(ctx, `
INSERT INTO calibration_history (symbol_id, cal_date, factor, base_value, source)
SELECT id, ?, ?, ?, ? FROM symbols WHERE symbol = ?
ON DUPLICATE KEY UPDATE
  factor = VALUES(factor),
  base_value = VALUES(base_value),
  source = VALUES(source)`)
	if err != nil {
		return err
	}
	defer stmt.Close()

	for _, cal := range calibrations {
		if cal.Symbol == "" || cal.Date == "" || cal.Factor <= 0 {
			continue
		}
		source := cal.Source
		if source == "" {
			source = "auto_daily"
		}
		if _, err := stmt.ExecContext(ctx, cal.Date, cal.Factor, nullableFloat(cal.BaseValue), source, cal.Symbol); err != nil {
			return err
		}
	}
	return nil
}

func loadFundPairs(ctx context.Context, db *sql.DB, data *domain.ValuationData) error {
	rows, err := db.QueryContext(ctx, `
SELECT fs.symbol, ps.symbol, fp.pair_type, COALESCE(fp.note, '')
FROM fund_pairs fp
JOIN symbols fs ON fs.id = fp.fund_symbol_id
JOIN symbols ps ON ps.id = fp.pair_symbol_id
WHERE fp.active = 1`)
	if err != nil {
		return err
	}
	defer rows.Close()

	for rows.Next() {
		var pair domain.FundPair
		if err := rows.Scan(&pair.FundSymbol, &pair.PairSymbol, &pair.PairType, &pair.Note); err != nil {
			return err
		}
		data.FundPairs[pair.FundSymbol] = append(data.FundPairs[pair.FundSymbol], pair)
	}
	return rows.Err()
}

func loadPositions(ctx context.Context, db *sql.DB, data *domain.ValuationData) error {
	rows, err := db.QueryContext(ctx, `
SELECT s.symbol, fp.position_ratio
FROM fund_positions fp
JOIN symbols s ON s.id = fp.fund_symbol_id`)
	if err != nil {
		return err
	}
	defer rows.Close()

	for rows.Next() {
		var symbol string
		var ratio float64
		if err := rows.Scan(&symbol, &ratio); err != nil {
			return err
		}
		data.Positions[symbol] = ratio
	}
	return rows.Err()
}

func loadLatestCalibrations(ctx context.Context, db *sql.DB, data *domain.ValuationData) error {
	rows, err := db.QueryContext(ctx, `
SELECT s.symbol, DATE_FORMAT(c.cal_date, '%Y-%m-%d'), c.factor, COALESCE(c.base_value, 0), c.source
FROM calibration_history c
JOIN symbols s ON s.id = c.symbol_id
JOIN (
  SELECT symbol_id, MAX(cal_date) AS cal_date
  FROM calibration_history
  GROUP BY symbol_id
) latest ON latest.symbol_id = c.symbol_id AND latest.cal_date = c.cal_date`)
	if err != nil {
		return err
	}
	defer rows.Close()

	for rows.Next() {
		var cal domain.Calibration
		if err := rows.Scan(&cal.Symbol, &cal.Date, &cal.Factor, &cal.BaseValue, &cal.Source); err != nil {
			return err
		}
		data.LatestCalibrations[cal.Symbol] = cal
	}
	return rows.Err()
}

func loadLatestNetValues(ctx context.Context, db *sql.DB, data *domain.ValuationData) error {
	rows, err := db.QueryContext(ctx, `
SELECT s.symbol, DATE_FORMAT(nv.nav_date, '%Y-%m-%d'), nv.nav, nv.source, nv.confidence
FROM net_values nv
JOIN symbols s ON s.id = nv.symbol_id
JOIN (
  SELECT symbol_id, MAX(nav_date) AS nav_date
  FROM net_values
  GROUP BY symbol_id
) latest ON latest.symbol_id = nv.symbol_id AND latest.nav_date = nv.nav_date`)
	if err != nil {
		return err
	}
	defer rows.Close()

	for rows.Next() {
		var nav domain.NetValue
		if err := rows.Scan(&nav.Symbol, &nav.Date, &nav.NAV, &nav.Source, &nav.Confidence); err != nil {
			return err
		}
		data.LatestNetValues[nav.Symbol] = nav
		putNetValueByDate(data, nav)
	}
	return rows.Err()
}

func loadRecentNetValues(ctx context.Context, db *sql.DB, data *domain.ValuationData) error {
	rows, err := db.QueryContext(ctx, `
SELECT s.symbol, DATE_FORMAT(nv.nav_date, '%Y-%m-%d'), nv.nav, nv.source, nv.confidence
FROM net_values nv
JOIN symbols s ON s.id = nv.symbol_id
WHERE nv.nav_date >= DATE_SUB(CURDATE(), INTERVAL 730 DAY)`)
	if err != nil {
		return err
	}
	defer rows.Close()

	for rows.Next() {
		var nav domain.NetValue
		if err := rows.Scan(&nav.Symbol, &nav.Date, &nav.NAV, &nav.Source, &nav.Confidence); err != nil {
			return err
		}
		putNetValueByDate(data, nav)
	}
	return rows.Err()
}

func putNetValueByDate(data *domain.ValuationData, nav domain.NetValue) {
	if data.NetValuesByDate[nav.Symbol] == nil {
		data.NetValuesByDate[nav.Symbol] = map[string]domain.NetValue{}
	}
	data.NetValuesByDate[nav.Symbol][nav.Date] = nav
}

func loadCurrentHoldingDates(ctx context.Context, db *sql.DB, data *domain.ValuationData) error {
	rows, err := db.QueryContext(ctx, `
SELECT s.symbol, DATE_FORMAT(hd.holding_date, '%Y-%m-%d'), hd.source
FROM fund_holding_dates hd
JOIN symbols s ON s.id = hd.fund_symbol_id
WHERE hd.is_current = 1`)
	if err != nil {
		return err
	}
	defer rows.Close()

	for rows.Next() {
		var hd domain.HoldingDate
		if err := rows.Scan(&hd.FundSymbol, &hd.Date, &hd.Source); err != nil {
			return err
		}
		data.CurrentHoldingDates[hd.FundSymbol] = hd
	}
	return rows.Err()
}

func loadHoldings(ctx context.Context, db *sql.DB, data *domain.ValuationData) error {
	rows, err := db.QueryContext(ctx, `
SELECT fs.symbol, DATE_FORMAT(fh.holding_date, '%Y-%m-%d'), hs.symbol,
       COALESCE(fh.holding_name, ''), COALESCE(fh.ratio, 0), COALESCE(fh.fx_adjust, 0), COALESCE(fh.currency, ''), fh.source
FROM fund_holdings fh
JOIN symbols fs ON fs.id = fh.fund_symbol_id
JOIN symbols hs ON hs.id = fh.holding_symbol_id`)
	if err != nil {
		return err
	}
	defer rows.Close()

	for rows.Next() {
		var holding domain.Holding
		if err := rows.Scan(&holding.FundSymbol, &holding.HoldingDate, &holding.HoldingSymbol, &holding.HoldingName, &holding.Ratio, &holding.FXAdjust, &holding.Currency, &holding.Source); err != nil {
			return err
		}
		data.Holdings[holding.FundSymbol] = append(data.Holdings[holding.FundSymbol], holding)
	}
	return rows.Err()
}

func loadRecentDailyPrices(ctx context.Context, db *sql.DB, data *domain.ValuationData) error {
	rows, err := db.QueryContext(ctx, `
SELECT s.symbol, DATE_FORMAT(dp.trade_date, '%Y-%m-%d'), dp.close_price, COALESCE(dp.adj_close, dp.close_price), dp.source
FROM daily_prices dp
JOIN symbols s ON s.id = dp.symbol_id
WHERE dp.trade_date >= DATE_SUB(CURDATE(), INTERVAL 730 DAY)`)
	if err != nil {
		return err
	}
	defer rows.Close()

	for rows.Next() {
		var price domain.DailyPrice
		if err := rows.Scan(&price.Symbol, &price.Date, &price.Close, &price.AdjClose, &price.Source); err != nil {
			return err
		}
		if data.DailyPricesByDate[price.Symbol] == nil {
			data.DailyPricesByDate[price.Symbol] = map[string]domain.DailyPrice{}
		}
		data.DailyPricesByDate[price.Symbol][price.Date] = price
	}
	return rows.Err()
}

func loadRecentValuationAnchorPrices(ctx context.Context, db *sql.DB, data *domain.ValuationData) error {
	rows, err := db.QueryContext(ctx, `
SELECT fs.symbol, DATE_FORMAT(vap.anchor_date, '%Y-%m-%d'), vap.anchor_key, rs.symbol,
       vap.target_at_utc, COALESCE(vap.target_timezone, ''), vap.observed_at_utc,
       vap.price, vap.weight, vap.source, COALESCE(vap.capture_status, '')
FROM valuation_anchor_prices vap
JOIN symbols fs ON fs.id = vap.fund_symbol_id
JOIN symbols rs ON rs.id = vap.reference_symbol_id
WHERE vap.anchor_date >= DATE_SUB(CURDATE(), INTERVAL 730 DAY)`)
	if err != nil {
		return err
	}
	defer rows.Close()

	for rows.Next() {
		var price domain.ValuationAnchorPrice
		var observedAt sql.NullTime
		if err := rows.Scan(
			&price.FundSymbol,
			&price.AnchorDate,
			&price.AnchorKey,
			&price.ReferenceSymbol,
			&price.TargetAt,
			&price.TargetTimezone,
			&observedAt,
			&price.Price,
			&price.Weight,
			&price.Source,
			&price.CaptureStatus,
		); err != nil {
			return err
		}
		price.TargetAt = price.TargetAt.UTC()
		if observedAt.Valid {
			price.ObservedAt = observedAt.Time.UTC()
		}
		putValuationAnchorPrice(data, price)
	}
	return rows.Err()
}

func putValuationAnchorPrice(data *domain.ValuationData, price domain.ValuationAnchorPrice) {
	key := domain.ValuationAnchorSetKey(price.FundSymbol, price.AnchorDate, price.ReferenceSymbol)
	set := data.ValuationAnchors[key]
	if set.FundSymbol == "" {
		set.FundSymbol = price.FundSymbol
		set.AnchorDate = price.AnchorDate
		set.ReferenceSymbol = price.ReferenceSymbol
		if strategy, ok := domain.WeightedAnchorStrategyForFund(price.FundSymbol); ok {
			for _, point := range strategy.Points {
				set.RequiredWeight += point.Weight
			}
		}
	}
	set.Points = append(set.Points, price)
	weighted := 0.0
	coverage := 0.0
	for _, point := range set.Points {
		if point.Price <= 0 || point.Weight <= 0 {
			continue
		}
		weighted += point.Price * point.Weight
		coverage += point.Weight
	}
	if coverage > 0 {
		set.WeightedPrice = weighted / coverage
		set.CoverageWeight = coverage
	}
	if set.RequiredWeight <= 0 {
		set.RequiredWeight = coverage
	}
	data.ValuationAnchors[key] = set
}

func loadLatestFXQuotes(ctx context.Context, db *sql.DB, data *domain.ValuationData) error {
	rows, err := db.QueryContext(ctx, `SELECT pair, rate FROM latest_fx_quotes`)
	if err != nil {
		return err
	}
	defer rows.Close()

	for rows.Next() {
		var fx domain.FXQuote
		if err := rows.Scan(&fx.Pair, &fx.Rate); err != nil {
			return err
		}
		data.FXQuotes[fx.Pair] = fx
	}
	return rows.Err()
}

func loadRecentFXCentralParity(ctx context.Context, db *sql.DB, data *domain.ValuationData) error {
	rows, err := db.QueryContext(ctx, `
SELECT pair, DATE_FORMAT(rate_date, '%Y-%m-%d'), rate, source
FROM fx_central_parity
WHERE rate_date >= DATE_SUB(CURDATE(), INTERVAL 730 DAY)`)
	if err != nil {
		return err
	}
	defer rows.Close()

	for rows.Next() {
		var fx domain.FXCentralParity
		if err := rows.Scan(&fx.Pair, &fx.Date, &fx.Rate, &fx.Source); err != nil {
			return err
		}
		putFXCentralParity(data, fx)
	}
	return rows.Err()
}

func putFXCentralParity(data *domain.ValuationData, fx domain.FXCentralParity) {
	if data.FXCentralParity[fx.Pair] == nil {
		data.FXCentralParity[fx.Pair] = map[string]domain.FXCentralParity{}
	}
	data.FXCentralParity[fx.Pair][fx.Date] = fx
}

func nullableFloat(value float64) any {
	if value == 0 {
		return nil
	}
	return value
}

func nullableLevelsJSON(levels []domain.Level) any {
	if len(levels) == 0 {
		return nil
	}
	body, err := json.Marshal(levels)
	if err != nil {
		return nil
	}
	return string(body)
}

func parseStoredLevels(value string) []domain.Level {
	value = strings.TrimSpace(value)
	if value == "" {
		return nil
	}
	var levels []domain.Level
	if err := json.Unmarshal([]byte(value), &levels); err != nil {
		return nil
	}
	return levels
}

func nullableDate(value string) any {
	if value == "" {
		return nil
	}
	if _, err := time.Parse("2006-01-02", value); err != nil {
		return nil
	}
	return value
}

func nullableTime(value string) any {
	if value == "" {
		return nil
	}
	if len(value) == len("15:04") {
		value += ":00"
	}
	if _, err := time.Parse("15:04:05", value); err != nil {
		return nil
	}
	return value
}

func sqlTimeUTC(value time.Time) string {
	return value.UTC().Format("2006-01-02 15:04:05.000")
}

func (r *Repository) ensureColumn(ctx context.Context, table string, column string, alterClause string) error {
	table = strings.TrimSpace(table)
	column = strings.TrimSpace(column)
	alterClause = strings.TrimSpace(alterClause)
	if table == "" || column == "" || alterClause == "" {
		return nil
	}
	var count int
	if err := r.db.QueryRowContext(ctx, `
SELECT COUNT(*)
FROM information_schema.columns
WHERE table_schema = DATABASE()
  AND table_name = ?
  AND column_name = ?`,
		table,
		column,
	).Scan(&count); err != nil {
		return err
	}
	if count > 0 {
		return nil
	}
	_, err := r.db.ExecContext(ctx, "ALTER TABLE "+table+" "+alterClause)
	return err
}

func nullableFloatPointer(value *float64) any {
	if value == nil {
		return nil
	}
	return *value
}

func nullableFloat64ToPointer(value sql.NullFloat64) *float64 {
	if !value.Valid {
		return nil
	}
	out := value.Float64
	return &out
}

func nullableZeroFloat(value float64) any {
	if value == 0 {
		return nil
	}
	return value
}

func nullableString(value string) any {
	value = strings.TrimSpace(value)
	if value == "" {
		return nil
	}
	return value
}

func nullableSQLTimeUTC(value time.Time) any {
	if value.IsZero() {
		return nil
	}
	return sqlTimeUTC(value)
}

func inferSinaSymbol(symbol string) string {
	symbol = strings.TrimSpace(symbol)
	if symbol == "" {
		return ""
	}
	lower := strings.ToLower(symbol)
	switch {
	case strings.HasPrefix(lower, "fx_"):
		return lower
	case strings.HasPrefix(lower, "hf_"):
		return "hf_" + strings.ToUpper(symbol[3:])
	case strings.HasPrefix(lower, "nf_"):
		return "nf_" + strings.ToUpper(symbol[3:])
	case strings.HasPrefix(lower, "znb_"):
		return "znb_" + strings.ToUpper(symbol[4:])
	case strings.HasPrefix(lower, "b_"):
		return "b_" + strings.ToUpper(symbol[2:])
	case strings.HasPrefix(lower, "rt_hk"):
		return "rt_hk" + symbol[5:]
	case strings.HasPrefix(lower, "gb_"):
		return "gb_" + strings.ToLower(symbol[3:])
	}
	if regexp.MustCompile(`^\d{5}$`).MatchString(symbol) {
		return "rt_hk" + symbol
	}
	if strings.HasPrefix(symbol, "^") && len(symbol) > 1 {
		return "b_" + strings.ToUpper(symbol[1:])
	}
	if regexp.MustCompile(`^[A-Z][A-Z0-9.]{0,9}$`).MatchString(symbol) {
		return "gb_" + strings.ToLower(symbol)
	}
	if len(symbol) < 3 {
		return ""
	}
	prefix := strings.ToUpper(symbol[:2])
	code := symbol[2:]
	if prefix == "SH" || prefix == "SZ" || prefix == "BJ" {
		return strings.ToLower(prefix) + code
	}
	return ""
}

func inferMarket(symbol string) string {
	if len(symbol) < 2 {
		return "unknown"
	}
	lower := strings.ToLower(symbol)
	if regexp.MustCompile(`^\d{5}$`).MatchString(symbol) {
		return "hk"
	}
	if strings.HasPrefix(lower, "fx_") {
		return "fx"
	}
	if strings.HasPrefix(lower, "hf_") {
		return "global_future"
	}
	if strings.HasPrefix(lower, "nf_") {
		return "cn_future"
	}
	if strings.HasPrefix(lower, "znb_") || strings.HasPrefix(lower, "b_") || strings.HasPrefix(symbol, "^") {
		return "global_index"
	}
	if strings.HasPrefix(lower, "rt_hk") {
		return "hk"
	}
	if strings.HasPrefix(lower, "gb_") {
		return "us"
	}
	if regexp.MustCompile(`^[A-Z][A-Z0-9.]{0,9}$`).MatchString(symbol) {
		return "us"
	}
	switch strings.ToUpper(symbol[:2]) {
	case "SH", "SZ", "BJ":
		return "cn"
	default:
		return "external"
	}
}

func inferAssetType(symbol string) string {
	lower := strings.ToLower(symbol)
	if strings.HasPrefix(lower, "fx_") {
		return "fx"
	}
	if strings.HasPrefix(lower, "hf_") || strings.HasPrefix(lower, "nf_") {
		return "future"
	}
	if strings.HasPrefix(lower, "znb_") || strings.HasPrefix(lower, "b_") || strings.HasPrefix(symbol, "^") {
		return "index"
	}
	if strings.HasPrefix(lower, "rt_hk") {
		return "stock_hk"
	}
	if strings.HasPrefix(lower, "gb_") {
		return "stock_us"
	}
	if regexp.MustCompile(`^\d{5}$`).MatchString(symbol) {
		return "stock_hk"
	}
	if regexp.MustCompile(`^[A-Z][A-Z0-9.]{0,9}$`).MatchString(symbol) {
		return "stock_us"
	}
	return "fund_a"
}

func inferCurrency(symbol string) string {
	switch inferMarket(symbol) {
	case "cn":
		return "CNY"
	case "hk":
		return "HKD"
	case "us":
		return "USD"
	case "global_future":
		return "USD"
	}
	return ""
}

func inferTimezone(symbol string) string {
	switch inferMarket(symbol) {
	case "cn":
		return "Asia/Shanghai"
	case "hk":
		return "Asia/Hong_Kong"
	case "us":
		return "America/New_York"
	case "global_future":
		return "America/New_York"
	case "global_index":
		return "Asia/Shanghai"
	}
	return ""
}
