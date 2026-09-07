package sharesync

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"newnavnav/internal/domain"
)

func TestTargetDateUsesCurrentDisclosureDateAfter2350Shanghai(t *testing.T) {
	service := NewService(Options{})
	location, err := time.LoadLocation("Asia/Shanghai")
	if err != nil {
		t.Fatal(err)
	}

	before := time.Date(2026, 6, 11, 23, 49, 59, 0, location)
	if got := service.TargetDate(before); got != "2026-06-10" {
		t.Fatalf("before cutoff target date = %s, want 2026-06-10", got)
	}

	atCutoff := time.Date(2026, 6, 11, 23, 50, 0, 0, location)
	if got := service.TargetDate(atCutoff); got != "2026-06-11" {
		t.Fatalf("at cutoff target date = %s, want 2026-06-11", got)
	}

	weekend := time.Date(2026, 6, 13, 23, 50, 0, 0, location)
	if got := service.TargetDate(weekend); got != "2026-06-12" {
		t.Fatalf("weekend target date = %s, want 2026-06-12", got)
	}
}

func TestSZSEReportRowTrimsCodeAndStoresExchangeDate(t *testing.T) {
	record, ok := reportRow{
		SizeDate:          "2026-06-11",
		FundCode:          "160723  ",
		SecurityShortName: "嘉实原油LOF",
		CurrentSize:       "67,557.59",
	}.toRecord("LOF")
	if !ok {
		t.Fatal("expected report row to parse")
	}
	if record.Symbol != "SZ160723" {
		t.Fatalf("symbol = %q, want SZ160723", record.Symbol)
	}
	if record.ShareDate != "2026-06-11" {
		t.Fatalf("share date = %q, want 2026-06-11", record.ShareDate)
	}
}

func TestSyncMissingBackfillsInitialRangePerSymbol(t *testing.T) {
	client := &fakeFundSizeClient{
		rows: map[string][]domain.ShareHistoryRecord{
			"SZ159605|ETF": {
				{Symbol: "SZ159605", FundType: "ETF", ShareDate: "2026-06-12", Shares10K: 100, Source: sourceName},
			},
			"SZ160140|LOF": {
				{Symbol: "SZ160140", FundType: "LOF", ShareDate: "2026-06-12", Shares10K: 50, Source: sourceName},
			},
		},
	}
	repository := &fakeShareHistoryRepository{}
	service := NewService(Options{
		Client:              client,
		Repository:          repository,
		Symbols:             []string{"SH513050", "SZ160140", "SZ159605"},
		QueryInterval:       time.Nanosecond,
		InitialLookbackDays: 3,
	})
	location, err := time.LoadLocation("Asia/Shanghai")
	if err != nil {
		t.Fatal(err)
	}

	err = service.SyncMissing(context.Background(), time.Date(2026, 6, 11, 23, 50, 0, 0, location))
	if err != nil {
		t.Fatal(err)
	}

	if len(repository.rows) != 2 {
		t.Fatalf("written rows = %d, want 2", len(repository.rows))
	}
	if client.calls[0].symbol != "SZ159605" || client.calls[0].startDate != "2026-06-09" || client.calls[0].endDate != "2026-06-11" {
		t.Fatalf("first call = %+v, want SZ159605 2026-06-09..2026-06-11", client.calls[0])
	}
	if client.calls[len(client.calls)-1].symbol != "SZ160140" {
		t.Fatalf("last call symbol = %s, want SZ160140", client.calls[len(client.calls)-1].symbol)
	}
}

