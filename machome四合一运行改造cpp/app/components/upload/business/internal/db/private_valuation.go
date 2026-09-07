package db

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"sort"
	"time"

	"newnavnav/internal/domain"
	"newnavnav/internal/privatevaluation"
)

func (r *Repository) EnsurePrivateValuationSchema(ctx context.Context) error {
	if r == nil || r.db == nil {
		return fmt.Errorf("database repository is unavailable")
	}
	statements := []string{
		`CREATE TABLE IF NOT EXISTS private_valuation_inputs (
  symbol VARCHAR(32) NOT NULL,
  schema_version INT NOT NULL,
  model_version VARCHAR(96) NOT NULL,
  pcf_trading_day DATE NOT NULL,
  fx_trading_day DATE NOT NULL,
  fx_quote_time TIME NOT NULL,
  generated_at DATETIME(3) NOT NULL,
  received_at DATETIME(3) NOT NULL,
  payload_json JSON NOT NULL,
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (symbol),
  KEY idx_private_inputs_dates (pcf_trading_day, fx_trading_day, fx_quote_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
		`CREATE TABLE IF NOT EXISTS private_valuation_minute_history (
  symbol VARCHAR(32) NOT NULL,
  snapshot_minute DATETIME NOT NULL,
  trading_day DATE NOT NULL,
  market_price DECIMAL(18, 6) NOT NULL,
  basket_bid_nav DECIMAL(18, 10) NOT NULL,
  basket_ask_nav DECIMAL(18, 10) NOT NULL,
  buy_direction_premium_rate DECIMAL(16, 10) NOT NULL,
  sell_direction_premium_rate DECIMAL(16, 10) NOT NULL,
  pcf_trading_day DATE NULL,
  xop_equivalent_shares DECIMAL(24, 6) NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (symbol, snapshot_minute),
  KEY idx_private_minute_history_day (trading_day, symbol)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
		`CREATE TABLE IF NOT EXISTS private_india_final_nav_history (
  symbol VARCHAR(32) NOT NULL,
  trading_day DATE NOT NULL,
  base_nav_date DATE NOT NULL,
  base_nav DECIMAL(18, 10) NOT NULL,
  investment_ratio DECIMAL(16, 12) NOT NULL,
  static_ratio DECIMAL(16, 12) NOT NULL,
  base_anchor_price DECIMAL(18, 10) NOT NULL,
  target_anchor_price DECIMAL(18, 10) NOT NULL,
  base_fx DECIMAL(18, 10) NOT NULL,
  target_fx DECIMAL(18, 10) NOT NULL,
  final_estimate_nav DECIMAL(18, 10) NOT NULL,
  source VARCHAR(128) NOT NULL,
  generated_at DATETIME(3) NOT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (symbol, trading_day),
  KEY idx_private_india_final_nav_history_day (trading_day, symbol)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
		`CREATE TABLE IF NOT EXISTS private_india_nifty_bridge_review_history (
  symbol VARCHAR(32) NOT NULL,
  snapshot_minute DATETIME NOT NULL,
  trading_day DATE NOT NULL,
  state VARCHAR(48) NOT NULL,
  market_price DECIMAL(18, 6) NOT NULL,
  direct_nav_bid DECIMAL(18, 10) NOT NULL,
  direct_nav_ask DECIMAL(18, 10) NOT NULL,
  bridge_nav_bid DECIMAL(18, 10) NOT NULL,
  bridge_nav_ask DECIMAL(18, 10) NOT NULL,
  base_nav_date DATE NOT NULL,
  base_nav DECIMAL(18, 10) NOT NULL,
  investment_ratio DECIMAL(16, 12) NOT NULL,
  static_ratio DECIMAL(16, 12) NOT NULL,
  anchor_price DECIMAL(18, 10) NOT NULL,
  base_fx DECIMAL(18, 10) NOT NULL,
  current_fx DECIMAL(18, 10) NOT NULL,
  current_fx_trading_day DATE NOT NULL,
  direct_inda_bid DECIMAL(18, 10) NOT NULL,
  direct_inda_ask DECIMAL(18, 10) NOT NULL,
  direct_inda_observed_at DATETIME(3) NOT NULL,
  synthetic_inda_bid DECIMAL(18, 10) NOT NULL,
  synthetic_inda_ask DECIMAL(18, 10) NOT NULL,
  nifty_bid DECIMAL(20, 6) NOT NULL,
  nifty_ask DECIMAL(20, 6) NOT NULL,
  nifty_contract VARCHAR(64) NOT NULL,
  nifty_observed_at DATETIME(3) NOT NULL,
  inda_reference_bid DECIMAL(18, 10) NOT NULL,
  inda_reference_ask DECIMAL(18, 10) NOT NULL,
  inda_reference_contract VARCHAR(64) NOT NULL,
  inda_reference_observed_at DATETIME(3) NOT NULL,
  nifty_reference_bid DECIMAL(20, 6) NOT NULL,
  nifty_reference_ask DECIMAL(20, 6) NOT NULL,
  nifty_reference_contract VARCHAR(64) NOT NULL,
  nifty_reference_observed_at DATETIME(3) NOT NULL,
  reference_at DATETIME(3) NOT NULL,
  beta DECIMAL(16, 12) NOT NULL,
  selection_version VARCHAR(96) NOT NULL,
  roll_date DATE NULL,
  roll_captured_at DATETIME(3) NULL,
  roll_old_contract VARCHAR(64) NULL,
  roll_new_contract VARCHAR(64) NULL,
  roll_direction VARCHAR(32) NULL,
  roll_bid_factor DECIMAL(20, 12) NULL,
  roll_ask_factor DECIMAL(20, 12) NULL,
  roll_source VARCHAR(128) NULL,
  input_source VARCHAR(128) NOT NULL,
  input_generated_at DATETIME(3) NOT NULL,
  input_json JSON NOT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (symbol, snapshot_minute),
  KEY idx_private_india_nifty_bridge_review_day (symbol, trading_day, state)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
	}
	for _, statement := range statements {
		if _, err := r.db.ExecContext(ctx, statement); err != nil {
			return err
		}
	}
	// The minute-history table predates the two-model India valuation. Check
	// information_schema instead of relying on ADD COLUMN IF NOT EXISTS so the
	// migration also works on older MySQL-compatible deployments.
	for _, column := range []struct {
		name       string
		definition string
	}{
		{name: "nifty_bridge_bid_nav", definition: "DECIMAL(18, 10) NULL"},
		{name: "nifty_bridge_ask_nav", definition: "DECIMAL(18, 10) NULL"},
		{name: "nifty_bridge_selection_version", definition: "VARCHAR(96) NULL"},
		{name: "settlement_nav", definition: "DECIMAL(18, 10) NULL"},
		{name: "trading_nav", definition: "DECIMAL(18, 10) NULL"},
		{name: "active_contract", definition: "VARCHAR(16) NULL"},
		{name: "futures_price", definition: "DECIMAL(18, 6) NULL"},
		{name: "intraday_average", definition: "DECIMAL(18, 6) NULL"},
	} {
		if err := r.ensurePrivateMinuteHistoryColumn(ctx, column.name, column.definition); err != nil {
			return err
		}
	}
	return nil
}

func (r *Repository) ensurePrivateMinuteHistoryColumn(ctx context.Context, name, definition string) error {
	var count int
	if err := r.db.QueryRowContext(ctx, `
SELECT COUNT(*)
FROM information_schema.columns
WHERE table_schema = DATABASE()
  AND table_name = 'private_valuation_minute_history'
  AND column_name = ?`, name).Scan(&count); err != nil {
		return err
	}
	if count > 0 {
		return nil
	}
	_, err := r.db.ExecContext(ctx, "ALTER TABLE private_valuation_minute_history ADD COLUMN "+name+" "+definition)
	return err
}

func (r *Repository) UpsertPrivateValuationInput(ctx context.Context, input privatevaluation.Input) error {
	return upsertPrivateValuationInput(ctx, r.db, input)
}

func (r *Repository) UpsertPrivateValuationInputs(ctx context.Context, inputs []privatevaluation.Input) error {
	if r == nil || r.db == nil {
		return fmt.Errorf("database repository is unavailable")
	}
	tx, err := r.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback() }()
	for _, input := range inputs {
		if err := upsertPrivateValuationInput(ctx, tx, input); err != nil {
			return err
		}
	}
	return tx.Commit()
}

func upsertPrivateValuationInput(ctx context.Context, exec privateValuationExecer, input privatevaluation.Input) error {
	payload, err := json.Marshal(input)
	if err != nil {
		return err
	}
	fxTradingDay, fxQuoteTime := privateInputFXMetadata(input)
	_, err = exec.ExecContext(ctx, `
INSERT INTO private_valuation_inputs (
  symbol, schema_version, model_version, pcf_trading_day, fx_trading_day,
  fx_quote_time, generated_at, received_at, payload_json
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON DUPLICATE KEY UPDATE
  schema_version = VALUES(schema_version),
  model_version = VALUES(model_version),
  pcf_trading_day = VALUES(pcf_trading_day),
  fx_trading_day = VALUES(fx_trading_day),
  fx_quote_time = VALUES(fx_quote_time),
  generated_at = VALUES(generated_at),
  received_at = VALUES(received_at),
  payload_json = VALUES(payload_json)`,
		input.Symbol,
		input.SchemaVersion,
		input.ModelVersion,
		input.ValuationAnchorDate(),
		fxTradingDay,
		fxQuoteTime+":00",
		input.GeneratedAt,
		input.ReceivedAt,
		payload,
	)
	return err
}

func privateInputFXMetadata(input privatevaluation.Input) (string, string) {
	if input.India != nil {
		return input.India.CurrentFX.TradingDay, input.India.CurrentFX.QuoteTime
	}
	if input.LOF != nil {
		return input.LOF.CurrentFX.TradingDay, input.LOF.CurrentFX.QuoteTime
	}
	if input.Silver != nil {
		observed := input.Silver.ObservedAt.In(privateHistoryLocation())
		return input.Silver.TradingDay, observed.Format("15:04")
	}
	if input.FX.TradingDay != "" && input.FX.QuoteTime != "" {
		return input.FX.TradingDay, input.FX.QuoteTime
	}
	if len(input.FXRates) > 0 {
		return input.FXRates[0].TradingDay, input.FXRates[0].QuoteTime
	}
	return "", ""
}

func (r *Repository) LoadPrivateValuationInputs(ctx context.Context) ([]privatevaluation.Input, error) {
	rows, err := r.db.QueryContext(ctx, `SELECT payload_json FROM private_valuation_inputs ORDER BY symbol`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	inputs := make([]privatevaluation.Input, 0)
	for rows.Next() {
		var payload []byte
		if err := rows.Scan(&payload); err != nil {
			return nil, err
		}
		var input privatevaluation.Input
		if err := json.Unmarshal(payload, &input); err != nil {
			return nil, err
		}
		inputs = append(inputs, input)
	}
	return inputs, rows.Err()
}

func (r *Repository) UpsertPrivateValuationSnapshot(ctx context.Context, snapshot privatevaluation.Snapshot) error {
	return upsertPrivateValuationSnapshot(ctx, r.db, snapshot)
}

type privateValuationExecer interface {
	ExecContext(context.Context, string, ...any) (sql.Result, error)
}

func upsertPrivateValuationSnapshot(ctx context.Context, exec privateValuationExecer, snapshot privatevaluation.Snapshot) error {
	point, ok := privateMinuteHistoryPoint(snapshot)
	if !ok {
		return nil
	}
	minute := point.Minute.In(privateHistoryLocation()).Truncate(time.Minute)
	var xopEquivalentShares any
	if point.XOPEquivalentShares != nil {
		xopEquivalentShares = *point.XOPEquivalentShares
	}
	var niftyBridgeBidNAV, niftyBridgeAskNAV, niftyBridgeSelectionVersion any
	if point.NiftyBridgeBidNAV != nil {
		niftyBridgeBidNAV = *point.NiftyBridgeBidNAV
	}
	if point.NiftyBridgeAskNAV != nil {
		niftyBridgeAskNAV = *point.NiftyBridgeAskNAV
	}
	if point.NiftyBridgeSelectionVersion != "" {
		niftyBridgeSelectionVersion = point.NiftyBridgeSelectionVersion
	}
	var settlementNAV, tradingNAV, activeContract, futuresPrice, intradayAverage any
	if point.SettlementNAV != nil {
		settlementNAV = *point.SettlementNAV
	}
	if point.TradingNAV != nil {
		tradingNAV = *point.TradingNAV
	}
	if point.ActiveContract != "" {
		activeContract = point.ActiveContract
	}
	if point.FuturesPrice != nil {
		futuresPrice = *point.FuturesPrice
	}
	if point.IntradayAverage != nil {
		intradayAverage = *point.IntradayAverage
	}
	if _, err := exec.ExecContext(ctx, `
INSERT INTO private_valuation_minute_history (
  symbol, snapshot_minute, trading_day, market_price, basket_bid_nav,
  basket_ask_nav, buy_direction_premium_rate, sell_direction_premium_rate,
  pcf_trading_day, xop_equivalent_shares, nifty_bridge_bid_nav,
  nifty_bridge_ask_nav, nifty_bridge_selection_version, settlement_nav,
  trading_nav, active_contract, futures_price, intraday_average
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULLIF(?, ''), ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON DUPLICATE KEY UPDATE
  trading_day = VALUES(trading_day),
  market_price = VALUES(market_price),
  basket_bid_nav = VALUES(basket_bid_nav),
  basket_ask_nav = VALUES(basket_ask_nav),
  buy_direction_premium_rate = VALUES(buy_direction_premium_rate),
  sell_direction_premium_rate = VALUES(sell_direction_premium_rate),
  pcf_trading_day = VALUES(pcf_trading_day),
  xop_equivalent_shares = VALUES(xop_equivalent_shares),
  nifty_bridge_bid_nav = VALUES(nifty_bridge_bid_nav),
	  nifty_bridge_ask_nav = VALUES(nifty_bridge_ask_nav),
	  nifty_bridge_selection_version = VALUES(nifty_bridge_selection_version),
  settlement_nav = VALUES(settlement_nav),
  trading_nav = VALUES(trading_nav),
  active_contract = VALUES(active_contract),
  futures_price = VALUES(futures_price),
  intraday_average = VALUES(intraday_average)`,
		snapshot.Symbol,
		minute,
		minute.Format("2006-01-02"),
		point.MarketPrice,
		point.BasketBidNAV,
		point.BasketAskNAV,
		point.BuyDirectionPremiumRate,
		point.SellDirectionPremiumRate,
		point.PCFTradingDay,
		xopEquivalentShares,
		niftyBridgeBidNAV,
		niftyBridgeAskNAV,
		niftyBridgeSelectionVersion,
		settlementNAV,
		tradingNAV,
		activeContract,
		futuresPrice,
		intradayAverage,
	); err != nil {
		return err
	}
	return upsertPrivateIndiaNiftyBridgeReviewHistory(ctx, exec, snapshot)
}

func upsertPrivateIndiaNiftyBridgeReviewHistory(ctx context.Context, exec privateValuationExecer, snapshot privatevaluation.Snapshot) error {
	point, ok := privatevaluation.IndiaNiftyBridgeHistoryPointFromSnapshot(snapshot)
	if !ok {
		return nil
	}
	inputJSON, err := json.Marshal(point.Input)
	if err != nil {
		return err
	}
	audit := point.Audit
	var rollDate, rollCapturedAt, rollOldContract, rollNewContract, rollDirection any
	var rollBidFactor, rollAskFactor, rollSource any
	if roll := audit.RollAdjustment; roll != nil {
		rollDate = roll.RollDate
		rollCapturedAt = roll.CapturedAt.UTC()
		rollOldContract = roll.OldContract
		rollNewContract = roll.NewContract
		rollDirection = roll.Direction
		rollBidFactor = roll.BidFactor
		rollAskFactor = roll.AskFactor
		rollSource = roll.Source
	}
	_, err = exec.ExecContext(ctx, `
INSERT INTO private_india_nifty_bridge_review_history (
  symbol, snapshot_minute, trading_day, state, market_price,
  direct_nav_bid, direct_nav_ask, bridge_nav_bid, bridge_nav_ask,
  base_nav_date, base_nav, investment_ratio, static_ratio, anchor_price,
  base_fx, current_fx, current_fx_trading_day,
  direct_inda_bid, direct_inda_ask, direct_inda_observed_at,
  synthetic_inda_bid, synthetic_inda_ask,
  nifty_bid, nifty_ask, nifty_contract, nifty_observed_at,
  inda_reference_bid, inda_reference_ask, inda_reference_contract,
  inda_reference_observed_at, nifty_reference_bid, nifty_reference_ask,
  nifty_reference_contract, nifty_reference_observed_at, reference_at,
  beta, selection_version, roll_date, roll_captured_at, roll_old_contract,
  roll_new_contract, roll_direction, roll_bid_factor, roll_ask_factor,
  roll_source, input_source, input_generated_at, input_json
) VALUES (
  ?, ?, ?, ?, ?, ?, ?, ?, ?, NULLIF(?, ''), ?, ?, ?, ?, ?, ?, NULLIF(?, ''),
  ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
  NULLIF(?, ''), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
)
ON DUPLICATE KEY UPDATE
  trading_day = VALUES(trading_day), state = VALUES(state), market_price = VALUES(market_price),
  direct_nav_bid = VALUES(direct_nav_bid), direct_nav_ask = VALUES(direct_nav_ask),
  bridge_nav_bid = VALUES(bridge_nav_bid), bridge_nav_ask = VALUES(bridge_nav_ask),
  base_nav_date = VALUES(base_nav_date), base_nav = VALUES(base_nav),
  investment_ratio = VALUES(investment_ratio), static_ratio = VALUES(static_ratio),
  anchor_price = VALUES(anchor_price), base_fx = VALUES(base_fx), current_fx = VALUES(current_fx),
  current_fx_trading_day = VALUES(current_fx_trading_day),
  direct_inda_bid = VALUES(direct_inda_bid), direct_inda_ask = VALUES(direct_inda_ask),
  direct_inda_observed_at = VALUES(direct_inda_observed_at),
  synthetic_inda_bid = VALUES(synthetic_inda_bid), synthetic_inda_ask = VALUES(synthetic_inda_ask),
  nifty_bid = VALUES(nifty_bid), nifty_ask = VALUES(nifty_ask),
  nifty_contract = VALUES(nifty_contract), nifty_observed_at = VALUES(nifty_observed_at),
  inda_reference_bid = VALUES(inda_reference_bid), inda_reference_ask = VALUES(inda_reference_ask),
  inda_reference_contract = VALUES(inda_reference_contract),
  inda_reference_observed_at = VALUES(inda_reference_observed_at),
  nifty_reference_bid = VALUES(nifty_reference_bid), nifty_reference_ask = VALUES(nifty_reference_ask),
  nifty_reference_contract = VALUES(nifty_reference_contract),
  nifty_reference_observed_at = VALUES(nifty_reference_observed_at),
  reference_at = VALUES(reference_at), beta = VALUES(beta), selection_version = VALUES(selection_version),
  roll_date = VALUES(roll_date), roll_captured_at = VALUES(roll_captured_at),
  roll_old_contract = VALUES(roll_old_contract), roll_new_contract = VALUES(roll_new_contract),
  roll_direction = VALUES(roll_direction), roll_bid_factor = VALUES(roll_bid_factor),
  roll_ask_factor = VALUES(roll_ask_factor), roll_source = VALUES(roll_source),
  input_source = VALUES(input_source), input_generated_at = VALUES(input_generated_at),
  input_json = VALUES(input_json)`,
		point.Symbol,
		// DATETIME has no timezone. Pass the Shanghai wall-clock string
		// explicitly so the MySQL driver cannot normalize the instant to UTC
		// and turn a 09:35 checkpoint into 01:35 on disk.
		privateIndiaNiftyBridgeSnapshotMinute(point.Minute),
		point.Minute.Format("2006-01-02"),
		point.RollStatus,
		point.MarketPrice,
		point.DirectBidNAV,
		point.DirectAskNAV,
		point.BridgeBidNAV,
		point.BridgeAskNAV,
		audit.BaseNAVDate,
		audit.BaseNAV,
		audit.InvestmentRatio,
		audit.StaticRatio,
		audit.BaseAnchorPrice,
		audit.BaseFX,
		audit.CurrentFX,
		audit.CurrentFXTradingDay,
		audit.DirectINDABid,
		audit.DirectINDAAsk,
		audit.DirectINDAObservedAt.UTC(),
		audit.SyntheticINDABid,
		audit.SyntheticINDAAsk,
		audit.CurrentNiftyBid,
		audit.CurrentNiftyAsk,
		audit.CurrentNiftyContract,
		audit.CurrentNiftyObservedAt.UTC(),
		audit.ReferenceINDABid,
		audit.ReferenceINDAAsk,
		audit.ReferenceINDAContract,
		audit.ReferenceINDAObservedAt.UTC(),
		audit.ReferenceNiftyBid,
		audit.ReferenceNiftyAsk,
		audit.ReferenceNiftyContract,
		audit.ReferenceNiftyObservedAt.UTC(),
		audit.ReferenceAt.UTC(),
		audit.Beta,
		audit.ContractSelectionVersion,
		rollDate,
		rollCapturedAt,
		rollOldContract,
		rollNewContract,
		rollDirection,
		rollBidFactor,
		rollAskFactor,
		rollSource,
		audit.InputSource,
		audit.InputGeneratedAt.UTC(),
		inputJSON,
	)
	return err
}

func (r *Repository) ReplacePrivateValuationSnapshots(
	ctx context.Context, symbol string, day time.Time, snapshots []privatevaluation.Snapshot,
) error {
	if r == nil || r.db == nil {
		return fmt.Errorf("database repository is unavailable")
	}
	tx, err := r.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	dayKey := day.In(privateHistoryLocation()).Format("2006-01-02")
	hasValidNiftyBridge := false
	for _, snapshot := range snapshots {
		if snapshot.Symbol != symbol || snapshot.AsOf.In(privateHistoryLocation()).Format("2006-01-02") != dayKey {
			return fmt.Errorf("replacement snapshot symbol or day mismatch")
		}
		if _, ok := privatevaluation.IndiaNiftyBridgeHistoryPointFromSnapshot(snapshot); ok {
			hasValidNiftyBridge = true
		}
	}
	if _, err := tx.ExecContext(ctx, `
DELETE FROM private_valuation_minute_history
WHERE symbol = ? AND trading_day = ?`, symbol, dayKey); err != nil {
		return err
	}
	// An old audited bridge day is replaced only by a batch that contains at
	// least one valid bridge observation. Direct-only or invalid bridge imports
	// must not erase the independently auditable history.
	if hasValidNiftyBridge {
		if _, err := tx.ExecContext(ctx, `
DELETE FROM private_india_nifty_bridge_review_history
WHERE symbol = ? AND trading_day = ?`, symbol, dayKey); err != nil {
			return err
		}
	}
	for _, snapshot := range snapshots {
		if err := upsertPrivateValuationSnapshot(ctx, tx, snapshot); err != nil {
			return err
		}
	}
	return tx.Commit()
}

func (r *Repository) LoadPrivateValuationMinuteHistory(ctx context.Context, symbol string, days int) ([]privatevaluation.MinuteHistoryPoint, error) {
	if r == nil || r.db == nil {
		return nil, fmt.Errorf("database repository is unavailable")
	}
	days, err := privatevaluation.NormalizeMinuteHistoryDays(days)
	if err != nil {
		return nil, err
	}
	const maxSnapshotsPerDay = 500
	rows, err := r.db.QueryContext(ctx, `
SELECT snapshot_minute, market_price, basket_bid_nav, basket_ask_nav,
       buy_direction_premium_rate, sell_direction_premium_rate,
       COALESCE(DATE_FORMAT(pcf_trading_day, '%Y-%m-%d'), ''),
       xop_equivalent_shares, nifty_bridge_bid_nav, nifty_bridge_ask_nav,
       nifty_bridge_selection_version, settlement_nav, trading_nav,
       active_contract, futures_price, intraday_average
FROM private_valuation_minute_history
WHERE symbol = ?
ORDER BY snapshot_minute DESC
LIMIT ?`, symbol, days*maxSnapshotsPerDay)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	selectedDays := make(map[string]struct{}, days)
	points := make([]privatevaluation.MinuteHistoryPoint, 0)
	for rows.Next() {
		point, err := scanPrivateMinuteHistoryPoint(rows)
		if err != nil {
			return nil, err
		}
		if !privatevaluation.IsMinuteHistoryTradingSessionForSymbol(symbol, point.Minute) {
			continue
		}
		dateKey := point.Minute.In(privateHistoryLocation()).Format("2006-01-02")
		if _, ok := selectedDays[dateKey]; !ok {
			if len(selectedDays) >= days {
				break
			}
			selectedDays[dateKey] = struct{}{}
		}
		points = append(points, point)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	sort.Slice(points, func(i, j int) bool { return points[i].Minute.Before(points[j].Minute) })
	return points, nil
}

func (r *Repository) LoadPrivateValuationMinuteHistoryDate(ctx context.Context, symbol, day string) ([]privatevaluation.MinuteHistoryPoint, error) {
	if r == nil || r.db == nil {
		return nil, fmt.Errorf("database repository is unavailable")
	}
	parsedDay, err := time.Parse("20060102", day)
	if err != nil {
		return nil, fmt.Errorf("invalid private minute history date: %w", err)
	}
	rows, err := r.db.QueryContext(ctx, `
SELECT snapshot_minute, market_price, basket_bid_nav, basket_ask_nav,
       buy_direction_premium_rate, sell_direction_premium_rate,
       COALESCE(DATE_FORMAT(pcf_trading_day, '%Y-%m-%d'), ''),
       xop_equivalent_shares, nifty_bridge_bid_nav, nifty_bridge_ask_nav,
       nifty_bridge_selection_version, settlement_nav, trading_nav,
       active_contract, futures_price, intraday_average
FROM private_valuation_minute_history
WHERE symbol = ?
  AND trading_day = ?
ORDER BY snapshot_minute`, symbol, parsedDay.Format("2006-01-02"))
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	points := make([]privatevaluation.MinuteHistoryPoint, 0)
	for rows.Next() {
		point, err := scanPrivateMinuteHistoryPoint(rows)
		if err != nil {
			return nil, err
		}
		if privatevaluation.IsMinuteHistoryTradingSessionForSymbol(symbol, point.Minute) {
			points = append(points, point)
		}
	}
	return points, rows.Err()
}

// privateMinuteHistoryPoint applies any current model-specific correction once,
// before the compact minute point is persisted. Historical reads then require
// only the chart fields instead of the full valuation snapshot.
func privateMinuteHistoryPoint(snapshot privatevaluation.Snapshot) (privatevaluation.MinuteHistoryPoint, bool) {
	if snapshot.Symbol == privatevaluation.SH513350Symbol && snapshot.Input != nil && snapshot.DomesticQuote != nil {
		quotes := map[string]domain.Quote{snapshot.Symbol: *snapshot.DomesticQuote}
		recalculated := privatevaluation.CalculateForSymbol(snapshot.Symbol, snapshot.Input, quotes, snapshot.AsOf)
		return privatevaluation.MinuteHistoryPointFromSnapshot(recalculated)
	}
	return privatevaluation.MinuteHistoryPointFromSnapshot(snapshot)
}

func (r *Repository) LoadPrivateValuationMinuteHistoryDates(ctx context.Context, symbol string, limit int) ([]string, error) {
	if r == nil || r.db == nil {
		return nil, fmt.Errorf("database repository is unavailable")
	}
	rows, err := r.db.QueryContext(ctx, `
SELECT DISTINCT DATE_FORMAT(trading_day, '%Y%m%d')
FROM private_valuation_minute_history
WHERE symbol = ?
ORDER BY DATE_FORMAT(trading_day, '%Y%m%d') DESC
LIMIT ?`, symbol, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	dates := make([]string, 0)
	for rows.Next() {
		var day string
		if err := rows.Scan(&day); err != nil {
			return nil, err
		}
		dates = append(dates, day)
	}
	return dates, rows.Err()
}

// LoadPrivateSilverCloseHistory returns the final persisted China-session
// checkpoint for each trading day. The underlying minute rows are immutable
// historical inputs; the official NAV join can fill in later without changing
// either silver estimate or its original close premium.
func (r *Repository) LoadPrivateSilverCloseHistory(ctx context.Context, symbol string, days int) ([]privatevaluation.SilverCloseHistoryRow, error) {
	if r == nil || r.db == nil {
		return nil, fmt.Errorf("database repository is unavailable")
	}
	if days <= 0 || days > 3650 {
		days = 365
	}
	rows, err := r.db.QueryContext(ctx, `
SELECT DATE_FORMAT(history.trading_day, '%Y-%m-%d'), history.snapshot_minute,
       official.nav, COALESCE(official.source, ''), history.market_price,
       history.settlement_nav, history.buy_direction_premium_rate,
       history.trading_nav, history.sell_direction_premium_rate,
       COALESCE(history.active_contract, ''), history.futures_price,
       history.intraday_average
FROM private_valuation_minute_history history
JOIN (
  SELECT trading_day, MAX(snapshot_minute) AS snapshot_minute
  FROM private_valuation_minute_history
  WHERE symbol = ?
    AND settlement_nav IS NOT NULL
    AND trading_nav IS NOT NULL
  GROUP BY trading_day
  ORDER BY trading_day DESC
  LIMIT ?
) selected
  ON selected.trading_day = history.trading_day
 AND selected.snapshot_minute = history.snapshot_minute
LEFT JOIN symbols target_symbol
  ON target_symbol.symbol = history.symbol
LEFT JOIN net_values official
  ON official.symbol_id = target_symbol.id
 AND official.nav_date = history.trading_day
WHERE history.symbol = ?
ORDER BY history.trading_day DESC`, symbol, days, symbol)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	result := make([]privatevaluation.SilverCloseHistoryRow, 0, days)
	for rows.Next() {
		var row privatevaluation.SilverCloseHistoryRow
		var closeMinute time.Time
		var officialNAV sql.NullFloat64
		var futuresPrice, intradayAverage sql.NullFloat64
		if err := rows.Scan(
			&row.TradingDay,
			&closeMinute,
			&officialNAV,
			&row.OfficialNAVSource,
			&row.MarketPrice,
			&row.SettlementNAV,
			&row.SettlementPremiumRate,
			&row.TradingNAV,
			&row.TradingPremiumRate,
			&row.ActiveContract,
			&futuresPrice,
			&intradayAverage,
		); err != nil {
			return nil, err
		}
		row.CloseMinute = closeMinute.In(privateHistoryLocation())
		if officialNAV.Valid {
			value := officialNAV.Float64
			row.OfficialNAV = &value
		}
		if futuresPrice.Valid {
			value := futuresPrice.Float64
			row.FuturesPrice = &value
		}
		if intradayAverage.Valid {
			value := intradayAverage.Float64
			row.IntradayAverage = &value
		}
		result = append(result, row)
	}
	return result, rows.Err()
}

// LoadPrivateIndiaHistoryReview reads the final China-session estimate for
// each requested day and joins the official T NAV plus the official T-2 NAV
// base. The caller performs fitting; this query only returns auditable facts.
func (r *Repository) LoadPrivateIndiaHistoryReview(ctx context.Context, symbol string, days int) ([]privatevaluation.IndiaHistoryReviewSource, error) {
	if r == nil || r.db == nil {
		return nil, fmt.Errorf("database repository is unavailable")
	}
	if days <= 0 || days > 365 {
		days = 120
	}
	rows, err := r.db.QueryContext(ctx, `
SELECT DATE_FORMAT(history.trading_day, '%Y-%m-%d'), official.nav,
       DATE_FORMAT(history.base_nav_date, '%Y-%m-%d'), history.base_nav,
       history.final_estimate_nav, history.investment_ratio, history.static_ratio,
       history.base_anchor_price, history.target_anchor_price, history.base_fx,
       history.target_fx, history.source
FROM private_india_final_nav_history history
LEFT JOIN symbols target_symbol
  ON target_symbol.symbol = history.symbol
LEFT JOIN net_values official
  ON official.symbol_id = target_symbol.id
 AND official.nav_date = history.trading_day
WHERE history.symbol = ?
ORDER BY history.trading_day DESC
LIMIT ?`, symbol, days)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	result := make([]privatevaluation.IndiaHistoryReviewSource, 0, days)
	for rows.Next() {
		var source privatevaluation.IndiaHistoryReviewSource
		var officialNAV, baseNAV sql.NullFloat64
		var baseNAVDate sql.NullString
		if err := rows.Scan(
			&source.TargetDate,
			&officialNAV,
			&baseNAVDate,
			&baseNAV,
			&source.FinalEstimateNAV,
			&source.InvestmentRatio,
			&source.StaticRatio,
			&source.BaseAnchorPrice,
			&source.TargetAnchorPrice,
			&source.BaseFX,
			&source.TargetFX,
			&source.Source,
		); err != nil {
			return nil, err
		}
		if officialNAV.Valid {
			value := officialNAV.Float64
			source.OfficialNAV = &value
		}
		if baseNAVDate.Valid {
			source.BaseNAVDate = baseNAVDate.String
		}
		if baseNAV.Valid {
			value := baseNAV.Float64
			source.BaseNAV = &value
		}
		result = append(result, source)
	}
	return result, rows.Err()
}

func (r *Repository) LoadPrivateIndiaNiftyBridgeHistoryReview(ctx context.Context, symbol string, days int) ([]privatevaluation.IndiaNiftyBridgeReviewSource, error) {
	if r == nil || r.db == nil {
		return nil, fmt.Errorf("database repository is unavailable")
	}
	if days <= 0 || days > 365 {
		days = 120
	}
	rows, err := r.db.QueryContext(ctx, `
SELECT DATE_FORMAT(history.snapshot_minute, '%Y-%m-%d %H:%i:%s'),
       history.market_price, history.direct_nav_bid, history.direct_nav_ask,
       history.bridge_nav_bid, history.bridge_nav_ask, history.state,
       DATE_FORMAT(history.base_nav_date, '%Y-%m-%d'), history.base_nav,
       history.investment_ratio, history.static_ratio, history.anchor_price,
       history.base_fx, history.current_fx,
       COALESCE(DATE_FORMAT(history.current_fx_trading_day, '%Y-%m-%d'), ''),
       history.direct_inda_bid, history.direct_inda_ask,
       DATE_FORMAT(history.direct_inda_observed_at, '%Y-%m-%dT%H:%i:%s.%fZ'),
       history.synthetic_inda_bid, history.synthetic_inda_ask,
       history.nifty_bid, history.nifty_ask, history.nifty_contract,
       DATE_FORMAT(history.nifty_observed_at, '%Y-%m-%dT%H:%i:%s.%fZ'),
       history.inda_reference_bid, history.inda_reference_ask,
       history.inda_reference_contract,
       DATE_FORMAT(history.inda_reference_observed_at, '%Y-%m-%dT%H:%i:%s.%fZ'),
       history.nifty_reference_bid, history.nifty_reference_ask,
       history.nifty_reference_contract,
       DATE_FORMAT(history.nifty_reference_observed_at, '%Y-%m-%dT%H:%i:%s.%fZ'),
       DATE_FORMAT(history.reference_at, '%Y-%m-%dT%H:%i:%s.%fZ'),
       history.beta, history.selection_version,
       COALESCE(DATE_FORMAT(history.roll_date, '%Y-%m-%d'), ''),
       COALESCE(DATE_FORMAT(history.roll_captured_at, '%Y-%m-%dT%H:%i:%s.%fZ'), ''),
       COALESCE(history.roll_old_contract, ''), COALESCE(history.roll_new_contract, ''),
       COALESCE(history.roll_direction, ''), history.roll_bid_factor,
	       history.roll_ask_factor, COALESCE(history.roll_source, ''),
	       history.input_source,
	       DATE_FORMAT(history.input_generated_at, '%Y-%m-%dT%H:%i:%s.%fZ'),
	       official.nav
FROM private_india_nifty_bridge_review_history history
JOIN (
  SELECT trading_day
  FROM private_india_nifty_bridge_review_history
  WHERE symbol = ?
  GROUP BY trading_day
  ORDER BY trading_day DESC
  LIMIT ?
) selected_days ON selected_days.trading_day = history.trading_day
LEFT JOIN symbols target_symbol
  ON target_symbol.symbol = history.symbol
LEFT JOIN net_values official
  ON official.symbol_id = target_symbol.id
 AND official.nav_date = history.trading_day
WHERE history.symbol = ?
ORDER BY history.snapshot_minute`, symbol, days, symbol)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	result := make([]privatevaluation.IndiaNiftyBridgeReviewSource, 0)
	for rows.Next() {
		var source privatevaluation.IndiaNiftyBridgeReviewSource
		var minuteRaw string
		var directObservedRaw, niftyObservedRaw, indaReferenceObservedRaw string
		var niftyReferenceObservedRaw, referenceAtRaw, inputGeneratedAtRaw string
		var rollDate, rollCapturedRaw, rollOld, rollNew, rollDirection, rollSource string
		var rollBidFactor, rollAskFactor, officialNAV sql.NullFloat64
		if err := rows.Scan(
			&minuteRaw,
			&source.MarketPrice,
			&source.DirectBidNAV,
			&source.DirectAskNAV,
			&source.BridgeBidNAV,
			&source.BridgeAskNAV,
			&source.RollStatus,
			&source.Audit.BaseNAVDate,
			&source.Audit.BaseNAV,
			&source.Audit.InvestmentRatio,
			&source.Audit.StaticRatio,
			&source.Audit.BaseAnchorPrice,
			&source.Audit.BaseFX,
			&source.Audit.CurrentFX,
			&source.Audit.CurrentFXTradingDay,
			&source.Audit.DirectINDABid,
			&source.Audit.DirectINDAAsk,
			&directObservedRaw,
			&source.Audit.SyntheticINDABid,
			&source.Audit.SyntheticINDAAsk,
			&source.Audit.CurrentNiftyBid,
			&source.Audit.CurrentNiftyAsk,
			&source.Audit.CurrentNiftyContract,
			&niftyObservedRaw,
			&source.Audit.ReferenceINDABid,
			&source.Audit.ReferenceINDAAsk,
			&source.Audit.ReferenceINDAContract,
			&indaReferenceObservedRaw,
			&source.Audit.ReferenceNiftyBid,
			&source.Audit.ReferenceNiftyAsk,
			&source.Audit.ReferenceNiftyContract,
			&niftyReferenceObservedRaw,
			&referenceAtRaw,
			&source.Audit.Beta,
			&source.Audit.ContractSelectionVersion,
			&rollDate,
			&rollCapturedRaw,
			&rollOld,
			&rollNew,
			&rollDirection,
			&rollBidFactor,
			&rollAskFactor,
			&rollSource,
			&source.Audit.InputSource,
			&inputGeneratedAtRaw,
			&officialNAV,
		); err != nil {
			return nil, err
		}
		minute, err := time.ParseInLocation("2006-01-02 15:04:05", minuteRaw, privateHistoryLocation())
		if err != nil {
			return nil, err
		}
		source.Symbol = symbol
		source.Minute = minute
		for _, timestamp := range []struct {
			raw    string
			target *time.Time
		}{
			{directObservedRaw, &source.Audit.DirectINDAObservedAt},
			{niftyObservedRaw, &source.Audit.CurrentNiftyObservedAt},
			{indaReferenceObservedRaw, &source.Audit.ReferenceINDAObservedAt},
			{niftyReferenceObservedRaw, &source.Audit.ReferenceNiftyObservedAt},
			{referenceAtRaw, &source.Audit.ReferenceAt},
			{inputGeneratedAtRaw, &source.Audit.InputGeneratedAt},
		} {
			parsed, err := time.Parse(time.RFC3339Nano, timestamp.raw)
			if err != nil {
				return nil, err
			}
			*timestamp.target = parsed.UTC()
		}
		if rollDate != "" {
			if !rollBidFactor.Valid || !rollAskFactor.Valid || rollCapturedRaw == "" {
				return nil, fmt.Errorf("incomplete persisted NIFTY roll audit for %s at %s", symbol, minuteRaw)
			}
			capturedAt, err := time.Parse(time.RFC3339Nano, rollCapturedRaw)
			if err != nil {
				return nil, err
			}
			source.Audit.RollAdjustment = &privatevaluation.IndiaNiftyBridgeRollAudit{
				RollDate:    rollDate,
				CapturedAt:  capturedAt.UTC(),
				OldContract: rollOld,
				NewContract: rollNew,
				Direction:   rollDirection,
				BidFactor:   rollBidFactor.Float64,
				AskFactor:   rollAskFactor.Float64,
				Source:      rollSource,
			}
		}
		if officialNAV.Valid && officialNAV.Float64 > 0 {
			value := officialNAV.Float64
			source.OfficialNAV = &value
		}
		result = append(result, source)
	}
	return result, rows.Err()
}

// UpsertPrivateIndiaFinalNAVHistory persists complete T-day final-NAV
// replays separately from private_valuation_minute_history.  That separation
// is intentional: a delayed post-close replay must never affect the live
// China-session NIFTY bridge chart.
func (r *Repository) UpsertPrivateIndiaFinalNAVHistory(
	ctx context.Context, symbol string, points []privatevaluation.IndiaFinalNAVHistoryPoint,
) error {
	if r == nil || r.db == nil {
		return fmt.Errorf("database repository is unavailable")
	}
	if len(points) == 0 {
		return nil
	}
	tx, err := r.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	for _, point := range points {
		if _, err := tx.ExecContext(ctx, `
INSERT INTO private_india_final_nav_history (
  symbol, trading_day, base_nav_date, base_nav, investment_ratio, static_ratio,
  base_anchor_price, target_anchor_price, base_fx, target_fx,
  final_estimate_nav, source, generated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON DUPLICATE KEY UPDATE
  base_nav_date = VALUES(base_nav_date),
  base_nav = VALUES(base_nav),
  investment_ratio = VALUES(investment_ratio),
  static_ratio = VALUES(static_ratio),
  base_anchor_price = VALUES(base_anchor_price),
  target_anchor_price = VALUES(target_anchor_price),
  base_fx = VALUES(base_fx),
  target_fx = VALUES(target_fx),
  final_estimate_nav = VALUES(final_estimate_nav),
  source = VALUES(source),
  generated_at = VALUES(generated_at)`,
			symbol,
			point.TargetDate,
			point.BaseNAVDate,
			point.BaseNAV,
			point.InvestmentRatio,
			point.StaticRatio,
			point.BaseAnchorPrice,
			point.TargetAnchorPrice,
			point.BaseFX,
			point.TargetFX,
			point.FinalEstimateNAV,
			point.Source,
			point.GeneratedAt,
		); err != nil {
			return err
		}
	}
	return tx.Commit()
}

type privateMinuteHistoryScanner interface {
	Scan(dest ...any) error
}

func scanPrivateMinuteHistoryPoint(scanner privateMinuteHistoryScanner) (privatevaluation.MinuteHistoryPoint, error) {
	var point privatevaluation.MinuteHistoryPoint
	var minute time.Time
	var pcfTradingDay string
	var xopEquivalentShares sql.NullFloat64
	var niftyBridgeBidNAV sql.NullFloat64
	var niftyBridgeAskNAV sql.NullFloat64
	var niftyBridgeSelectionVersion sql.NullString
	var settlementNAV sql.NullFloat64
	var tradingNAV sql.NullFloat64
	var activeContract sql.NullString
	var futuresPrice sql.NullFloat64
	var intradayAverage sql.NullFloat64
	if err := scanner.Scan(
		&minute,
		&point.MarketPrice,
		&point.BasketBidNAV,
		&point.BasketAskNAV,
		&point.BuyDirectionPremiumRate,
		&point.SellDirectionPremiumRate,
		&pcfTradingDay,
		&xopEquivalentShares,
		&niftyBridgeBidNAV,
		&niftyBridgeAskNAV,
		&niftyBridgeSelectionVersion,
		&settlementNAV,
		&tradingNAV,
		&activeContract,
		&futuresPrice,
		&intradayAverage,
	); err != nil {
		return privatevaluation.MinuteHistoryPoint{}, err
	}
	point.Minute = minute.In(privateHistoryLocation())
	point.PCFTradingDay = pcfTradingDay
	if xopEquivalentShares.Valid {
		shares := xopEquivalentShares.Float64
		point.XOPEquivalentShares = &shares
	}
	if niftyBridgeBidNAV.Valid {
		value := niftyBridgeBidNAV.Float64
		point.NiftyBridgeBidNAV = &value
	}
	if niftyBridgeAskNAV.Valid {
		value := niftyBridgeAskNAV.Float64
		point.NiftyBridgeAskNAV = &value
	}
	if niftyBridgeSelectionVersion.Valid {
		point.NiftyBridgeSelectionVersion = niftyBridgeSelectionVersion.String
	}
	if settlementNAV.Valid {
		value := settlementNAV.Float64
		point.SettlementNAV = &value
	}
	if tradingNAV.Valid {
		value := tradingNAV.Float64
		point.TradingNAV = &value
	}
	if activeContract.Valid {
		point.ActiveContract = activeContract.String
	}
	if futuresPrice.Valid {
		value := futuresPrice.Float64
		point.FuturesPrice = &value
	}
	if intradayAverage.Valid {
		value := intradayAverage.Float64
		point.IntradayAverage = &value
	}
	return point, nil
}

func privateHistoryLocation() *time.Location {
	return time.FixedZone("Asia/Shanghai", 8*60*60)
}

func privateIndiaNiftyBridgeSnapshotMinute(value time.Time) string {
	return value.In(privateHistoryLocation()).Format("2006-01-02 15:04:05")
}

func firstLevelPrice(levels []domain.Level) any {
	for _, level := range levels {
		if level.Price > 0 {
			return level.Price
		}
	}
	return nil
}
