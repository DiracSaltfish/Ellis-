package fxsync

import (
	"context"
	"log/slog"
	"strings"
	"sync"
	"time"
	_ "time/tzdata"

	"newnavnav/internal/domain"
	"newnavnav/internal/ingest/safe"
)

type Repository interface {
	UpsertFXCentralParity(ctx context.Context, rates []domain.FXCentralParity) error
	HasFXCentralParity(ctx context.Context, pair string, rateDate string) (bool, error)
}

type Options struct {
	Client       *safe.Client
	Repository   Repository
	Logger       *slog.Logger
	LookbackDays int
}

type Service struct {
	client       *safe.Client
	repository   Repository
	logger       *slog.Logger
	lookbackDays int

	mu        sync.RWMutex
	lastRunAt time.Time
	lastError string
	nextRunAt time.Time
}

func NewService(opts Options) *Service {
	logger := opts.Logger
	if logger == nil {
		logger = slog.Default()
	}
	lookbackDays := opts.LookbackDays
	if lookbackDays <= 0 {
		lookbackDays = 90
	}
	return &Service{
		client:       opts.Client,
		repository:   opts.Repository,
		logger:       logger,
		lookbackDays: lookbackDays,
	}
}

func (s *Service) SetNextRunAt(next time.Time) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.nextRunAt = next
}

func (s *Service) SyncMissing(ctx context.Context, now time.Time) error {
	start := localDate(now).AddDate(0, 0, -s.lookbackDays+1)
	end := localDate(now)
	rows, err := s.client.FetchRange(ctx, start, end)
	if err != nil {
		s.setRunStatus(now, err)
		return err
	}
	if err := s.repository.UpsertFXCentralParity(ctx, rows); err != nil {
		s.setRunStatus(now, err)
		return err
	}
	s.setRunStatus(now, nil)
	s.logger.Info("safe central parity synced", "rows", len(rows), "start", start.Format("2006-01-02"), "end", end.Format("2006-01-02"))
	return nil
}

func (s *Service) RunTodayEnsureLoop(ctx context.Context) {
	ticker := time.NewTicker(time.Minute)
	defer ticker.Stop()
	for {
		s.ensureTodayIfNeeded(ctx, time.Now())
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

func (s *Service) ensureTodayIfNeeded(ctx context.Context, now time.Time) {
	localNow := now.In(shanghaiLocation())
	if localNow.Weekday() == time.Saturday || localNow.Weekday() == time.Sunday {
		return
	}
	minute := localNow.Hour()*60 + localNow.Minute()
	if minute < 9*60+30 || minute > 10*60+30 {
		return
	}
	dateText := localNow.Format("2006-01-02")
	ok, err := s.repository.HasFXCentralParity(ctx, "USDCNY", dateText)
	if err != nil {
		s.logger.Warn("safe central parity existence check failed", "date", dateText, "error", err)
		return
	}
	if ok {
		return
	}
	if err := s.syncDate(ctx, localNow); err != nil {
		s.logger.Warn("safe central parity today retry failed", "date", dateText, "error", err)
	}
}

func (s *Service) syncDate(ctx context.Context, day time.Time) error {
	rows, err := s.client.FetchRange(ctx, day, day)
	if err != nil {
		return err
	}
	if err := s.repository.UpsertFXCentralParity(ctx, rows); err != nil {
		return err
	}
	s.setRunStatus(time.Now(), nil)
	s.logger.Info("safe central parity synced for day", "date", day.Format("2006-01-02"), "rows", len(rows))
	return nil
}

func (s *Service) setRunStatus(now time.Time, err error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.lastRunAt = now
	if err != nil {
		s.lastError = err.Error()
		return
	}
	s.lastError = ""
}

func localDate(now time.Time) time.Time {
	local := now.In(shanghaiLocation())
	return time.Date(local.Year(), local.Month(), local.Day(), 0, 0, 0, 0, local.Location())
}

func shanghaiLocation() *time.Location {
	loc, err := time.LoadLocation("Asia/Shanghai")
	if err != nil {
		return time.Local
	}
	return loc
}

func (s *Service) Status() map[string]any {
	s.mu.RLock()
	defer s.mu.RUnlock()
	status := "ok"
	if strings.TrimSpace(s.lastError) != "" {
		status = "error"
	}
	return map[string]any{
		"status":      status,
		"last_run_at": zeroTimeString(s.lastRunAt),
		"last_error":  s.lastError,
		"next_run_at": zeroTimeString(s.nextRunAt),
	}
}

func zeroTimeString(value time.Time) string {
	if value.IsZero() {
		return ""
	}
	return value.Format(time.RFC3339)
}
