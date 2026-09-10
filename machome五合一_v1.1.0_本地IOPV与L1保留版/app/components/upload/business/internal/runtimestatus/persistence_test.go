package runtimestatus

import (
	"context"
	"testing"
	"time"
)

func TestCompletedCalibrationSurvivesRestart(t *testing.T) {
	dir := t.TempDir()
	tracker, err := NewPersistentTracker(dir)
	if err != nil {
		t.Fatal(err)
	}
	task := tracker.Register("daily_calibration", "calibration", "22:45")
	if err := task.Run(context.Background(), time.Now(), func(context.Context, time.Time) error { return nil }); err != nil {
		t.Fatal(err)
	}
	next, _ := NewPersistentTracker(dir)
	restored := next.Register("daily_calibration", "calibration", "22:45").Status()
	if !restored.LastSuccess || restored.LastFinishedAt == nil || restored.SuccessCount != 1 || restored.Running {
		t.Fatalf("lost completion evidence: %+v", restored)
	}
	changed := next.Register("daily_calibration", "calibration", "23:00").Status()
	if changed.LastSuccess {
		t.Fatal("changed schedule reused an incompatible receipt")
	}
}

func TestInterruptedTaskDoesNotRestorePreviousSuccess(t *testing.T) {
	dir := t.TempDir()
	tracker, _ := NewPersistentTracker(dir)
	task := tracker.Register("daily_calibration", "calibration", "22:45")
	task.mu.Lock()
	task.status.Running = true
	task.status.LastSuccess = true
	task.persistLocked()
	task.mu.Unlock()
	next, _ := NewPersistentTracker(dir)
	status := next.Register("daily_calibration", "calibration", "22:45").Status()
	if status.LastSuccess || status.Running || status.Status != "interrupted" {
		t.Fatalf("unconfirmed work marked successful: %+v", status)
	}
}
