package privatevaluation

import (
	"context"
	"testing"
	"time"
)

type fixedHistoryRepository struct {
	snapshots   []Snapshot
	replacedDay time.Time
}

func (r *fixedHistoryRepository) LoadPrivateValuationInputs(context.Context) ([]Input, error) {
	return nil, nil
}

func (r *fixedHistoryRepository) UpsertPrivateValuationInput(context.Context, Input) error {
	return nil
}

func (r *fixedHistoryRepository) UpsertPrivateValuationSnapshot(_ context.Context, snapshot Snapshot) error {
	r.snapshots = append(r.snapshots, snapshot)
	return nil
}

func (r *fixedHistoryRepository) ReplacePrivateValuationSnapshots(_ context.Context, _ string, day time.Time, snapshots []Snapshot) error {
	r.replacedDay = day
	r.snapshots = append([]Snapshot(nil), snapshots...)
	return nil
}

func TestImportHistoricalMinutesAcceptsLatestFixedIndexPCF(t *testing.T) {
	minute := time.Date(2026, 6, 2, 10, 0, 0, 0, shanghaiLocation)
	input := validNQInput(time.Date(2026, 7, 10, 16, 30, 0, 0, shanghaiLocation))
	input.PCF.HistoricalFixedProxy = true
	input.IB.MarketDataType = "HistoricalBidAsk"
	input.GeneratedAt = minute
	input.IB.ObservedAt = minute
	repository := &fixedHistoryRepository{}
	service := NewService(repository, nil)
	count, err := service.ImportHistoricalMinutes(context.Background(), SZ159659Symbol, []HistoricalMinuteInput{{
		Minute: minute, MarketPrice: 2.0, Input: input,
	}})
	if err != nil {
		t.Fatalf("fixed latest PCF history import failed: %v", err)
	}
	if count != 1 || len(repository.snapshots) != 1 {
		t.Fatalf("imported=%d snapshots=%d, want one", count, len(repository.snapshots))
	}
}

func TestImportHistoricalMinutesAcceptsLatestFixedN225MPCF(t *testing.T) {
	minute := time.Date(2026, 6, 2, 10, 0, 0, 0, shanghaiLocation)
	input := validN225MInput(time.Date(2026, 7, 10, 16, 0, 0, 0, shanghaiLocation))
	input.PCF.HistoricalFixedProxy = true
	input.IB.MarketDataType = "HistoricalBidAsk"
	input.GeneratedAt = minute
	input.IB.ObservedAt = minute
	repository := &fixedHistoryRepository{}
	service := NewService(repository, nil)
	count, err := service.ImportHistoricalMinutes(context.Background(), SH513520Symbol, []HistoricalMinuteInput{{
		Minute: minute, MarketPrice: 2.0, Input: input,
	}})
	if err != nil {
		t.Fatalf("fixed latest N225M PCF history import failed: %v", err)
	}
	if count != 1 || len(repository.snapshots) != 1 {
		t.Fatalf("imported=%d snapshots=%d, want one", count, len(repository.snapshots))
	}
}

func TestImportHistoricalMinutesAcceptsLatestFixedDAXPCF(t *testing.T) {
	minute := time.Date(2026, 6, 2, 10, 0, 0, 0, shanghaiLocation)
	input := validDAXInput(time.Date(2026, 7, 31, 16, 0, 0, 0, shanghaiLocation))
	input.PCF.HistoricalFixedProxy = true
	input.IB.MarketDataType = "HistoricalBidAsk"
	input.GeneratedAt = minute
	input.IB.ObservedAt = minute
	repository := &fixedHistoryRepository{}
	service := NewService(repository, nil)
	count, err := service.ImportHistoricalMinutes(context.Background(), SH513030Symbol, []HistoricalMinuteInput{{
		Minute: minute, MarketPrice: 1.8, Input: input,
	}})
	if err != nil {
		t.Fatalf("fixed latest DAX PCF history import failed: %v", err)
	}
	if count != 1 || len(repository.snapshots) != 1 {
		t.Fatalf("imported=%d snapshots=%d, want one", count, len(repository.snapshots))
	}
}

func TestReplaceHistoricalMinutesUsesOneDayRepositoryTransaction(t *testing.T) {
	minute := time.Date(2026, 7, 31, 14, 59, 0, 0, shanghaiLocation)
	input := validDAXInput(time.Date(2026, 7, 31, 16, 0, 0, 0, shanghaiLocation))
	input.PCF.HistoricalFixedProxy = true
	input.IB.MarketDataType = "HistoricalBidAsk"
	input.GeneratedAt = minute
	input.IB.ObservedAt = minute
	repository := &fixedHistoryRepository{}
	service := NewService(repository, nil)
	count, err := service.ReplaceHistoricalMinutes(context.Background(), SH513030Symbol, []HistoricalMinuteInput{{
		Minute: minute, MarketPrice: 1.34, Input: input,
	}})
	if err != nil {
		t.Fatalf("replace history failed: %v", err)
	}
	if count != 1 || len(repository.snapshots) != 1 || repository.replacedDay.Format("2006-01-02") != "2026-07-31" {
		t.Fatalf("count=%d snapshots=%d day=%v", count, len(repository.snapshots), repository.replacedDay)
	}
}

func TestReplaceHistoricalMinutesRejectsMixedDaysBeforeRepositoryWrite(t *testing.T) {
	first := time.Date(2026, 7, 30, 14, 59, 0, 0, shanghaiLocation)
	second := time.Date(2026, 7, 31, 14, 59, 0, 0, shanghaiLocation)
	input := validDAXInput(time.Date(2026, 7, 31, 16, 0, 0, 0, shanghaiLocation))
	input.PCF.HistoricalFixedProxy = true
	input.IB.MarketDataType = "HistoricalBidAsk"
	input.GeneratedAt = first
	input.IB.ObservedAt = first
	secondInput := input
	secondInput.PCF.TradingDay = "2026-07-31"
	secondInput.GeneratedAt = second
	secondInput.IB.ObservedAt = second
	repository := &fixedHistoryRepository{}
	service := NewService(repository, nil)
	_, err := service.ReplaceHistoricalMinutes(context.Background(), SH513030Symbol, []HistoricalMinuteInput{
		{Minute: first, MarketPrice: 1.34, Input: input},
		{Minute: second, MarketPrice: 1.34, Input: secondInput},
	})
	if err == nil || !repository.replacedDay.IsZero() {
		t.Fatalf("mixed-day error=%v replacedDay=%v", err, repository.replacedDay)
	}
}

func TestImportHistoricalMinutesRejectsOlderFixedPCF(t *testing.T) {
	minute := time.Date(2026, 7, 10, 10, 0, 0, 0, shanghaiLocation)
	input := validNQInput(time.Date(2026, 7, 9, 16, 30, 0, 0, shanghaiLocation))
	input.PCF.TradingDay = "2026-07-09"
	input.PCF.HistoricalFixedProxy = true
	input.IB.MarketDataType = "HistoricalBidAsk"
	service := NewService(&fixedHistoryRepository{}, nil)
	if _, err := service.ImportHistoricalMinutes(context.Background(), SZ159659Symbol, []HistoricalMinuteInput{{
		Minute: minute, MarketPrice: 2.0, Input: input,
	}}); err == nil {
		t.Fatal("older fixed PCF must not calibrate a later chart minute")
	}
}
