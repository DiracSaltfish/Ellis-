package navsync

import (
	"context"
	"fmt"
	"log/slog"
	"strings"
	"sync"
	"time"

	"newnavnav/internal/domain"
	"newnavnav/internal/ingest/eastmoney"
)

type EastmoneyClient interface {
	FetchNetValues(ctx context.Context, fundCode string, startDate string, endDate string) ([]eastmoney.NetValueRecord, error)
}

type Repository interface {
	LatestNetValueDate(ctx context.Context, symbol string) (string, bool, error)
	UpsertNetValues(ctx context.Context, values []domain.NetValue) error
}

type EastmoneyService struct {
	client       EastmoneyClient
	repository   Repository
	symbols      []string
	lookbackDays int
	delay        time.Duration
	logger       *slog.Logger

	runMu    sync.Mutex
	statusMu sync.RWMutex
	status   EastmoneyStatus
}

type EastmoneyOptions struct {
	Client       EastmoneyClient
	Repository   Repository
	Symbols      []string
	LookbackDays int
	Delay        time.Duration
	Logger       *slog.Logger
}

type EastmoneyStatus struct {
	Enabled             bool                       `json:"enabled"`
	Running             bool                       `json:"running"`
	RunID               int64                      `json:"run_id"`
	TotalSymbols        int                        `json:"total_symbols"`
	CurrentIndex        int                        `json:"current_index"`
	CurrentSymbol       string                     `json:"current_symbol,omitempty"`
	CurrentStartDate    string                     `json:"current_start_date,omitempty"`
	CurrentEndDate      string                     `json:"current_end_date,omitempty"`
	NextRunAt           *time.Time                 `json:"next_run_at,omitempty"`
	LastStartedAt       *time.Time                 `json:"last_started_at,omitempty"`
	LastFinishedAt      *time.Time                 `json:"last_finished_at,omitempty"`
	LastDurationSeconds float64                    `json:"last_duration_seconds,omitempty"`
	LastSuccess         bool                       `json:"last_success"`
	LastError           string                     `json:"last_error,omitempty"`
	Requests            int                        `json:"requests"`
	Written             int                        `json:"written"`
	Skipped             int                        `json:"skipped"`
	Failed              int                        `json:"failed"`
	NoRows              int                        `json:"no_rows"`
	LastWrittenAt       *time.Time                 `json:"last_written_at,omitempty"`
	LastWrittenSymbol   string                     `json:"last_written_symbol,omitempty"`
	LastWrittenDate     string                     `json:"last_written_date,omitempty"`
	LastWrittenRows     int                        `json:"last_written_rows,omitempty"`
	RecentSymbolResults []EastmoneySymbolRunStatus `json:"recent_symbol_results"`
}

type EastmoneySymbolRunStatus struct {
	At        time.Time `json:"at"`
	Symbol    string    `json:"symbol"`
	FundCode  string    `json:"fund_code,omitempty"`
	Status    string    `json:"status"`
	StartDate string    `json:"start_date,omitempty"`
	EndDate   string    `json:"end_date,omitempty"`
	Rows      int       `json:"rows,omitempty"`
	Error     string    `json:"error,omitempty"`
}

func NewEastmoneyService(opts EastmoneyOptions) *EastmoneyService {
	lookback := opts.LookbackDays
	if lookback <= 0 {
		lookback = 30
	}
	delay := opts.Delay
	if delay < 0 {
		delay = 0
	}
	logger := opts.Logger
	if logger == nil {
		logger = slog.Default()
	}
	symbols := uniqueFundSymbols(opts.Symbols)
	return &EastmoneyService{
		client:       opts.Client,
		repository:   opts.Repository,
		symbols:      symbols,
		lookbackDays: lookback,
		delay:        delay,
		logger:       logger,
		status: EastmoneyStatus{
			Enabled:      true,
			TotalSymbols: len(symbols),
		},
	}
}

