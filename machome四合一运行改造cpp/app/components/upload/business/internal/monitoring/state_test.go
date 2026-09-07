package monitoring

import (
	"context"
	"io"
	"log/slog"
	"path/filepath"
	"testing"
	"time"
)

type recordedMessage struct {
	title   string
	content string
}

type recordingSender struct {
	messages []recordedMessage
}

func (s *recordingSender) Send(_ context.Context, title string, content string) error {
	s.messages = append(s.messages, recordedMessage{title: title, content: content})
	return nil
}

func TestIncidentManagerThrottlesRepeatsAndResetsAfterRecovery(t *testing.T) {
	sender := &recordingSender{}
	manager := NewIncidentManager(sender, 6*time.Minute, filepath.Join(t.TempDir(), "state.json"), slog.New(slog.NewTextHandler(io.Discard, nil)))
	now := time.Date(2026, 9, 1, 9, 0, 0, 0, time.FixedZone("CST", 8*60*60))
	manager.now = func() time.Time { return now }
	problem := Problem{Key: "backend.private.ib", Title: "IB 异常", Detail: "stale", Severity: "critical"}

	manager.ReconcileScope(context.Background(), "backend.private.", []Problem{problem})
	if len(sender.messages) != 0 {
		t.Fatalf("unconfirmed incident sent %d messages", len(sender.messages))
	}
	now = now.Add(time.Minute)
	manager.ReconcileScope(context.Background(), "backend.private.", []Problem{problem})
	if len(sender.messages) != 1 {
		t.Fatalf("confirmed observation sent %d messages", len(sender.messages))
	}
	now = now.Add(5*time.Minute + 59*time.Second)
	manager.ReconcileScope(context.Background(), "backend.private.", []Problem{problem})
	if len(sender.messages) != 1 {
		t.Fatalf("repeat before six minutes sent %d messages", len(sender.messages))
	}
	now = now.Add(time.Second)
	manager.ReconcileScope(context.Background(), "backend.private.", []Problem{problem})
	if len(sender.messages) != 2 {
		t.Fatalf("repeat at six minutes sent %d messages", len(sender.messages))
	}
	for range 3 {
		now = now.Add(5 * time.Second)
		manager.ReconcileScope(context.Background(), "backend.private.", nil)
	}
	if len(sender.messages) != 3 {
		t.Fatalf("recovery sent %d total messages", len(sender.messages))
	}
	now = now.Add(time.Second)
	manager.ReconcileScope(context.Background(), "backend.private.", []Problem{problem})
	if len(sender.messages) != 3 {
		t.Fatalf("new incident notified before confirmation: %d total messages", len(sender.messages))
	}
	now = now.Add(time.Minute)
	manager.ReconcileScope(context.Background(), "backend.private.", []Problem{problem})
	if len(sender.messages) != 4 {
		t.Fatalf("new incident after recovery sent %d total messages", len(sender.messages))
	}
}

func TestIncidentManagerDoesNotResolveOtherScopes(t *testing.T) {
	sender := &recordingSender{}
	manager := NewIncidentManager(sender, 6*time.Minute, filepath.Join(t.TempDir(), "state.json"), slog.New(slog.NewTextHandler(io.Discard, nil)))
	now := time.Date(2026, 9, 1, 9, 0, 0, 0, time.UTC)
	manager.now = func() time.Time { return now }
	manager.ReconcileScope(context.Background(), "backend.private.", []Problem{{Key: "backend.private.ib", Title: "IB", Detail: "down"}})
	for range 5 {
		now = now.Add(5 * time.Second)
		manager.ReconcileScope(context.Background(), "backend.core.", nil)
	}
	if _, exists := manager.state.Incidents["backend.private.ib"]; !exists {
		t.Fatal("private incident was incorrectly resolved by core reconciliation")
	}
}
