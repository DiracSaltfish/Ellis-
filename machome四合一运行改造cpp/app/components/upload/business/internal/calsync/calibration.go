package calsync

import (
	"context"
	"fmt"
	"log/slog"
	"math"
	"sort"
	"strings"
	"time"

	"newnavnav/internal/domain"
)

const SourcePrefix = "auto_daily:"

type Repository interface {
	LoadValuationData(ctx context.Context) (domain.ValuationData, error)
	UpsertCalibrations(ctx context.Context, calibrations []domain.Calibration) error
}

type Service struct {
	repository Repository
	logger     *slog.Logger
}

type Options struct {
	Repository Repository
	Logger     *slog.Logger
}

func NewService(opts Options) *Service {
	logger := opts.Logger
	if logger == nil {
		logger = slog.Default()
	}
	return &Service{
		repository: opts.Repository,
		logger:     logger,
	}
}

func (s *Service) SyncMissing(ctx context.Context, today time.Time) error {
	if s.repository == nil {
		return fmt.Errorf("daily calibration requires repository")
	}
	data, err := s.repository.LoadValuationData(ctx)
	if err != nil {
		return err
	}
	domain.ApplyStaticValuationData(&data)

	funds := fundSymbols(data)
	calibrations := make([]domain.Calibration, 0, len(funds))
	missingNAV := 0
	missingClose := 0
	skippedCurrent := 0
	for _, fund := range funds {
		nav, ok := data.LatestNetValues[fund]
		if !ok || nav.Date == "" || nav.NAV <= 0 {
			missingNAV++
			s.logger.Debug("daily calibration skipped; missing latest nav", "symbol", fund)
			continue
		}
		if latest, ok := data.LatestCalibrations[fund]; ok && latest.Date >= nav.Date {
			skippedCurrent++
			s.logger.Debug("daily calibration skipped; calibration is current", "symbol", fund, "nav_date", nav.Date, "cal_date", latest.Date)
			continue
		}
		cal, ok := calibrationFromPairs(fund, nav, data)
		if !ok {
			missingClose++
			s.logger.Info("daily calibration skipped; missing pair close on nav date", "symbol", fund, "nav_date", nav.Date, "pairs", pairSymbols(data.FundPairs[fund]))
			continue
		}
		calibrations = append(calibrations, cal)
	}

	if len(calibrations) == 0 {
		s.logger.Info("daily calibration finished; nothing to write", "funds", len(funds), "missing_nav", missingNAV, "missing_close", missingClose, "skipped_current", skippedCurrent)
		return nil
	}
	if err := s.repository.UpsertCalibrations(ctx, calibrations); err != nil {
		return err
	}
	s.logger.Info("daily calibration synced", "funds", len(funds), "written", len(calibrations), "missing_nav", missingNAV, "missing_close", missingClose, "skipped_current", skippedCurrent, "run_date", today.Format("2006-01-02"))
	return nil
}

func fundSymbols(data domain.ValuationData) []string {
	seen := map[string]bool{}
	for fund := range data.FundPairs {
		if fund == "" || domain.IsExcludedSymbol(fund) {
			continue
		}
		seen[fund] = true
	}
	funds := make([]string, 0, len(seen))
	for fund := range seen {
		funds = append(funds, fund)
	}
	sort.Strings(funds)
	return funds
}

func calibrationFromPairs(fund string, nav domain.NetValue, data domain.ValuationData) (domain.Calibration, bool) {
	for _, pair := range data.FundPairs[fund] {
		if pair.FundSymbol != "" && !strings.EqualFold(pair.FundSymbol, fund) {
			continue
		}
		if domain.IsExcludedSymbol(pair.PairSymbol) {
			continue
		}
		price, ok := dailyPriceAt(data, pair.PairSymbol, nav.Date)
		if !ok || price <= 0 {
			continue
		}
		factor := price / nav.NAV
		if !validFactor(factor) {
			continue
		}
		return domain.Calibration{
			Symbol:    fund,
			Date:      nav.Date,
			Factor:    factor,
			BaseValue: nav.NAV,
			Source:    SourcePrefix + pair.PairSymbol,
		}, true
	}
	return domain.Calibration{}, false
}

func dailyPriceAt(data domain.ValuationData, symbol string, date string) (float64, bool) {
	byDate := data.DailyPricesByDate[symbol]
	if byDate == nil {
		return 0, false
	}
	price, ok := byDate[date]
	if !ok {
		return 0, false
	}
	if price.AdjClose > 0 {
		return price.AdjClose, true
	}
	return price.Close, price.Close > 0
}

func validFactor(factor float64) bool {
	return factor > 0 && factor < 1_000_000_000 && !math.IsInf(factor, 0) && !math.IsNaN(factor)
}

func pairSymbols(pairs []domain.FundPair) string {
	if len(pairs) == 0 {
		return ""
	}
	symbols := make([]string, 0, len(pairs))
	for _, pair := range pairs {
		if pair.PairSymbol != "" {
			symbols = append(symbols, pair.PairSymbol)
		}
	}
	return strings.Join(symbols, ",")
}
