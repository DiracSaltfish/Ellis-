package ratiofit

import (
	"testing"
	"time"

	"newnavnav/internal/domain"
	"newnavnav/internal/snapshot"
)

func TestMinuteHistoryClosePremiumLookupUsesLastMinutePoint(t *testing.T) {
	store := snapshot.NewMinuteHistoryStore(t.TempDir(), 120)
	now := time.Date(2026, 6, 19, 15, 0, 0, 0, shanghaiLocation())
	err := store.Upsert(now, []snapshot.MinuteHistoryPoint{
		{Symbol: "SH501312", Minute: "2026-06-19 14:58", MarketPrice: 1.001, EstimatedNAV: 1.0, PremiumPct: 0.1},
		{Symbol: "SH501312", Minute: "2026-06-19 15:00", MarketPrice: 1.005, EstimatedNAV: 1.0, PremiumPct: 0.5},
	})
	if err != nil {
		t.Fatal(err)
	}

	lookup := MinuteHistoryClosePremiumLookup{Store: store}
	point, err := lookup.LookupClosePremium("SH501312", "2026-06-19")
	if err != nil {
		t.Fatal(err)
	}
	if point == nil {
		t.Fatal("expected close premium point")
	}
	if point.Minute != "15:00" {
		t.Fatalf("point.Minute = %q, want 15:00", point.Minute)
	}
	if point.PremiumPct != 0.5 {
		t.Fatalf("point.PremiumPct = %.6f, want 0.500000", point.PremiumPct)
	}
}

func TestApplyClosePremiumPopulatesHistoryRows(t *testing.T) {
	store := snapshot.NewMinuteHistoryStore(t.TempDir(), 120)
	now := time.Date(2026, 6, 19, 15, 0, 0, 0, shanghaiLocation())
	err := store.Upsert(now, []snapshot.MinuteHistoryPoint{
		{Symbol: "SH501312", Minute: "2026-06-19 14:59", MarketPrice: 1.004, EstimatedNAV: 1.0, PremiumPct: 0.4},
	})
	if err != nil {
		t.Fatal(err)
	}

	rows := []domain.EffectiveRatioFitHistoryRow{
		{Symbol: "SH501312", TargetDate: "2026-06-19"},
		{Symbol: "SZ159659", TargetDate: "2026-06-19"},
	}
	if err := ApplyClosePremium(rows, MinuteHistoryClosePremiumLookup{Store: store}); err != nil {
		t.Fatal(err)
	}

	if rows[0].ClosingRealtimeMinute != "14:59" {
		t.Fatalf("rows[0].ClosingRealtimeMinute = %q, want 14:59", rows[0].ClosingRealtimeMinute)
	}
	if rows[0].ClosingRealtimePremiumPct == nil || *rows[0].ClosingRealtimePremiumPct != 0.4 {
		t.Fatalf("rows[0].ClosingRealtimePremiumPct = %v, want 0.4", rows[0].ClosingRealtimePremiumPct)
	}
	if rows[1].ClosingRealtimeMinute != "" {
		t.Fatalf("rows[1].ClosingRealtimeMinute = %q, want empty", rows[1].ClosingRealtimeMinute)
	}
	if rows[1].ClosingRealtimePremiumPct != nil {
		t.Fatalf("rows[1].ClosingRealtimePremiumPct = %v, want nil", rows[1].ClosingRealtimePremiumPct)
	}
}
