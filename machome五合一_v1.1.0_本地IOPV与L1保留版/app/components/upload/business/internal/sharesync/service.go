package sharesync

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"sort"
	"strings"
	"time"

	"newnavnav/internal/domain"
)

type FundSizeClient interface {
	FetchFundSizes(ctx context.Context, startDate string, endDate string, fundType string, symbol string) ([]domain.ShareHistoryRecord, error)
}

type Repository interface {
	UpsertShareHistory(ctx context.Context, rows []domain.ShareHistoryRecord) error
	LatestShareHistoryDateBySymbolPrefix(ctx context.Context, prefix string) (string, bool, error)
}

type Options struct {
	Client              FundSizeClient
	Repository          Repository
	Symbols             []string
	Logger              *slog.Logger
	QueryInterval       time.Duration
	InitialLookbackDays int
}

type Service struct {
	client              FundSizeClient
	repository          Repository
	symbols             []string
	logger              *slog.Logger
	location            *time.Location
	queryInterval       time.Duration
	initialLookbackDays int
}

func NewService(opts Options) *Service {
	logger := opts.Logger
	if logger == nil {
		logger = slog.Default()
	}
	location, err := time.LoadLocation("Asia/Shanghai")
	if err != nil {
		location = time.Local
	}
	queryInterval := opts.QueryInterval
	if queryInterval <= 0 {
		queryInterval = 3 * time.Second
	}
	lookbackDays := opts.InitialLookbackDays
	if lookbackDays <= 0 {
		lookbackDays = 60
	}
	return &Service{
		client:              opts.Client,
		repository:          opts.Repository,
		symbols:             trackedSZSymbols(opts.Symbols),
		logger:              logger,
		location:            location,
		queryInterval:       queryInterval,
		initialLookbackDays: lookbackDays,
	}
}

func (s *Service) SyncMissing(ctx context.Context, today time.Time) error {
	if s.client == nil {
		return errors.New("szse share history sync requires client")
	}
	if s.repository == nil {
		return errors.New("szse share history sync requires repository")
	}
	if today.IsZero() {
		today = time.Now()
	}
	latestDate, hasHistory, err := s.repository.LatestShareHistoryDateBySymbolPrefix(ctx, "SZ")
	if err != nil {
		return err
	}
	mode := "daily"
	if hasHistory && !isWeekday(today.In(s.location)) {
		s.logger.Info("szse share history sync skipped; non-weekday disclosure run", "latest_date", latestDate, "tracked_symbols", len(s.symbols))
		return nil
	}
	endDate := s.TargetDate(today)
	startDate := endDate
	if !hasHistory {
		startDate = parseDateOrFallback(endDate).AddDate(0, 0, -s.initialLookbackDays+1).Format("2006-01-02")
		mode = "initial_backfill"
	}
	rows, fetchErr := s.fetchTrackedRows(ctx, startDate, endDate)
	if len(rows) == 0 {
		if fetchErr != nil {
			return fetchErr
		}
		s.logger.Info("szse share history sync skipped; no tracked rows", "start_date", startDate, "end_date", endDate, "latest_date", latestDate, "mode", mode, "tracked_symbols", len(s.symbols))
		return nil
	}
	if err := s.repository.UpsertShareHistory(ctx, rows); err != nil {
		return err
	}
	typeCounts := map[string]int{}
	for _, row := range rows {
		typeCounts[row.FundType]++
	}
	s.logger.Info("szse share history sync finished", "start_date", startDate, "end_date", endDate, "latest_date", latestDate, "mode", mode, "rows", len(rows), "type_counts", typeCounts)
	return fetchErr
}

func (s *Service) SyncRange(ctx context.Context, startDate string, endDate string) ([]domain.ShareHistoryRecord, error) {
	startDate = strings.TrimSpace(startDate)
	endDate = strings.TrimSpace(endDate)
	if startDate == "" && endDate == "" {
		endDate = s.TargetDate(time.Now())
		startDate = endDate
	}
	if startDate == "" {
		startDate = endDate
	}
	if endDate == "" {
		endDate = startDate
	}
	rows, fetchErr := s.fetchTrackedRows(ctx, startDate, endDate)
	if len(rows) == 0 {
		return rows, fetchErr
	}
	if s.repository != nil {
		if err := s.repository.UpsertShareHistory(ctx, rows); err != nil {
			return nil, err
		}
	}
	return rows, fetchErr
}

