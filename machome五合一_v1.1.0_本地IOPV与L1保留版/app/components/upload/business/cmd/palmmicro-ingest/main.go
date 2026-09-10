package main

import (
	"context"
	"database/sql"
	"flag"
	"fmt"
	"log/slog"
	"os"
	"strings"
	"time"

	"newnavnav/internal/config"
	"newnavnav/internal/db"
	"newnavnav/internal/domain"
	"newnavnav/internal/ingest/palmmicro"
)

func main() {
	var baseURL string
	var branchKeys string
	var skipFundList bool
	var skipHoldings bool
	var delayMS int
	flag.StringVar(&baseURL, "base-url", "https://www.palmmicro.com", "Palmmicro base URL")
	flag.StringVar(&branchKeys, "branches", "qdiimix", "comma-separated branch keys for optional holdings import")
	flag.BoolVar(&skipFundList, "skip-fundlist", false, "skip fundlist import")
	flag.BoolVar(&skipHoldings, "skip-holdings", true, "skip holdings import; pass -skip-holdings=false only for a deliberate legacy backfill")
	flag.IntVar(&delayMS, "delay-ms", 300, "delay between remote requests")
	flag.Parse()

	cfg := config.Load()
	logger := slog.New(slog.NewTextHandler(os.Stdout, &slog.HandlerOptions{Level: cfg.LogLevel}))
	slog.SetDefault(logger)
	if cfg.MySQLDSN == "" {
		logger.Error("MYSQL_DSN is required")
		os.Exit(1)
	}

	ctx := context.Background()
	database, err := db.Open(ctx, cfg.MySQLDSN)
	if err != nil {
		logger.Error("mysql unavailable", "error", err)
		os.Exit(1)
	}
	defer database.Close()

	repository := db.NewRepository(database)
	client := palmmicro.NewClient(baseURL, 15*time.Second)
	ingester := &ingester{
		db:         database,
		repository: repository,
		client:     client,
		delay:      time.Duration(delayMS) * time.Millisecond,
	}

	if !skipFundList {
		count, err := ingester.importFundList(ctx)
		if err != nil {
			logger.Error("fundlist import failed", "error", err)
			os.Exit(1)
		}
		logger.Info("fundlist imported", "rows", count)
	}
	if !skipHoldings {
		symbols := symbolsForBranches(branchKeys)
		count, err := ingester.importHoldings(ctx, symbols)
		if err != nil {
			logger.Error("holdings import failed", "error", err)
			os.Exit(1)
		}
		logger.Info("holdings imported", "funds", count)
	} else {
		logger.Info("holdings import skipped")
	}
}

type ingester struct {
	db         *sql.DB
	repository *db.Repository
	client     *palmmicro.Client
	delay      time.Duration
}

func (i *ingester) importFundList(ctx context.Context) (int, error) {
	raw, err := i.client.Get(ctx, "/woody/res/fundlistcn.php")
	if err != nil {
		return 0, err
	}
	items, err := palmmicro.ParseFundList(raw)
	if err != nil {
		return 0, err
	}
	for _, item := range items {
		if err := i.writeFundListItem(ctx, item); err != nil {
			return 0, fmt.Errorf("%s: %w", item.FundSymbol, err)
		}
	}
	return len(items), nil
}

func (i *ingester) importHoldings(ctx context.Context, symbols []string) (int, error) {
	imported := 0
	for idx, symbol := range symbols {
		if idx > 0 && i.delay > 0 {
			time.Sleep(i.delay)
		}
		raw, err := i.client.Get(ctx, "/woody/res/holdingscn.php?symbol="+symbol)
		if err != nil {
			slog.Warn("holding page fetch failed", "symbol", symbol, "error", err)
			continue
		}
		snapshot, err := palmmicro.ParseHoldingSnapshot(symbol, raw)
		if err != nil {
			return imported, fmt.Errorf("%s: %w", symbol, err)
		}
		if len(snapshot.Holdings) == 0 || snapshot.HoldingDate == "" {
			continue
		}
		if err := i.writeHoldingSnapshot(ctx, snapshot); err != nil {
			return imported, fmt.Errorf("%s: %w", symbol, err)
		}
		imported++
		slog.Info("holding imported", "symbol", symbol, "date", snapshot.HoldingDate, "rows", len(snapshot.Holdings))
	}
	return imported, nil
}

func (i *ingester) writeFundListItem(ctx context.Context, item palmmicro.FundListItem) error {
	if item.CalibrationDate == "" || item.Calibration <= 0 || item.Position == 0 {
		return nil
	}
	if err := i.repository.EnsureSymbols(ctx, []string{item.FundSymbol, item.PairSymbol}); err != nil {
		return err
	}
	if err := i.updateSymbolName(ctx, item.FundSymbol, item.FundName); err != nil {
		return err
	}
	if err := i.updateSymbolName(ctx, item.PairSymbol, item.PairName); err != nil {
		return err
	}

	if _, err := i.db.ExecContext(ctx, `
INSERT INTO fund_pairs (fund_symbol_id, pair_symbol_id, pair_type, note)
SELECT f.id, p.id, 'palmmicro_fundpair', 'imported from palmmicro fundlistcn.php'
FROM symbols f
JOIN symbols p ON p.symbol = ?
WHERE f.symbol = ?
ON DUPLICATE KEY UPDATE active = 1, note = VALUES(note)`, item.PairSymbol, item.FundSymbol); err != nil {
		return err
	}
	if _, err := i.db.ExecContext(ctx, `
INSERT INTO fund_positions (fund_symbol_id, position_ratio, source)
SELECT id, ?, 'palmmicro' FROM symbols WHERE symbol = ?
ON DUPLICATE KEY UPDATE position_ratio = VALUES(position_ratio), source = VALUES(source)`, item.Position, item.FundSymbol); err != nil {
		return err
	}
	if _, err := i.db.ExecContext(ctx, `
INSERT INTO calibration_history (symbol_id, cal_date, factor, source)
SELECT id, ?, ?, 'palmmicro' FROM symbols WHERE symbol = ?
ON DUPLICATE KEY UPDATE factor = VALUES(factor), source = VALUES(source)`, item.CalibrationDate, item.Calibration, item.FundSymbol); err != nil {
		return err
	}
	return nil
}