func (s *EastmoneyService) SyncMissing(ctx context.Context, today time.Time) error {
	s.runMu.Lock()
	defer s.runMu.Unlock()

	if s.client == nil || s.repository == nil {
		s.finishRunError(fmt.Errorf("eastmoney nav sync requires client and repository"))
		return fmt.Errorf("eastmoney nav sync requires client and repository")
	}
	s.beginRun(today)
	if len(s.symbols) == 0 {
		s.logger.Info("eastmoney nav sync skipped; no fund symbols")
		s.finishRun(nil)
		return nil
	}

	endDate := today.Format("2006-01-02")
	var runErr error
	requests := 0
	written := 0
	skipped := 0
	for idx, symbol := range s.symbols {
		if idx > 0 && requests > 0 && s.delay > 0 {
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(s.delay):
			}
		}

		startDate, shouldFetch, err := s.fetchWindow(ctx, symbol, today)
		if err != nil {
			s.logger.Warn("eastmoney nav local check failed", "symbol", symbol, "error", err)
			s.recordSymbolResult(symbol, "", "local_check_failed", "", endDate, 0, err)
			runErr = err
			continue
		}
		s.setCurrentSymbol(idx+1, symbol, startDate, endDate)
		if !shouldFetch {
			skipped++
			s.incrementSkipped()
			s.recordSymbolResult(symbol, eastmoneyFundCode(symbol), "current", "", endDate, 0, nil)
			s.logger.Info("eastmoney nav skipped; local data is current", "symbol", symbol, "date", endDate)
			continue
		}

		fundCode := eastmoneyFundCode(symbol)
		requests++
		s.incrementRequests()
		records, err := s.client.FetchNetValues(ctx, fundCode, startDate, endDate)
		if err != nil {
			s.logger.Warn("eastmoney nav fetch failed", "symbol", symbol, "fund_code", fundCode, "start_date", startDate, "end_date", endDate, "error", err)
			s.recordSymbolResult(symbol, fundCode, "fetch_failed", startDate, endDate, 0, err)
			runErr = err
			continue
		}
		values := make([]domain.NetValue, 0, len(records))
		for _, record := range records {
			values = append(values, domain.NetValue{
				Symbol:     symbol,
				Date:       record.Date,
				NAV:        record.UnitNAV,
				Source:     "eastmoney",
				Confidence: "official",
			})
		}
		if len(values) == 0 {
			s.logger.Warn("eastmoney nav returned no new rows", "symbol", symbol, "fund_code", fundCode, "start_date", startDate, "end_date", endDate)
			s.incrementNoRows()
			s.recordSymbolResult(symbol, fundCode, "no_rows", startDate, endDate, 0, nil)
			continue
		}
		if err := s.repository.UpsertNetValues(ctx, values); err != nil {
			s.logger.Warn("eastmoney nav write failed", "symbol", symbol, "rows", len(values), "error", err)
			s.recordSymbolResult(symbol, fundCode, "write_failed", startDate, endDate, len(values), err)
			runErr = err
			continue
		}
		written += len(values)
		s.recordWrite(symbol, values[len(values)-1].Date, len(values))
		s.recordSymbolResult(symbol, fundCode, "synced", startDate, endDate, len(values), nil)
		s.logger.Info("eastmoney nav synced", "symbol", symbol, "fund_code", fundCode, "rows", len(values), "start_date", startDate, "end_date", endDate)
	}

	s.logger.Info("eastmoney nav sync finished", "symbols", len(s.symbols), "skipped", skipped, "requests", requests, "written", written)
	s.finishRun(runErr)
	return runErr
}

func (s *EastmoneyService) Status() EastmoneyStatus {
	s.statusMu.RLock()
	defer s.statusMu.RUnlock()
	status := s.status
	status.RecentSymbolResults = append([]EastmoneySymbolRunStatus(nil), s.status.RecentSymbolResults...)
	return status
}

func (s *EastmoneyService) SetNextRunAt(next time.Time) {
	s.statusMu.Lock()
	defer s.statusMu.Unlock()
	s.status.NextRunAt = ptrTime(next)
}

func DisabledStatus(reason string) EastmoneyStatus {
	return EastmoneyStatus{
		Enabled:   false,
		LastError: reason,
	}
}

func (s *EastmoneyService) beginRun(today time.Time) {
	now := time.Now()
	s.statusMu.Lock()
	defer s.statusMu.Unlock()
	s.status.RunID++
	s.status.Running = true
	s.status.TotalSymbols = len(s.symbols)
	s.status.CurrentIndex = 0
	s.status.CurrentSymbol = ""
	s.status.CurrentStartDate = ""
	s.status.CurrentEndDate = today.Format("2006-01-02")
	s.status.LastStartedAt = ptrTime(now)
	s.status.LastFinishedAt = nil
	s.status.LastDurationSeconds = 0
	s.status.LastError = ""
	s.status.Requests = 0
	s.status.Written = 0
	s.status.Skipped = 0
	s.status.Failed = 0
	s.status.NoRows = 0
}

