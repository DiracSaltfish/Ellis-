package privatevaluation

import (
	"context"
	"math"
	"testing"
	"time"
)

type silverCloseHistoryRepositoryStub struct {
	rows   []SilverCloseHistoryRow
	symbol string
	days   int
}

func (s *silverCloseHistoryRepositoryStub) LoadPrivateValuationInputs(context.Context) ([]Input, error) {
	return nil, nil
}

func (s *silverCloseHistoryRepositoryStub) UpsertPrivateValuationInput(context.Context, Input) error {
	return nil
}

func (s *silverCloseHistoryRepositoryStub) UpsertPrivateValuationSnapshot(context.Context, Snapshot) error {
	return nil
}

func (s *silverCloseHistoryRepositoryStub) LoadPrivateSilverCloseHistory(_ context.Context, symbol string, days int) ([]SilverCloseHistoryRow, error) {
	s.symbol = symbol
	s.days = days
	return append([]SilverCloseHistoryRow(nil), s.rows...), nil
}

func TestSilverCloseHistoryJoinsOfficialNAVAndCalculatesDeviation(t *testing.T) {
	shanghai := time.FixedZone("Asia/Shanghai", 8*60*60)
	officialNAV := 1.8
	repository := &silverCloseHistoryRepositoryStub{rows: []SilverCloseHistoryRow{
		{
			TradingDay:            "2026-08-19",
			CloseMinute:           time.Date(2026, 8, 19, 15, 0, 0, 0, shanghai),
			OfficialNAV:           &officialNAV,
			SettlementNAV:         1.82,
			SettlementPremiumRate: 0.04,
			TradingNAV:            1.84,
			TradingPremiumRate:    0.03,
		},
		{
			TradingDay:            "2026-08-20",
			CloseMinute:           time.Date(2026, 8, 20, 15, 0, 0, 0, shanghai),
			SettlementNAV:         1.83,
			SettlementPremiumRate: 0.05,
			TradingNAV:            1.85,
			TradingPremiumRate:    0.04,
		},
	}}
	service := NewService(repository, nil)

	history, err := service.SilverCloseHistory(context.Background(), "sz161226", 180)
	if err != nil {
		t.Fatal(err)
	}
	if repository.symbol != SZ161226Symbol || repository.days != 180 {
		t.Fatalf("repository request = %s/%d", repository.symbol, repository.days)
	}
	if len(history.Rows) != 2 || history.Rows[0].TradingDay != "2026-08-20" {
		t.Fatalf("rows = %+v", history.Rows)
	}
	if history.Rows[0].SettlementDeviationRate != nil {
		t.Fatalf("unpublished NAV deviation = %v, want nil", history.Rows[0].SettlementDeviationRate)
	}
	want := 1.82/1.8 - 1
	got := history.Rows[1].SettlementDeviationRate
	if got == nil || math.Abs(*got-want) > 1e-9 {
		t.Fatalf("settlement deviation = %v, want %.10f", got, want)
	}
}
