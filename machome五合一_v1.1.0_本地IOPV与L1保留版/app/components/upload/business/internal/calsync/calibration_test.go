package calsync

import (
	"context"
	"testing"
	"time"

	"newnavnav/internal/domain"
)

type fakeCalibrationRepo struct {
	data         domain.ValuationData
	calibrations []domain.Calibration
}

func (r *fakeCalibrationRepo) LoadValuationData(ctx context.Context) (domain.ValuationData, error) {
	return r.data, nil
}

func (r *fakeCalibrationRepo) UpsertCalibrations(ctx context.Context, calibrations []domain.Calibration) error {
	r.calibrations = append(r.calibrations, calibrations...)
	return nil
}

func TestSyncMissingWritesCalibrationFromPairClose(t *testing.T) {
	data := domain.EmptyValuationData()
	data.FundPairs["SH513030"] = []domain.FundPair{
		{FundSymbol: "SH513030", PairSymbol: "znb_DAX", PairType: "qdii_eu_static"},
	}
	data.LatestNetValues["SH513030"] = domain.NetValue{
		Symbol: "SH513030",
		Date:   "2026-06-01",
		NAV:    2,
	}
	data.DailyPricesByDate["znb_DAX"] = map[string]domain.DailyPrice{
		"2026-06-01": {Symbol: "znb_DAX", Date: "2026-06-01", Close: 500, AdjClose: 510},
	}
	repo := &fakeCalibrationRepo{data: data}

	err := NewService(Options{Repository: repo}).SyncMissing(context.Background(), time.Date(2026, 6, 2, 22, 45, 0, 0, time.Local))
	if err != nil {
		t.Fatalf("SyncMissing returned error: %v", err)
	}
	if len(repo.calibrations) != 1 {
		t.Fatalf("expected 1 calibration, got %d", len(repo.calibrations))
	}
	cal := repo.calibrations[0]
	if cal.Symbol != "SH513030" || cal.Date != "2026-06-01" {
		t.Fatalf("unexpected calibration identity: %+v", cal)
	}
	if cal.Factor != 255 {
		t.Fatalf("expected adj close based factor 255, got %f", cal.Factor)
	}
	if cal.BaseValue != 2 {
		t.Fatalf("expected base value 2, got %f", cal.BaseValue)
	}
	if cal.Source != "auto_daily:znb_DAX" {
		t.Fatalf("unexpected source: %s", cal.Source)
	}
}

func TestSyncMissingSkipsCurrentCalibration(t *testing.T) {
	data := domain.EmptyValuationData()
	data.FundPairs["SH513030"] = []domain.FundPair{
		{FundSymbol: "SH513030", PairSymbol: "znb_DAX", PairType: "qdii_eu_static"},
	}
	data.LatestNetValues["SH513030"] = domain.NetValue{
		Symbol: "SH513030",
		Date:   "2026-06-01",
		NAV:    2,
	}
	data.LatestCalibrations["SH513030"] = domain.Calibration{
		Symbol: "SH513030",
		Date:   "2026-06-01",
		Factor: 240,
		Source: "auto_daily:znb_DAX",
	}
	data.DailyPricesByDate["znb_DAX"] = map[string]domain.DailyPrice{
		"2026-06-01": {Symbol: "znb_DAX", Date: "2026-06-01", Close: 500},
	}
	repo := &fakeCalibrationRepo{data: data}

	err := NewService(Options{Repository: repo}).SyncMissing(context.Background(), time.Date(2026, 6, 2, 22, 45, 0, 0, time.Local))
	if err != nil {
		t.Fatalf("SyncMissing returned error: %v", err)
	}
	if len(repo.calibrations) != 0 {
		t.Fatalf("expected no calibration writes, got %d", len(repo.calibrations))
	}
}