func (s *Service) SyncDate(ctx context.Context, date string) ([]domain.ShareHistoryRecord, error) {
	date = strings.TrimSpace(date)
	return s.SyncRange(ctx, date, date)
}

func (s *Service) TargetDate(now time.Time) string {
	if now.IsZero() {
		now = time.Now()
	}
	local := now.In(s.location)
	cutoff := time.Date(local.Year(), local.Month(), local.Day(), 23, 50, 0, 0, s.location)
	target := time.Date(local.Year(), local.Month(), local.Day(), 0, 0, 0, 0, s.location)
	if local.Before(cutoff) || !isWeekday(target) {
		target = previousBusinessDay(target)
	}
	return target.Format("2006-01-02")
}

func (s *Service) fetchTrackedRows(ctx context.Context, startDate string, endDate string) ([]domain.ShareHistoryRecord, error) {
	allRows := make([]domain.ShareHistoryRecord, 0, len(s.symbols))
	failures := make([]string, 0)
	for index, symbol := range s.symbols {
		if index > 0 {
			if err := waitForInterval(ctx, s.queryInterval); err != nil {
				return nil, err
			}
		}
		var symbolRows []domain.ShareHistoryRecord
		var matchedType string
		attemptErrors := make([]string, 0, 2)
		for _, fundType := range []string{"ETF", "LOF"} {
			rows, err := s.client.FetchFundSizes(ctx, startDate, endDate, fundType, symbol)
			if err != nil {
				if ctx.Err() != nil {
					return dedupeRows(allRows), ctx.Err()
				}
				attemptErrors = append(attemptErrors, fmt.Sprintf("%s: %v", fundType, err))
				continue
			}
			if len(rows) > 0 {
				symbolRows = rows
				matchedType = fundType
				break
			}
		}
		if len(symbolRows) == 0 {
			if len(attemptErrors) > 0 {
				s.logger.Warn("szse share history symbol fetch failed", "symbol", symbol, "start_date", startDate, "end_date", endDate, "errors", strings.Join(attemptErrors, " | "))
				failures = append(failures, fmt.Sprintf("%s [%s]", symbol, strings.Join(attemptErrors, "; ")))
				continue
			}
			s.logger.Debug("szse share history symbol skipped; no ETF/LOF rows", "symbol", symbol, "start_date", startDate, "end_date", endDate)
			continue
		}
		s.logger.Info("szse share history symbol fetched", "symbol", symbol, "fund_type", matchedType, "rows", len(symbolRows), "index", index+1, "total", len(s.symbols))
		allRows = append(allRows, symbolRows...)
	}
	rows := dedupeRows(allRows)
	if len(failures) > 0 {
		return rows, fmt.Errorf("szse share history partial fetch failed for %d symbols: %s", len(failures), strings.Join(failures, ", "))
	}
	return rows, nil
}

func trackedSZSymbols(symbols []string) []string {
	seen := map[string]bool{}
	out := make([]string, 0, len(symbols))
	for _, symbol := range symbols {
		symbol = strings.ToUpper(strings.TrimSpace(symbol))
		if strings.HasPrefix(symbol, "SZ") && !seen[symbol] {
			seen[symbol] = true
			out = append(out, symbol)
		}
	}
	sort.Strings(out)
	return out
}

func waitForInterval(ctx context.Context, interval time.Duration) error {
	if interval <= 0 {
		return nil
	}
	timer := time.NewTimer(interval)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

func isWeekday(date time.Time) bool {
	weekday := date.Weekday()
	return weekday >= time.Monday && weekday <= time.Friday
}

func previousBusinessDay(date time.Time) time.Time {
	previous := date.AddDate(0, 0, -1)
	for !isWeekday(previous) {
		previous = previous.AddDate(0, 0, -1)
	}
	return previous
}

func parseDateOrFallback(date string) time.Time {
	parsed, err := time.Parse("2006-01-02", strings.TrimSpace(date))
	if err != nil {
		return time.Now()
	}
	return parsed
}

func dedupeRows(rows []domain.ShareHistoryRecord) []domain.ShareHistoryRecord {
	seen := make(map[string]int, len(rows))
	out := make([]domain.ShareHistoryRecord, 0, len(rows))
	for _, row := range rows {
		key := strings.ToUpper(strings.TrimSpace(row.Symbol)) + "|" + strings.TrimSpace(row.ShareDate)
		if index, ok := seen[key]; ok {
			out[index] = row
			continue
		}
		seen[key] = len(out)
		out = append(out, row)
	}
	return out
}
