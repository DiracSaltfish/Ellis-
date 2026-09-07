package runtimestatus

import (
	"context"
	"errors"
	"testing"
	"time"
)

type fakeSyncer struct {
	err error
}

func (s fakeSyncer) SyncMissing(ctx context.Context, today time.Time) error {
	return s.err
}

func TestTrackedDailySyncerRecordsSuccessAndSchedule(t *testing.T) {
	task := NewTask("purchase_status", "申购状态", true, "08:05", "")
	tracked := task.Wrap(fakeSyncer{})
	next := time.Date(2026, 6, 12, 8, 5, 0, 0, time.Local)
	tracked.SetNextRunAt(next)

	if err := tracked.SyncMissing(context.Background(), time.Date(2026, 6, 12, 9, 0, 0, 0, time.Local)); err != nil {
		t.Fatal(err)
	}

	status := task.Status()
	if status.Status != "ok" || status.Running || !status.LastSuccess {
		t.Fatalf("status = %+v, want ok finished success", status)
	}
	if status.NextRunAt == nil || !status.NextRunAt.Equal(next) {
		t.Fatalf("next run = %v, want %v", status.NextRunAt, next)
	}
	if status.RunCount != 1 || status.SuccessCount != 1 || status.FailureCount != 0 {
		t.Fatalf("counts = %+v, want one success", status)
	}
}

func TestTrackedDailySyncerRecordsError(t *testing.T) {
	task := NewTask("daily_calibration", "每日校准", true, "22:45", "")
	tracked := task.Wrap(fakeSyncer{err: errors.New("boom")})

	err := tracked.SyncMissing(context.Background(), time.Now())
	if err == nil {
		t.Fatal("expected error")
	}

	status := task.Status()
	if status.Status != "error" || status.LastError != "boom" || status.LastSuccess {
		t.Fatalf("status = %+v, want error boom", status)
	}
	if status.RunCount != 1 || status.SuccessCount != 0 || status.FailureCount != 1 {
		t.Fatalf("counts = %+v, want one failure", status)
	}
}
