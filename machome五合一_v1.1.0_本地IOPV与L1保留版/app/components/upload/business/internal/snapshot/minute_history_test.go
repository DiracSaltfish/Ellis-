package snapshot

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestMinuteHistoryStoreUpsertReadAndCleanup(t *testing.T) {
	root := t.TempDir()
	store := NewMinuteHistoryStore(root, 2)
	now := time.Date(2026, 6, 2, 10, 31, 12, 0, shanghaiLocation())
	oldDir := filepath.Join(root, "20260531")
	if err := os.MkdirAll(oldDir, 0o755); err != nil {
		t.Fatal(err)
	}

	realtime := 1.24
	if err := store.Upsert(now, []MinuteHistoryPoint{
		{
			Symbol:       "SZ164824",
			Minute:       "2026-06-02 10:31",
			Timestamp:    now.Format(time.RFC3339Nano),
			MarketPrice:  1.23,
			EstimatedNAV: 1.25,
			PremiumPct:   -1.6,
			RealtimeEST:  &realtime,
		},
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(oldDir); !os.IsNotExist(err) {
		t.Fatalf("old day dir still exists: %v", err)
	}

	if err := store.Upsert(now, []MinuteHistoryPoint{
		{
			Symbol:       "SZ164824",
			Minute:       "2026-06-02 10:31",
			Timestamp:    now.Add(10 * time.Second).Format(time.RFC3339Nano),
			MarketPrice:  1.24,
			EstimatedNAV: 1.25,
			PremiumPct:   -0.8,
		},
	}); err != nil {
		t.Fatal(err)
	}
	rows, err := store.Read("SZ164824", 2, now)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 1 {
		t.Fatalf("len(rows) = %d, want 1", len(rows))
	}
	if rows[0].MarketPrice != 1.24 || rows[0].PremiumPct != -0.8 {
		t.Fatalf("row was not updated: %+v", rows[0])
	}
	if _, err := os.Stat(filepath.Join(root, "20260602", "SZ164824.csv")); err != nil {
		t.Fatalf("expected symbol minute history file: %v", err)
	}
}

func TestMinuteHistoryChartRowsKeepsFourEstimatedNAVDecimals(t *testing.T) {
	rows := minuteHistoryChartRows([]MinuteHistoryPoint{{
		Minute:       "2026-06-02 10:31",
		MarketPrice:  1.23456,
		EstimatedNAV: 1.23456,
		PremiumPct:   -1.234,
	}}, "20260602")

	if len(rows) != 1 {
		t.Fatalf("len(rows) = %d, want 1", len(rows))
	}
	if rows[0].Minute != "10:31" {
		t.Fatalf("minute = %q, want 10:31", rows[0].Minute)
	}
	if rows[0].MarketPrice != 1.235 {
		t.Fatalf("market_price = %.6f, want 1.235", rows[0].MarketPrice)
	}
	if rows[0].EstimatedNAV != 1.2346 {
		t.Fatalf("estimated_nav = %.6f, want 1.2346", rows[0].EstimatedNAV)
	}
	if rows[0].PremiumPct != -1.23 {
		t.Fatalf("premium_pct = %.6f, want -1.23", rows[0].PremiumPct)
	}
}

func TestMinuteHistoryChartRowsCarriesOfficialEst(t *testing.T) {
	rows := minuteHistoryChartRows([]MinuteHistoryPoint{{
		Minute:       "2026-06-02 10:31",
		MarketPrice:  1.23456,
		EstimatedNAV: 1.23456,
		PremiumPct:   -1.234,
		OfficialEST:  0.8804,
	}}, "20260602")

	if len(rows) != 1 || rows[0].OfficialEst != 0.8804 {
		t.Fatalf("official_est = %+v, want 0.8804", rows[0])
	}
	zero := minuteHistoryChartRows([]MinuteHistoryPoint{{
		Minute: "2026-06-02 10:31", MarketPrice: 1, EstimatedNAV: 1,
	}}, "")
	if len(zero) != 1 || zero[0].OfficialEst != 0 {
		t.Fatalf("absent official_est must stay zero: %+v", zero[0])
	}
}

func TestMinuteHistoryStoreReadsLegacyDailyFileAndListsDates(t *testing.T) {
	root := t.TempDir()
	store := NewMinuteHistoryStore(root, 45)
	now := time.Date(2026, 6, 4, 10, 0, 0, 0, shanghaiLocation())
	legacyPath := filepath.Join(root, "20260603", minuteHistoryFile)
	if err := writeMinuteHistoryFile(legacyPath, []MinuteHistoryPoint{
		{
			Symbol:       "SZ164824",
			Minute:       "2026-06-03 10:00",
			Timestamp:    now.AddDate(0, 0, -1).Format(time.RFC3339Nano),
			MarketPrice:  1.23,
			EstimatedNAV: 1.25,
			PremiumPct:   -1.6,
		},
		{
			Symbol:       "SH513100",
			Minute:       "2026-06-03 10:00",
			Timestamp:    now.AddDate(0, 0, -1).Format(time.RFC3339Nano),
			MarketPrice:  1.52,
			EstimatedNAV: 1.54,
			PremiumPct:   -1.3,
		},
	}); err != nil {
		t.Fatal(err)
	}

	rows, err := store.ReadDate("SZ164824", "2026-06-03")
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 1 || rows[0].Symbol != "SZ164824" {
		t.Fatalf("legacy rows = %+v, want SZ164824 only", rows)
	}
	dates, err := store.AvailableDates("SZ164824", 10, now)
	if err != nil {
		t.Fatal(err)
	}
	if len(dates) != 1 || dates[0] != "20260603" {
		t.Fatalf("dates = %+v, want 20260603", dates)
	}
}

func TestMinuteHistoryStoreSkipsOutsideTradingSession(t *testing.T) {
	root := t.TempDir()
	store := NewMinuteHistoryStore(root)
	now := time.Date(2026, 6, 2, 18, 31, 12, 0, shanghaiLocation())

	if err := store.Upsert(now, []MinuteHistoryPoint{
		{
			Symbol:       "SZ164824",
			Minute:       "2026-06-02 18:31",
			Timestamp:    now.Format(time.RFC3339Nano),
			MarketPrice:  1.23,
			EstimatedNAV: 1.25,
			PremiumPct:   -1.6,
		},
		{
			Symbol:       "SZ164824",
			Minute:       "2026-06-02 09:29",
			Timestamp:    now.Format(time.RFC3339Nano),
			MarketPrice:  1.23,
			EstimatedNAV: 1.25,
			PremiumPct:   -1.6,
		},
	}); err != nil {
		t.Fatal(err)
	}
	rows, err := store.Read("SZ164824", 2, now)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 0 {
		t.Fatalf("len(rows) = %d, want 0: %+v", len(rows), rows)
	}
}

func TestMinuteHistoryStoreReadFiltersOutOfSessionRows(t *testing.T) {
	root := t.TempDir()
	store := NewMinuteHistoryStore(root)
	now := time.Date(2026, 6, 2, 10, 0, 0, 0, shanghaiLocation())
	path := filepath.Join(root, "20260602", minuteHistoryFile)
	if err := writeMinuteHistoryFile(path, []MinuteHistoryPoint{
		{
			Symbol:       "SZ164824",
			Minute:       "2026-06-02 10:00",
			Timestamp:    now.Format(time.RFC3339Nano),
			MarketPrice:  1.23,
			EstimatedNAV: 1.25,
			PremiumPct:   -1.6,
		},
		{
			Symbol:       "SZ164824",
			Minute:       "2026-06-02 18:31",
			Timestamp:    now.Format(time.RFC3339Nano),
			MarketPrice:  1.24,
			EstimatedNAV: 1.25,
			PremiumPct:   -0.8,
		},
	}); err != nil {
		t.Fatal(err)
	}
	rows, err := store.Read("SZ164824", 2, now)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 1 {
		t.Fatalf("len(rows) = %d, want 1: %+v", len(rows), rows)
	}
	if rows[0].Minute != "2026-06-02 10:00" {
		t.Fatalf("unexpected row: %+v", rows[0])
	}
}

func TestMinuteHistoryStoreReadUsesNineAMRollover(t *testing.T) {
	root := t.TempDir()
	store := NewMinuteHistoryStore(root)
	todayPath := filepath.Join(root, "20260603", minuteHistoryFile)
	prevPath := filepath.Join(root, "20260602", minuteHistoryFile)
	prevNow := time.Date(2026, 6, 2, 10, 0, 0, 0, shanghaiLocation())
	todayNow := time.Date(2026, 6, 3, 10, 0, 0, 0, shanghaiLocation())
	if err := writeMinuteHistoryFile(prevPath, []MinuteHistoryPoint{{
		Symbol:       "SZ159567",
		Minute:       "2026-06-02 10:15",
		Timestamp:    prevNow.Format(time.RFC3339Nano),
		MarketPrice:  0.637,
		EstimatedNAV: 0.643,
		PremiumPct:   -0.93,
	}}); err != nil {
		t.Fatal(err)
	}
	if err := writeMinuteHistoryFile(todayPath, []MinuteHistoryPoint{{
		Symbol:       "SZ159567",
		Minute:       "2026-06-03 09:31",
		Timestamp:    todayNow.Format(time.RFC3339Nano),
		MarketPrice:  0.638,
		EstimatedNAV: 0.644,
		PremiumPct:   -0.92,
	}}); err != nil {
		t.Fatal(err)
	}

	beforeRollover := time.Date(2026, 6, 3, 8, 59, 0, 0, shanghaiLocation())
	rows, err := store.Read("SZ159567", 1, beforeRollover)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 1 || rows[0].Minute != "2026-06-02 10:15" {
		t.Fatalf("before rollover rows = %+v, want previous day", rows)
	}

	afterRollover := time.Date(2026, 6, 3, 9, 0, 0, 0, shanghaiLocation())
	rows, err = store.Read("SZ159567", 1, afterRollover)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 1 || rows[0].Minute != "2026-06-03 09:31" {
		t.Fatalf("after rollover rows = %+v, want current day", rows)
	}
}

func TestMinuteHistoryStoreCleanupUsesNineAMRollover(t *testing.T) {
	root := t.TempDir()
	store := NewMinuteHistoryStore(root, 2)
	for _, day := range []string{"20260601", "20260602", "20260603"} {
		if err := os.MkdirAll(filepath.Join(root, day), 0o755); err != nil {
			t.Fatal(err)
		}
	}

	beforeRollover := time.Date(2026, 6, 3, 8, 59, 0, 0, shanghaiLocation())
	if err := store.cleanupLocked(beforeRollover); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(root, "20260601")); err != nil {
		t.Fatalf("expected 20260601 to remain before rollover: %v", err)
	}

	afterRollover := time.Date(2026, 6, 3, 9, 0, 0, 0, shanghaiLocation())
	if err := store.cleanupLocked(afterRollover); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(root, "20260601")); !os.IsNotExist(err) {
		t.Fatalf("expected 20260601 removed after rollover, got err=%v", err)
	}
}

