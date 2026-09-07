package monitoring

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"path/filepath"
	"testing"
	"time"

	"newnavnav/internal/hkconnectfx"
	"newnavnav/internal/snapshot"
)

func newDebounceManager(t *testing.T, sender Sender) (*IncidentManager, *time.Time) {
	t.Helper()
	manager := NewIncidentManager(sender, 6*time.Minute, filepath.Join(t.TempDir(), "state.json"), slog.New(slog.NewTextHandler(io.Discard, nil)))
	now := time.Date(2026, 9, 4, 10, 0, 0, 123456000, time.FixedZone("CST", 8*60*60))
	manager.now = func() time.Time { return now }
	return manager, &now
}

func TestRealtimeConfirmationPolicy(t *testing.T) {
	for _, key := range []string{
		"backend.public.missing", "backend.public.stale", "backend.public.websocket", "backend.public.upload",
		"backend.private.missing.nikkei225", "backend.private.stale.germany", "backend.private.ib.silver",
		"backend.core.snapshot", "backend.core.snapshot_source",
	} {
		if got := incidentConfirmationDelay(key); got != time.Minute {
			t.Errorf("%s: delay = %s", key, got)
		}
	}
	for _, key := range []string{"backend.core.database", "backend.core.task.pcf", "backend.private.pcf.nikkei225", "backend.private.fx.germany", "backend.private.hkfx_usdcny"} {
		if got := incidentConfirmationDelay(key); got != 0 {
			t.Errorf("%s: existing policy changed: %s", key, got)
		}
	}
}

func TestTransientRealtimeFailureCancelsSilentlyAndRestartsTimer(t *testing.T) {
	sender := &recordingSender{}
	m, now := newDebounceManager(t, sender)
	ctx := context.Background()
	problem := Problem{Key: "backend.public.upload", Title: "上传停止"}
	reconcile := func(bad bool) {
		var problems []Problem
		if bad {
			problems = []Problem{problem}
		}
		m.ReconcileScope(ctx, "backend.public.", problems)
	}
	reconcile(true)
	*now = now.Add(59 * time.Second)
	reconcile(true)
	if len(sender.messages) != 0 {
		t.Fatal("alert sent before 60 seconds")
	}
	reconcile(false)
	if len(m.state.Incidents) != 0 || len(sender.messages) != 0 {
		t.Fatal("transient failure was not cancelled silently on first healthy observation")
	}
	*now = now.Add(time.Second)
	reconcile(true)
	*now = now.Add(time.Minute - time.Nanosecond)
	reconcile(true)
	if len(sender.messages) != 0 {
		t.Fatal("recurrence reused old timer")
	}
	*now = now.Add(time.Nanosecond)
	reconcile(true)
	if len(sender.messages) != 1 {
		t.Fatalf("want one confirmed alert, got %d", len(sender.messages))
	}
}

func TestIndependentRealtimeIncidentTimers(t *testing.T) {
	sender := &recordingSender{}
	m, now := newDebounceManager(t, sender)
	a := Problem{Key: "backend.private.ib.nikkei225", Title: "日经"}
	b := Problem{Key: "backend.private.ib.germany", Title: "德国"}
	m.Reconcile(context.Background(), []Problem{a})
	*now = now.Add(30 * time.Second)
	m.Reconcile(context.Background(), []Problem{a, b})
	*now = now.Add(30 * time.Second)
	m.Reconcile(context.Background(), []Problem{a, b})
	if len(sender.messages) != 1 || sender.messages[0].title != "【NAVNAV 后端异常】日经" {
		t.Fatalf("independent incident sent too early: %+v", sender.messages)
	}
	*now = now.Add(30 * time.Second)
	m.Reconcile(context.Background(), []Problem{a, b})
	if len(sender.messages) != 2 {
		t.Fatal("second incident not confirmed at its own deadline")
	}
}

func TestPauseDiscardsOnlyPendingAndResumeStartsNewTimer(t *testing.T) {
	sender := &recordingSender{}
	m, now := newDebounceManager(t, sender)
	ctx := context.Background()
	a := Problem{Key: "backend.public.upload", Title: "已确认"}
	b := Problem{Key: "backend.public.websocket", Title: "待确认"}
	m.Reconcile(ctx, []Problem{a})
	*now = now.Add(time.Minute)
	m.Reconcile(ctx, []Problem{a, b})
	m.PauseScope("backend.public.")
	if len(m.state.Incidents) != 1 || m.state.Incidents[a.Key].LastNotifiedAt.IsZero() || len(sender.messages) != 1 {
		t.Fatalf("pause changed delivered state or sent a recovery: %+v", m.state.Incidents)
	}
	*now = now.Add(time.Hour)
	m.Reconcile(ctx, []Problem{a, b})
	if len(sender.messages) != 2 || !m.state.Incidents[b.Key].LastNotifiedAt.IsZero() {
		t.Fatal("paused pending incident was sent on resume")
	}
	*now = now.Add(time.Minute)
	m.Reconcile(ctx, []Problem{a, b})
	if len(sender.messages) != 3 {
		t.Fatal("resumed pending incident did not confirm after a new minute")
	}
}