func (i *ingester) writeHoldingSnapshot(ctx context.Context, snapshot palmmicro.HoldingSnapshot) error {
	symbols := []string{snapshot.FundSymbol}
	for _, holding := range snapshot.Holdings {
		symbols = append(symbols, holding.Symbol)
	}
	if err := i.repository.EnsureSymbols(ctx, symbols); err != nil {
		return err
	}
	if _, err := i.db.ExecContext(ctx, `
INSERT INTO fund_positions (fund_symbol_id, position_ratio, source)
SELECT id, ?, 'palmmicro' FROM symbols WHERE symbol = ?
ON DUPLICATE KEY UPDATE position_ratio = VALUES(position_ratio), source = VALUES(source)`, snapshot.Position, snapshot.FundSymbol); err != nil {
		return err
	}
	if snapshot.NetValueDate != "" && snapshot.NetValue > 0 {
		if _, err := i.db.ExecContext(ctx, `
INSERT INTO net_values (symbol_id, nav_date, nav, source, confidence)
SELECT id, ?, ?, 'palmmicro', 'public_page' FROM symbols WHERE symbol = ?
ON DUPLICATE KEY UPDATE nav = VALUES(nav), confidence = VALUES(confidence)`, snapshot.NetValueDate, snapshot.NetValue, snapshot.FundSymbol); err != nil {
			return err
		}
	}
	if _, err := i.db.ExecContext(ctx, `
UPDATE fund_holding_dates fhd
JOIN symbols s ON s.id = fhd.fund_symbol_id
SET fhd.is_current = 0
WHERE s.symbol = ? AND fhd.holding_date <> ?`, snapshot.FundSymbol, snapshot.HoldingDate); err != nil {
		return err
	}
	if _, err := i.db.ExecContext(ctx, `
INSERT INTO fund_holding_dates (fund_symbol_id, holding_date, source, is_current)
SELECT id, ?, 'palmmicro', 1 FROM symbols WHERE symbol = ?
ON DUPLICATE KEY UPDATE source = VALUES(source), is_current = VALUES(is_current)`, snapshot.HoldingDate, snapshot.FundSymbol); err != nil {
		return err
	}

	for _, holding := range snapshot.Holdings {
		if err := i.updateSymbolName(ctx, holding.Symbol, holding.Name); err != nil {
			return err
		}
		if _, err := i.db.ExecContext(ctx, `
INSERT INTO fund_holdings (fund_symbol_id, holding_date, holding_symbol_id, holding_name, ratio, fx_adjust, currency, source)
SELECT f.id, ?, h.id, ?, ?, ?, ?, 'palmmicro'
FROM symbols f
JOIN symbols h ON h.symbol = ?
WHERE f.symbol = ?
ON DUPLICATE KEY UPDATE
  holding_name = VALUES(holding_name),
  ratio = VALUES(ratio),
  fx_adjust = VALUES(fx_adjust),
  currency = VALUES(currency),
  source = VALUES(source)`, snapshot.HoldingDate, holding.Name, holding.Ratio, holding.FXAdjust, holding.Currency, holding.Symbol, snapshot.FundSymbol); err != nil {
			return err
		}
		if _, err := i.db.ExecContext(ctx, `
INSERT INTO daily_prices (symbol_id, trade_date, close_price, adj_close, source)
SELECT id, ?, ?, ?, 'palmmicro_holding_base' FROM symbols WHERE symbol = ?
ON DUPLICATE KEY UPDATE
  close_price = VALUES(close_price),
  adj_close = VALUES(adj_close),
  source = VALUES(source)`, snapshot.HoldingDate, holding.BasePrice, holding.BasePrice, holding.Symbol); err != nil {
			return err
		}
	}
	return nil
}

func (i *ingester) updateSymbolName(ctx context.Context, symbol string, name string) error {
	if strings.TrimSpace(name) == "" {
		return nil
	}
	_, err := i.db.ExecContext(ctx, `UPDATE symbols SET name_cn = ?, updated_at = CURRENT_TIMESTAMP(3) WHERE symbol = ?`, name, symbol)
	return err
}

func symbolsForBranches(keys string) []string {
	seen := make(map[string]bool)
	var out []string
	for _, key := range strings.Split(keys, ",") {
		key = strings.TrimSpace(key)
		if key == "" {
			continue
		}
		branch, ok := domain.BranchByKey(key)
		if !ok {
			slog.Warn("unknown branch skipped", "branch", key)
			continue
		}
		for _, symbol := range branch.Symbols {
			if seen[symbol] {
				continue
			}
			seen[symbol] = true
			out = append(out, symbol)
		}
	}
	return out
}