func TestSyncMissingRestartAfterMidnightFetchesPreviousSZSEDisclosureDate(t *testing.T) {
	client := &fakeFundSizeClient{}
	repository := &fakeShareHistoryRepository{latest: "2026-06-11", hasLatest: true}
	service := NewService(Options{
		Client:        client,
		Repository:    repository,
		Symbols:       []string{"SZ160723"},
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
		t.Fatal("expected restart sync to call szse client")
	}
	for _, call := range client.calls {
		if call.startDate != "2026-06-11" || call.endDate != "2026-06-11" {
			t.Fatalf("restart call = %+v, want disclosure date 2026-06-11", call)
		}
	}
}

func TestSyncMissingSkipsWeekendDailyRun(t *testing.T) {
	client := &fakeFundSizeClient{}
	repository := &fakeShareHistoryRepository{latest: "2026-06-12", hasLatest: true}
	service := NewService(Options{
		Client:        client,
		Repository:    repository,
		Symbols:       []string{"SZ159605"},
		QueryInterval: time.Nanosecond,
	})
	location, err := time.LoadLocation("Asia/Shanghai")
	if err != nil {
		t.Fatal(err)
	}

	err = service.SyncMissing(context.Background(), time.Date(2026, 6, 13, 23, 50, 0, 0, location))
	if err != nil {
		t.Fatal(err)
	}
	if len(client.calls) != 0 {
		t.Fatalf("weekend calls = %d, want 0", len(client.calls))
	}
}

func TestSyncMissingPersistsSuccessfulRowsWhenSomeSymbolsFail(t *testing.T) {
	client := &fakeFundSizeClient{
		rows: map[string][]domain.ShareHistoryRecord{
			"SZ159501|ETF": {
				{Symbol: "SZ159501", FundType: "ETF", ShareDate: "2026-06-23", Shares10K: 100, Source: sourceName},
			},
			"SZ159503|ETF": {
				{Symbol: "SZ159503", FundType: "ETF", ShareDate: "2026-06-23", Shares10K: 200, Source: sourceName},
			},
		},
		errs: map[string]error{
			"SZ159502|ETF": errors.New("timeout"),
			"SZ159502|LOF": errors.New("timeout"),
		},
	}
	repository := &fakeShareHistoryRepository{latest: "2026-06-22", hasLatest: true}
	service := NewService(Options{
		Client:        client,
		Repository:    repository,
		Symbols:       []string{"SZ159501", "SZ159502", "SZ159503"},
		QueryInterval: time.Nanosecond,
	})
	location, err := time.LoadLocation("Asia/Shanghai")
	if err != nil {
		t.Fatal(err)
	}

	err = service.SyncMissing(context.Background(), time.Date(2026, 6, 23, 23, 50, 0, 0, location))
	if err == nil {
		t.Fatal("expected partial fetch error")
	}
	if !strings.Contains(err.Error(), "SZ159502") {
		t.Fatalf("error = %v, want symbol name", err)
	}
	if len(repository.rows) != 2 {
		t.Fatalf("written rows = %d, want 2", len(repository.rows))
	}
	if repository.rows[0].Symbol != "SZ159501" || repository.rows[1].Symbol != "SZ159503" {
		t.Fatalf("written rows = %+v, want symbols 159501 and 159503", repository.rows)
	}
}

type fakeFundSizeClient struct {
	rows  map[string][]domain.ShareHistoryRecord
	errs  map[string]error
	calls []fundSizeCall
}

type fundSizeCall struct {
	startDate string
	endDate   string
	fundType  string
	symbol    string
}

func (c *fakeFundSizeClient) FetchFundSizes(ctx context.Context, startDate string, endDate string, fundType string, symbol string) ([]domain.ShareHistoryRecord, error) {
	c.calls = append(c.calls, fundSizeCall{startDate: startDate, endDate: endDate, fundType: fundType, symbol: symbol})
	if err := c.errs[symbol+"|"+fundType]; err != nil {
		return nil, err
	}
	return append([]domain.ShareHistoryRecord(nil), c.rows[symbol+"|"+fundType]...), nil
}

type fakeShareHistoryRepository struct {
	latest    string
	hasLatest bool
	rows      []domain.ShareHistoryRecord
}

func (r *fakeShareHistoryRepository) UpsertShareHistory(ctx context.Context, rows []domain.ShareHistoryRecord) error {
	r.rows = append(r.rows, rows...)
	return nil
}

func (r *fakeShareHistoryRepository) LatestShareHistoryDateBySymbolPrefix(ctx context.Context, prefix string) (string, bool, error) {
	return r.latest, r.hasLatest, nil
}