func TestRestartDiscardsPendingButPreservesReceipts(t *testing.T) {
	sender := &recordingSender{}
	m, now := newDebounceManager(t, sender)
	ctx := context.Background()
	a := Problem{Key: "backend.public.upload", Title: "已确认"}
	b := Problem{Key: "backend.public.websocket", Title: "待确认"}
	m.Reconcile(ctx, []Problem{a})
	*now = now.Add(time.Minute)
	m.Reconcile(ctx, []Problem{a, b})
	m.SendOnce(ctx, "scheduled", "report", "ok")
	restored := NewIncidentManager(sender, 6*time.Minute, m.statePath, m.logger)
	restored.now = func() time.Time { return *now }
	if len(restored.state.Incidents) != 1 || restored.state.Incidents[a.Key].LastNotifiedAt.IsZero() || restored.state.Sent["scheduled"].IsZero() {
		t.Fatalf("incorrect restored state: %+v", restored.state)
	}
	*now = now.Add(2 * time.Minute)
	restored.Reconcile(ctx, []Problem{a, b})
	restored.SendOnce(ctx, "scheduled", "report", "ok")
	if len(sender.messages) != 2 {
		t.Fatal("restart reset repeat cooldown or reused pending timer")
	}
}

type failedDebounceSender struct{ attempts int }

func (s *failedDebounceSender) Send(context.Context, string, string) error {
	s.attempts++
	return errors.New("simulated failure")
}

func TestFailedNotificationDoesNotProduceOrphanRecovery(t *testing.T) {
	sender := &failedDebounceSender{}
	m, now := newDebounceManager(t, sender)
	ctx := context.Background()
	problems := []Problem{{Key: "backend.public.upload"}}
	m.Reconcile(ctx, problems)
	*now = now.Add(time.Minute)
	m.Reconcile(ctx, problems)
	*now = now.Add(29 * time.Second)
	m.Reconcile(ctx, problems)
	if sender.attempts != 1 {
		t.Fatal("failed alert retry ignored 30-second backoff")
	}
	*now = now.Add(time.Second)
	m.Reconcile(ctx, problems)
	if sender.attempts != 2 {
		t.Fatal("failed alert was never retried")
	}
	for range 4 {
		*now = now.Add(5 * time.Second)
		m.Reconcile(ctx, nil)
	}
	if sender.attempts != 2 || len(m.state.Incidents) != 0 {
		t.Fatal("recovery attempted despite no delivered incident")
	}
}

func TestCFETSAndScheduledMessagesKeepExistingTiming(t *testing.T) {
	sender := &recordingSender{}
	m, _ := newDebounceManager(t, sender)
	m.Reconcile(context.Background(), []Problem{{Key: "backend.private.fx.germany", Title: "CFETS"}})
	m.SendOnce(context.Background(), "morning", "report", "status")
	if len(sender.messages) != 2 {
		t.Fatal("CFETS or scheduled report was delayed")
	}
}

func TestBackendTickClearsPendingWhenSessionPaused(t *testing.T) {
	for _, tc := range []struct {
		name string
		at   time.Time
		key  string
	}{
		{"lunch", time.Date(2026, 9, 4, 11, 30, 0, 0, time.Local), "backend.public.upload"},
		{"public close", time.Date(2026, 9, 4, 14, 57, 0, 0, time.Local), "backend.public.upload"},
		{"private close", time.Date(2026, 9, 4, 15, 0, 0, 0, time.Local), "backend.private.ib.germany"},
		{"weekend", time.Date(2026, 9, 5, 10, 0, 0, 0, time.Local), "backend.private.ib.germany"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			sender := &recordingSender{}
			m, now := newDebounceManager(t, sender)
			*now = tc.at.Add(-30 * time.Second)
			m.Reconcile(context.Background(), []Problem{{Key: tc.key}})
			*now = tc.at
			runner := &BackendRunner{manager: m, sources: BackendSources{
				DatabaseAvailable: true,
				Snapshot: func(bool) snapshot.DebugStatus {
					return snapshot.DebugStatus{SnapshotStatus: map[string]any{"last_refresh_at": *now}}
				},
				HKConnectFX: func() hkconnectfx.Snapshot { return healthyHKFX(*now) },
			}}
			runner.tick(context.Background(), *now)
			if _, exists := m.state.Incidents[tc.key]; exists {
				t.Fatal("pending incident crossed inactive window")
			}
			if len(sender.messages) != 0 {
				t.Fatalf("pause emitted messages: %+v", sender.messages)
			}
		})
	}
}

func TestNewBusinessDateRequiresNewConfirmation(t *testing.T) {
	sender := &recordingSender{}
	m, now := newDebounceManager(t, sender)
	p := []Problem{{Key: "backend.public.upload", Title: "upload"}}
	m.Reconcile(context.Background(), p)
	*now = now.Add(time.Minute)
	m.Reconcile(context.Background(), p)
	if len(sender.messages) != 1 {
		t.Fatal("initial notification missing")
	}
	*now = now.Add(72 * time.Hour)
	m.Reconcile(context.Background(), p)
	if len(sender.messages) != 1 {
		t.Fatal("previous trading day's notification bypassed confirmation")
	}
	*now = now.Add(time.Minute)
	m.Reconcile(context.Background(), p)
	if len(sender.messages) != 2 {
		t.Fatal("new confirmed incident missing")
	}
}

func TestSilverBreakDoesNotDeclareRecovery(t *testing.T) {
	sender := &recordingSender{}
	m, now := newDebounceManager(t, sender)
	p := []Problem{{Key: "backend.private.ib.silver", Title: "silver"}}
	m.Reconcile(context.Background(), p)
	*now = now.Add(time.Minute)
	m.Reconcile(context.Background(), p)
	for i := 0; i < 4; i++ {
		*now = now.Add(time.Minute)
		m.ReconcileScope(context.Background(), "backend.private.", nil, "backend.private.ib.silver")
	}
	if len(sender.messages) != 1 || len(m.state.Incidents) != 1 {
		t.Fatal("exchange break was mistaken for recovery")
	}
}
