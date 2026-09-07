package sharesync

import (
	"context"
	"errors"
	"log/slog"
	"strings"
	"time"

	"newnavnav/internal/domain"
)

type SSEFundSizeClient interface {
	FetchFundSizesByDate(ctx context.Context, date string, fundType string) ([]domain.ShareHistoryRecord, error)
}

type SSEOptions struct {
	Client              SSEFundSizeClient
	Repository          Repository
	Symbols             []string
	Logger              *slog.Logger
	QueryInterval       time.Duration
	InitialLookbackDays int
}

type SSEService struct {
	client              SSEFundSizeClient
	repository          Repository
	symbols             map[string]bool
	logger              *slog.Logger
	location            *time.Location
	queryInterval       time.Duration
	initialLookbackDays int
}

func NewSSEService(opts SSEOptions) *SSEService {
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
		queryInterval = 200 * time.Millisecond
	}
	lookbackDays := opts.InitialLookbackDays
	if lookbackDays <= 0 {
		lookbackDays = 60
	}
	return &SSEService{
		client:              opts.Client,
		repository:          opts.Repository,
		symbols:             trackedSymbolsByPrefix(opts.Symbols, "SH"),
		logger:              logger,
		location:            location,
		queryInterval:       queryInterval,
		initialLookbackDays: lookbackDays,
	}
}

func (s *SSEService) SyncMissing(ctx context.Context, today time.Time) error {
	if s.client == nil {
		return errors.New("sse share history sync requires client")
	}
	if s.repository == nil {
		return errors.New("sse share history sync requires repository")
	}
	endDate := s.TargetDate(today)
	startDate := endDate
	latestDate, hasHistory, err := s.repository.LatestShareHistoryDateBySymbolPrefix(ctx, "SH")
	if err != nil {
		return err
	}
	mode := "daily"
	if !hasHistory {
		startDate = parseDateOrFallback(endDate).AddDate(0, 0, -s.initialLookbackDays+1).Format("2006-01-02")
		mode = "initial_backfill"
	} else if nextDate, ok := nextDateString(latestDate); ok && nextDate < endDate {
		startDate = nextDate
		mode = "catch_up"
	}
	rows, err := s.fetchTrackedRange(ctx, startDate, endDate)
	if err != nil {
		return err
	}
	if len(rows) == 0 {
		s.logger.Info("sse share history sync skipped; no tracked rows", "start_date", startDate, "end_date", endDate, "latest_date", latestDate, "mode", mode, "tracked_symbols", len(s.symbols))
		return nil
	}
	if err := s.repository.UpsertShareHistory(ctx, rows); err != nil {
		return err
	}
	typeCounts := map[string]int{}
	for _, row := range rows {
		typeCounts[row.FundType]++
	}
	s.logger.Info("sse share history sync finished", "start_date", startDate, "end_date", endDate, "latest_date", latestDate, "mode", mode, "rows", len(rows), "type_counts", typeCounts)
	return nil
}

func (s *SSEService) SyncRange(ctx context.Context, startDate string, endDate string) ([]domain.ShareHistoryRecord, error) {
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
	rows, err := s.fetchTrackedRange(ctx, startDate, endDate)
	if err != nil {
		return nil, err
	}
	if len(rows) == 0 {
		return rows, nil
	}
	if s.repository != nil {
		if err := s.repository.UpsertShareHistory(ctx, rows); err != nil {
			return nil, err
		}
	}
	return rows, nil
}

func (s *SSEService) SyncDate(ctx context.Context, date string) ([]domain.ShareHistoryRecord, error) {
	date = strings.TrimSpace(date)
	return s.SyncRange(ctx, date, date)
}

func (s *SSEService) TargetDate(now time.Time) string {
	if now.IsZero() {
		now = time.Now()
	}
	return now.In(s.location).Format("2006-01-02")
}

func nextDateString(date string) (string, bool) {
	parsed, err := time.Parse("2006-01-02", strings.TrimSpace(date))
	if err != nil {
		return "", false
	}
	return parsed.AddDate(0, 0, 1).Format("2006-01-02"), true
}

func (s *SSEService) fetchTrackedRange(ctx context.Context, startDate string, endDate string) ([]domain.ShareHistoryRecord, error) {
	start := parseDateOrFallback(startDate)
	end := parseDateOrFallback(endDate)
	if end.Before(start) {
		start, end = end, start
	}
	allRows := make([]domain.ShareHistoryRecord, 0, len(s.symbols))
	for day := start; !day.After(end); day = day.AddDate(0, 0, 1) {
		if !day.Equal(start) {
			if err := waitForInterval(ctx, s.queryInterval); err != nil {
				return nil, err
			}
		}
		date := day.Format("2006-01-02")
		rows, err := s.fetchTrackedDate(ctx, date)
		if err != nil {
			return nil, err
		}
		if len(rows) == 0 {
			s.logger.Debug("sse share history date skipped; no tracked rows", "date", date)
			continue
		}
		s.logger.Info("sse share history date fetched", "date", date, "rows", len(rows))
		allRows = append(allRows, rows...)
	}
	return dedupeRows(allRows), nil
}

func (s *SSEService) fetchTrackedDate(ctx context.Context, date string) ([]domain.ShareHistoryRecord, error) {
	allRows := make([]domain.ShareHistoryRecord, 0, len(s.symbols))
	for index, fundType := range []string{"ETF", "LOF"} {
		if index > 0 {
			if err := waitForInterval(ctx, s.queryInterval); err != nil {
				return nil, err
			}
		}
		rows, err := s.client.FetchFundSizesByDate(ctx, date, fundType)
		if err != nil {
			return nil, err
		}
		for _, row := range rows {
			if s.symbols[strings.ToUpper(strings.TrimSpace(row.Symbol))] {
				allRows = append(allRows, row)
			}
		}
	}
	return allRows, nil
}

func trackedSymbolsByPrefix(symbols []string, prefix string) map[string]bool {
	prefix = strings.ToUpper(strings.TrimSpace(prefix))
	out := map[string]bool{}
	for _, symbol := range symbols {
		symbol = strings.ToUpper(strings.TrimSpace(symbol))
		if strings.HasPrefix(symbol, prefix) {
			out[symbol] = true
		}
	}
	return out
}
