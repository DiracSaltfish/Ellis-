package sharesync

import (
	"context"
	"testing"
	"time"

	"newnavnav/internal/domain"
)

func TestSSESyncMissingRestartAfterMidnightCatchesUpThroughCurrentDate(t *testing.T) {
	client := &fakeSSEFundSizeClient{}
	repository := &fakeShareHistoryRepository{latest: "2026-06-10", hasLatest: true}
	service := NewSSEService(SSEOptions{
		Client:        client,
		Repository:    repository,
		Symbols:       []string{"SH513050"},
		QueryInterval: time.Nanosecond,
	})
	location, err := time.LoadLocation("Asia/Shanghai")
	if err != nil {
		t.Fatal(err)
	}

	err = service.SyncMissing(context.Background(), time.Date(2026, 6, 12, 0, 9, 0, 0, location))
	if err != nil {
		t.Fatal(err)
	}
	if len(client.calls) == 0 {
		t.Fatal("expected restart sync to call sse client")
	}
	dates := map[string]bool{}
	for _, call := range client.calls {
		dates[call.date] = true
	}
	if !dates["2026-06-11"] || !dates["2026-06-12"] {
		t.Fatalf("restart dates = %v, want 2026-06-11 and 2026-06-12", dates)
	}
}

type fakeSSEFundSizeClient struct {
	rows  map[string][]domain.ShareHistoryRecord
	calls []sseFundSizeCall
}

type sseFundSizeCall struct {
	date     string
	fundType string
}

func (c *fakeSSEFundSizeClient) FetchFundSizesByDate(ctx context.Context, date string, fundType string) ([]domain.ShareHistoryRecord, error) {
	c.calls = append(c.calls, sseFundSizeCall{date: date, fundType: fundType})
	return append([]domain.ShareHistoryRecord(nil), c.rows[date+"|"+fundType]...), nil
}
