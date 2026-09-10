package navsync

import (
	"context"
	"testing"
	"time"

	"newnavnav/internal/domain"
	"newnavnav/internal/ingest/eastmoney"
)

type fakeRepo struct {
	latest       map[string]string
	written      []domain.NetValue
	latestChecks int
}

func (r *fakeRepo) LatestNetValueDate(ctx context.Context, symbol string) (string, bool, error) {
	r.latestChecks++
	value, ok := r.latest[symbol]
	return value, ok, nil
}

func (r *fakeRepo) UpsertNetValues(ctx context.Context, values []domain.NetValue) error {
	r.written = append(r.written, values...)
	return nil
}

type fakeEastmoneyClient struct {
	calls []fetchCall
}

type fetchCall struct {
	code  string
	start string
	end   string
}

func (c *fakeEastmoneyClient) FetchNetValues(ctx context.Context, fundCode string, startDate string, endDate string) ([]eastmoney.NetValueRecord, error) {
	c.calls = append(c.calls, fetchCall{code: fundCode, start: startDate, end: endDate})
	return []eastmoney.NetValueRecord{
		{FundCode: fundCode, Date: endDate, UnitNAV: 1.2345, Source: "eastmoney"},
	}, nil
}

func TestSyncMissingSkipsCurrentLocalNAV(t *testing.T) {
	repo := &fakeRepo{latest: map[string]string{"SZ161226": "2026-06-02"}}
	client := &fakeEastmoneyClient{}
	service := NewEastmoneyService(EastmoneyOptions{
		Client:     client,
		Repository: repo,
		Symbols:    []string{"SZ161226"},
		Delay:      0,
	})

	err := service.SyncMissing(context.Background(), time.Date(2026, 6, 2, 20, 0, 0, 0, time.Local))
	if err != nil {
		t.Fatal(err)
	}
	if len(client.calls) != 0 {
		t.Fatalf("calls = %+v, want no eastmoney request", client.calls)
	}
	if len(repo.written) != 0 {
		t.Fatalf("written = %+v, want none", repo.written)
	}
}

func TestSyncMissingFetchesOnlyMissingWindow(t *testing.T) {
	repo := &fakeRepo{latest: map[string]string{"SZ161226": "2026-06-01"}}
	client := &fakeEastmoneyClient{}
	service := NewEastmoneyService(EastmoneyOptions{
		Client:     client,
		Repository: repo,
		Symbols:    []string{"SZ161226", "QQQ"},
		Delay:      0,
	})

	err := service.SyncMissing(context.Background(), time.Date(2026, 6, 2, 20, 0, 0, 0, time.Local))
	if err != nil {
		t.Fatal(err)
	}
	if len(client.calls) != 1 {
		t.Fatalf("len(calls) = %d, want 1", len(client.calls))
	}
	call := client.calls[0]
	if call.code != "161226" || call.start != "2026-06-02" || call.end != "2026-06-02" {
		t.Fatalf("call = %+v", call)
	}
	if len(repo.written) != 1 || repo.written[0].Symbol != "SZ161226" || repo.written[0].Date != "2026-06-02" {
		t.Fatalf("written = %+v", repo.written)
	}
}

func TestSyncMissingTracksDebugStatus(t *testing.T) {
	repo := &fakeRepo{latest: map[string]string{
		"SZ161226": "2026-06-02",
		"SH501300": "2026-06-01",
	}}
	client := &fakeEastmoneyClient{}
	service := NewEastmoneyService(EastmoneyOptions{
		Client:     client,
		Repository: repo,
		Symbols:    []string{"SZ161226", "SH501300"},
		Delay:      0,
	})

	err := service.SyncMissing(context.Background(), time.Date(2026, 6, 2, 20, 0, 0, 0, time.Local))
	if err != nil {
		t.Fatal(err)
	}

	status := service.Status()
	if status.Running {
		t.Fatal("status.Running = true, want false after sync")
	}
	if !status.LastSuccess || status.LastError != "" {
		t.Fatalf("last success/error = %v/%q, want success", status.LastSuccess, status.LastError)
	}
	if status.TotalSymbols != 2 || status.Requests != 1 || status.Written != 1 || status.Skipped != 1 || status.Failed != 0 {
		t.Fatalf("status counts = %+v", status)
	}
	if status.LastWrittenSymbol != "SH501300" || status.LastWrittenDate != "2026-06-02" {
		t.Fatalf("last written = %s/%s", status.LastWrittenSymbol, status.LastWrittenDate)
	}
	if len(status.RecentSymbolResults) != 2 {
		t.Fatalf("len(recent results) = %d, want 2", len(status.RecentSymbolResults))
	}
	if status.RecentSymbolResults[0].Status != "synced" || status.RecentSymbolResults[1].Status != "current" {
		t.Fatalf("recent statuses = %+v", status.RecentSymbolResults)
	}
}