func (s *EastmoneyService) finishRun(err error) {
	finished := time.Now()
	s.statusMu.Lock()
	defer s.statusMu.Unlock()
	s.status.Running = false
	s.status.CurrentSymbol = ""
	s.status.CurrentStartDate = ""
	s.status.CurrentEndDate = ""
	s.status.LastFinishedAt = ptrTime(finished)
	if s.status.LastStartedAt != nil {
		s.status.LastDurationSeconds = finished.Sub(*s.status.LastStartedAt).Seconds()
	}
	s.status.LastSuccess = err == nil
	if err != nil {
		s.status.LastError = err.Error()
	} else {
		s.status.LastError = ""
	}
}

func (s *EastmoneyService) finishRunError(err error) {
	s.beginRun(time.Now())
	s.finishRun(err)
}

func (s *EastmoneyService) setCurrentSymbol(index int, symbol string, startDate string, endDate string) {
	s.statusMu.Lock()
	defer s.statusMu.Unlock()
	s.status.CurrentIndex = index
	s.status.CurrentSymbol = symbol
	s.status.CurrentStartDate = startDate
	s.status.CurrentEndDate = endDate
}

func (s *EastmoneyService) incrementRequests() {
	s.statusMu.Lock()
	defer s.statusMu.Unlock()
	s.status.Requests++
}

func (s *EastmoneyService) incrementSkipped() {
	s.statusMu.Lock()
	defer s.statusMu.Unlock()
	s.status.Skipped++
}

func (s *EastmoneyService) incrementNoRows() {
	s.statusMu.Lock()
	defer s.statusMu.Unlock()
	s.status.NoRows++
}

func (s *EastmoneyService) recordWrite(symbol string, date string, rows int) {
	now := time.Now()
	s.statusMu.Lock()
	defer s.statusMu.Unlock()
	s.status.Written += rows
	s.status.LastWrittenAt = ptrTime(now)
	s.status.LastWrittenSymbol = symbol
	s.status.LastWrittenDate = date
	s.status.LastWrittenRows = rows
}

func (s *EastmoneyService) recordSymbolResult(symbol string, fundCode string, status string, startDate string, endDate string, rows int, err error) {
	result := EastmoneySymbolRunStatus{
		At:        time.Now(),
		Symbol:    symbol,
		FundCode:  fundCode,
		Status:    status,
		StartDate: startDate,
		EndDate:   endDate,
		Rows:      rows,
	}
	s.statusMu.Lock()
	defer s.statusMu.Unlock()
	if err != nil {
		result.Error = err.Error()
		s.status.Failed++
		s.status.LastError = result.Error
	}
	s.status.RecentSymbolResults = append([]EastmoneySymbolRunStatus{result}, s.status.RecentSymbolResults...)
	if len(s.status.RecentSymbolResults) > 80 {
		s.status.RecentSymbolResults = s.status.RecentSymbolResults[:80]
	}
}

func ptrTime(value time.Time) *time.Time {
	return &value
}

func (s *EastmoneyService) fetchWindow(ctx context.Context, symbol string, today time.Time) (string, bool, error) {
	latest, ok, err := s.repository.LatestNetValueDate(ctx, symbol)
	if err != nil {
		return "", false, err
	}
	if ok {
		latestDate, err := time.ParseInLocation("2006-01-02", latest, time.Local)
		if err != nil {
			return "", false, err
		}
		if !latestDate.Before(truncateDate(today)) {
			return "", false, nil
		}
		return latestDate.AddDate(0, 0, 1).Format("2006-01-02"), true, nil
	}
	return today.AddDate(0, 0, -s.lookbackDays).Format("2006-01-02"), true, nil
}

func truncateDate(value time.Time) time.Time {
	year, month, day := value.Date()
	return time.Date(year, month, day, 0, 0, 0, 0, value.Location())
}

func uniqueFundSymbols(symbols []string) []string {
	seen := map[string]bool{}
	var out []string
	for _, symbol := range symbols {
		symbol = strings.ToUpper(strings.TrimSpace(symbol))
		if domain.IsExcludedSymbol(symbol) || !isChinaFundSymbol(symbol) || seen[symbol] {
			continue
		}
		seen[symbol] = true
		out = append(out, symbol)
	}
	return out
}

func isChinaFundSymbol(symbol string) bool {
	if len(symbol) != 8 {
		return false
	}
	prefix := symbol[:2]
	if prefix != "SH" && prefix != "SZ" {
		return false
	}
	for _, ch := range symbol[2:] {
		if ch < '0' || ch > '9' {
			return false
		}
	}
	return true
}

func eastmoneyFundCode(symbol string) string {
	if len(symbol) == 8 && (strings.HasPrefix(symbol, "SH") || strings.HasPrefix(symbol, "SZ")) {
		return symbol[2:]
	}
	return symbol
}
