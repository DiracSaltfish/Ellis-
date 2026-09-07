package navsync

import (
	"context"
	"log/slog"
	"time"
)

type DailySyncer interface {
	SyncMissing(ctx context.Context, today time.Time) error
}

type ScheduleAware interface {
	SetNextRunAt(next time.Time)
}

func RunDaily(ctx context.Context, syncer DailySyncer, clockTime string, logger *slog.Logger) {
	RunDailyNamed(ctx, syncer, clockTime, logger, "eastmoney nav", 22, 30)
}

func RunDailyNamed(ctx context.Context, syncer DailySyncer, clockTime string, logger *slog.Logger, name string, fallbackHour int, fallbackMinute int) {
	if logger == nil {
		logger = slog.Default()
	}
	if name == "" {
		name = "daily"
	}
	hour, minute, ok := parseClockTime(clockTime)
	if !ok {
		hour, minute = fallbackHour, fallbackMinute
	}
	for {
		next := nextRunTime(time.Now(), hour, minute)
		if aware, ok := syncer.(ScheduleAware); ok {
			aware.SetNextRunAt(next)
		}
		logger.Info(name+" sync scheduled", "next_run", next.Format(time.RFC3339), "interval_time", clockTime)
		timer := time.NewTimer(time.Until(next))
		select {
		case <-ctx.Done():
			timer.Stop()
			return
		case <-timer.C:
			if err := syncer.SyncMissing(ctx, time.Now()); err != nil {
				logger.Warn(name+" sync finished with error", "error", err)
			}
		}
	}
}

func parseClockTime(value string) (int, int, bool) {
	parsed, err := time.Parse("15:04", value)
	if err != nil {
		return 0, 0, false
	}
	return parsed.Hour(), parsed.Minute(), true
}

func nextRunTime(now time.Time, hour int, minute int) time.Time {
	year, month, day := now.Date()
	next := time.Date(year, month, day, hour, minute, 0, 0, now.Location())
	if !next.After(now) {
		next = next.AddDate(0, 0, 1)
	}
	return next
}