func TestMinuteHistoryStoreDeleteDayRemovesSymbolAndLegacyFiles(t *testing.T) {
	root := t.TempDir()
	store := NewMinuteHistoryStore(root, 45)
	dayPath := filepath.Join(root, "20260619")
	if err := os.MkdirAll(dayPath, 0o755); err != nil {
		t.Fatal(err)
	}

	if err := writeMinuteHistoryFile(filepath.Join(dayPath, "SZ164824.csv"), []MinuteHistoryPoint{{
		Symbol:       "SZ164824",
		Minute:       "2026-06-19 10:00",
		Timestamp:    "2026-06-19T10:00:00+08:00",
		MarketPrice:  1.23,
		EstimatedNAV: 1.25,
		PremiumPct:   -1.6,
	}}); err != nil {
		t.Fatal(err)
	}
	if err := writeMinuteHistoryFile(filepath.Join(dayPath, minuteHistoryFile), []MinuteHistoryPoint{{
		Symbol:       "SH513100",
		Minute:       "2026-06-19 10:01",
		Timestamp:    "2026-06-19T10:01:00+08:00",
		MarketPrice:  1.52,
		EstimatedNAV: 1.54,
		PremiumPct:   -1.3,
	}}); err != nil {
		t.Fatal(err)
	}

	result, err := store.DeleteDay("2026-06-19")
	if err != nil {
		t.Fatal(err)
	}
	if !result.OK || !result.Deleted {
		t.Fatalf("result = %+v, want ok deleted", result)
	}
	if result.Day != "20260619" {
		t.Fatalf("result.Day = %q, want 20260619", result.Day)
	}
	if result.SymbolCount != 2 || result.RowCount != 2 {
		t.Fatalf("result = %+v, want 2 symbols and 2 rows", result)
	}
	if _, err := os.Stat(dayPath); !os.IsNotExist(err) {
		t.Fatalf("expected day dir removed, got err=%v", err)
	}
}
