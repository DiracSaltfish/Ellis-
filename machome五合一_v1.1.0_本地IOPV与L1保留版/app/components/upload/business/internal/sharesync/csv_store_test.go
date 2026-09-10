package sharesync

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"newnavnav/internal/domain"
)

type fakeClosePremiumLookup struct {
	values map[string]float64
}

func (l fakeClosePremiumLookup) LookupClosePremium(symbol string, shareDate string) (*float64, error) {
	value, ok := l.values[strings.ToUpper(strings.TrimSpace(symbol))+"|"+strings.TrimSpace(shareDate)]
	if !ok {
		return nil, nil
	}
	copy := value
	return &copy, nil
}

func TestCSVStoreUpsertAndLoadShareHistory(t *testing.T) {
	ctx := context.Background()
	store := NewCSVStore(t.TempDir())
	store.SetClosePremiumLookup(fakeClosePremiumLookup{
		values: map[string]float64{
			"SZ164701|2026-06-10": 1.25,
			"SZ164701|2026-06-11": -0.75,
		},
	})

	err := store.UpsertShareHistory(ctx, []domain.ShareHistoryRecord{
		{Symbol: "SZ164701", ShareDate: "2026-06-10", FundType: "LOF", Name: "黄金LOF", Shares10K: 100, Source: sourceName},
		{Symbol: "SZ164701", ShareDate: "2026-06-11", FundType: "LOF", Name: "黄金LOF", Shares10K: 115, Source: sourceName},
	})
	if err != nil {
		t.Fatal(err)
	}

	err = store.UpsertShareHistory(ctx, []domain.ShareHistoryRecord{
		{Symbol: "SZ164701", ShareDate: "2026-06-11", FundType: "LOF", Shares10K: 120, Source: sourceName},
	})
	if err != nil {
		t.Fatal(err)
	}

	if _, err := os.Stat(store.PathForSymbol("SZ164701")); err != nil {
		t.Fatalf("expected symbol csv file: %v", err)
	}
	if got := filepath.Base(store.PathForSymbol("SZ164701")); got != "164701SZ.csv" {
		t.Fatalf("csv file name = %s, want 164701SZ.csv", got)
	}
	content, err := os.ReadFile(store.PathForSymbol("SZ164701"))
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(strings.TrimSpace(string(content)), "\n")
	if len(lines) != 3 {
		t.Fatalf("csv lines = %d, want header plus 2 rows", len(lines))
	}
	if !strings.HasPrefix(lines[1], "2026-06-10,") || !strings.HasPrefix(lines[2], "2026-06-11,") {
		t.Fatalf("csv rows should stay ascending by date, got %q then %q", lines[1], lines[2])
	}

	rows, err := store.LoadShareHistory(ctx, "SZ164701", 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 2 {
		t.Fatalf("rows = %d, want 2", len(rows))
	}
	if rows[0].ShareDate != "2026-06-11" || rows[0].Shares10K != 120 {
		t.Fatalf("latest row = %+v, want 2026-06-11 shares 120", rows[0])
	}
	if rows[0].Name != "黄金LOF" {
		t.Fatalf("latest row name = %q, want preserved name", rows[0].Name)
	}
	if rows[0].PreviousShares10K == nil || *rows[0].PreviousShares10K != 100 {
		t.Fatalf("previous shares = %v, want 100", rows[0].PreviousShares10K)
	}
	if rows[0].ShareChange10K == nil || *rows[0].ShareChange10K != 20 {
		t.Fatalf("share change = %v, want 20", rows[0].ShareChange10K)
	}
	if rows[0].ShareChangePct == nil || *rows[0].ShareChangePct != 20 {
		t.Fatalf("share change pct = %v, want 20", rows[0].ShareChangePct)
	}
	if rows[0].ClosePremiumPct == nil || *rows[0].ClosePremiumPct != -0.75 {
		t.Fatalf("close premium pct = %v, want -0.75", rows[0].ClosePremiumPct)
	}
}

func TestCSVStoreLatestShareHistoryDateBySymbolPrefix(t *testing.T) {
	ctx := context.Background()
	store := NewCSVStore(t.TempDir())
	err := store.UpsertShareHistory(ctx, []domain.ShareHistoryRecord{
		{Symbol: "SZ164701", ShareDate: "2026-06-10", FundType: "LOF", Shares10K: 100, Source: sourceName},
		{Symbol: "SH513050", ShareDate: "2026-06-11", FundType: "ETF", Shares10K: 200, Source: "sse_etf_volume"},
	})
	if err != nil {
		t.Fatal(err)
	}

	latest, ok, err := store.LatestShareHistoryDateBySymbolPrefix(ctx, "SH")
	if err != nil {
		t.Fatal(err)
	}
	if !ok || latest != "2026-06-11" {
		t.Fatalf("latest SH = %q %v, want 2026-06-11 true", latest, ok)
	}

	latest, ok, err = store.LatestShareHistoryDateBySymbolPrefix(ctx, "BJ")
	if err != nil {
		t.Fatal(err)
	}
	if ok || latest != "" {
		t.Fatalf("latest BJ = %q %v, want empty false", latest, ok)
	}
}

func TestCSVStoreBackfillClosePremium(t *testing.T) {
	ctx := context.Background()
	store := NewCSVStore(t.TempDir())
	err := store.UpsertShareHistory(ctx, []domain.ShareHistoryRecord{
		{Symbol: "SH513050", ShareDate: "2026-06-16", FundType: "ETF", Shares10K: 800, Source: sourceName},
		{Symbol: "SH513050", ShareDate: "2026-06-17", FundType: "ETF", Shares10K: 620, Source: sourceName},
	})
	if err != nil {
		t.Fatal(err)
	}
	store.SetClosePremiumLookup(fakeClosePremiumLookup{
		values: map[string]float64{
			"SH513050|2026-06-17": 0.88,
		},
	})

	updated, err := store.BackfillClosePremium(ctx, []string{"SH513050"})
	if err != nil {
		t.Fatal(err)
	}
	if updated["SH513050"] != 1 {
		t.Fatalf("updated count = %d, want 1", updated["SH513050"])
	}
	rows, err := store.LoadShareHistory(ctx, "SH513050", 0)
	if err != nil {
		t.Fatal(err)
	}
	if rows[0].ClosePremiumPct == nil || *rows[0].ClosePremiumPct != 0.88 {
		t.Fatalf("latest close premium pct = %v, want 0.88", rows[0].ClosePremiumPct)
	}
}
