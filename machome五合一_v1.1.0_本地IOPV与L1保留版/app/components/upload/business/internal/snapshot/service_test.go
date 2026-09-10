package snapshot

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"

	"newnavnav/internal/domain"
	"newnavnav/internal/sharesync"
)

type fakeSinaClient struct {
	responses []map[string]domain.Quote
	calls     int
}

func (c *fakeSinaClient) FetchQuotes(ctx context.Context, symbols []string) (map[string]domain.Quote, error) {
	if c.calls >= len(c.responses) {
		c.calls++
		return map[string]domain.Quote{}, nil
	}
	out := c.responses[c.calls]
	c.calls++
	return out, nil
}

type trackingValuationRepo struct {
	data             domain.ValuationData
	latestQuotes     map[string]domain.Quote
	ensureCalls      int
	upsertQuoteCalls int
	loadQuoteCalls   int
}

func (r *trackingValuationRepo) EnsureSymbols(ctx context.Context, symbols []string) error {
	r.ensureCalls++
	return nil
}

func (r *trackingValuationRepo) UpsertLatestQuotes(ctx context.Context, quotes map[string]domain.Quote) error {
	r.upsertQuoteCalls++
	return nil
}

func (r *trackingValuationRepo) UpsertDailyPrices(ctx context.Context, prices []domain.DailyPrice) error {
	return nil
}

func (r *trackingValuationRepo) LoadLatestQuotes(ctx context.Context) (map[string]domain.Quote, error) {
	r.loadQuoteCalls++
	return r.latestQuotes, nil
}

func (r *trackingValuationRepo) LoadValuationData(ctx context.Context) (domain.ValuationData, error) {
	return r.data, nil
}

func TestShouldPreferStoredQuoteForSameDayIBOvernight(t *testing.T) {
	now := time.Date(2026, 6, 4, 16, 40, 0, 0, time.Local)
	fetched := domain.Quote{
		Symbol:       "ARKK",
		Price:        77.70,
		QuoteDate:    "2026-06-04",
		Source:       "sina_us_after_hours",
		QuoteSession: "extended",
	}
	stored := domain.Quote{
		Symbol:       "ARKK",
		Price:        77.57,
		QuoteDate:    "2026-06-04",
		Source:       "us_overnight_live:trades",
		QuoteSession: "us_overnight_live",
	}
	if !shouldPreferStoredQuote(now, fetched, stored) {
		t.Fatal("expected same-day IB overnight quote to override sina_us_after_hours")
	}
}

func TestShouldNotPreferStoredQuoteFromPreviousDay(t *testing.T) {
	now := time.Date(2026, 6, 4, 16, 40, 0, 0, time.Local)
	fetched := domain.Quote{
		Symbol:       "ARKK",
		Price:        77.70,
		QuoteDate:    "2026-06-04",
		Source:       "sina_us_after_hours",
		QuoteSession: "extended",
	}
	stored := domain.Quote{
		Symbol:       "ARKK",
		Price:        77.57,
		QuoteDate:    "2026-06-03",
		Source:       "us_overnight_live:trades",
		QuoteSession: "us_overnight_live",
	}
	if shouldPreferStoredQuote(now, fetched, stored) {
		t.Fatal("did not expect previous-day stored quote to override current fetched quote")
	}
}

func TestApplyUploadedQuotesDoesNotOverrideFreshFetchedQuoteWithStaleUpload(t *testing.T) {
	service := NewService(ServiceOptions{EnableUploadQuotes: true})
	now := time.Date(2026, 6, 22, 9, 35, 0, 0, shanghaiLocation())
	service.uploadedQuotes["XOP"] = domain.Quote{
		Symbol:         "XOP",
		Name:           "old ib overnight",
		Price:          155.41,
		PrevClose:      155.67,
		QuoteDate:      "2026-06-18",
		QuoteTime:      "15:00:00",
		Source:         "us_overnight_live:trades",
		SourceSymbol:   "XOP",
		QuoteSession:   "us_overnight_live",
		IsRealtime:     true,
		RealtimeStatus: "realtime",
		FetchedAt:      now.Add(-2 * uploadedQuoteStaleAfter),
	}

	quotes := map[string]domain.Quote{
		"XOP": {
			Symbol:         "XOP",
			Name:           "fresh fallback",
			Price:          155.67,
			PrevClose:      155.67,
			QuoteDate:      "2026-06-18",
			QuoteTime:      "04:00:00",
			Source:         "sina_us",
			SourceSymbol:   "gb_xop",
			QuoteSession:   "regular",
			IsRealtime:     false,
			RealtimeStatus: "closed",
			StaleReason:    "market closed",
			FetchedAt:      now,
		},
	}

	hasFresh, uploadSource := service.applyUploadedQuotes(quotes, now)
	if hasFresh {
		t.Fatal("expected stale upload to not count as fresh")
	}
	if uploadSource != "" {
		t.Fatalf("upload source = %q, want empty", uploadSource)
	}

	got := quotes["XOP"]
	if got.Price != 155.67 {
		t.Fatalf("quote price = %.2f, want fetched 155.67", got.Price)
	}
	if got.Source != "sina_us" {
		t.Fatalf("quote source = %q, want sina_us", got.Source)
	}
}

func TestRefreshKeepsPreviousQuotesInMemoryWithoutMySQLQuotePersistence(t *testing.T) {
	repo := &trackingValuationRepo{}
	sina := &fakeSinaClient{
		responses: []map[string]domain.Quote{
			{
				"SZ159529": {
					Symbol:         "SZ159529",
					Name:           "test",
					Price:          1.234,
					PrevClose:      1.2,
					QuoteDate:      "2026-06-04",
					QuoteTime:      "10:00:00",
					Source:         "sina",
					SourceSymbol:   "sz159529",
					QuoteSession:   "cn_regular",
					IsRealtime:     true,
					RealtimeStatus: "realtime",
					FetchedAt:      time.Date(2026, 6, 4, 10, 0, 0, 0, time.Local),
				},
			},
			{},
		},
	}
	service := NewService(ServiceOptions{
		Sina:       sina,
		Repository: repo,
		EnableSina: true,
	})

	if err := service.Refresh(context.Background()); err != nil {
		t.Fatalf("first refresh failed: %v", err)
	}
	first, ok := service.FundSnapshot("SZ159529")
	if !ok {
		t.Fatal("expected first snapshot to exist")
	}
	if first.Quote.Price != 1.234 {
		t.Fatalf("first quote price = %.3f, want 1.234", first.Quote.Price)
	}

	if err := service.Refresh(context.Background()); err != nil {
		t.Fatalf("second refresh failed: %v", err)
	}
	second, ok := service.FundSnapshot("SZ159529")
	if !ok {
		t.Fatal("expected second snapshot to exist")
	}
	if second.Quote.Price != first.Quote.Price {
		t.Fatalf("second quote price = %.3f, want cached %.3f", second.Quote.Price, first.Quote.Price)
	}
	if repo.ensureCalls != 0 {
		t.Fatalf("ensure symbols called %d times, want 0", repo.ensureCalls)
	}
	if repo.upsertQuoteCalls != 0 {
		t.Fatalf("upsert latest quotes called %d times, want 0", repo.upsertQuoteCalls)
	}
	if repo.loadQuoteCalls != 0 {
		t.Fatalf("load latest quotes called %d times, want 0", repo.loadQuoteCalls)
	}
}

func TestWarmLatestQuotesSeedsRestartFallbackWithoutFreshMinuteHistory(t *testing.T) {
	root := t.TempDir()
	now := time.Date(2026, 6, 6, 10, 1, 0, 0, shanghaiLocation())
	repo := &trackingValuationRepo{
		latestQuotes: map[string]domain.Quote{
			"SZ159529": {
				Symbol:         "SZ159529",
				Name:           "warm quote",
				Price:          1.234,
				PrevClose:      1.2,
				LimitUp:        1.32,
				LimitDown:      1.08,
				BidLevels:      []domain.Level{{Level: 1, Price: 1.233, Volume: 1000}},
				AskLevels:      []domain.Level{{Level: 1, Price: 1.234, Volume: 2000}},
				QuoteDate:      "2026-06-05",
				QuoteTime:      "14:59:30",
				Source:         "sina",
				SourceSymbol:   "sz159529",
				QuoteSession:   "cn_regular",
				IsRealtime:     false,
				RealtimeStatus: "stale",
				StaleReason:    "latest stored quote fallback",
				FetchedAt:      time.Date(2026, 6, 5, 14, 59, 30, 0, shanghaiLocation()),
			},
		},
	}
	service := NewService(ServiceOptions{
		Repository:         repo,
		MinuteHistoryDir:   root,
		EnableUploadQuotes: true,
	})
	service.now = func() time.Time { return now }

	if err := service.WarmLatestQuotes(context.Background()); err != nil {
		t.Fatalf("warm latest quotes failed: %v", err)
	}
	if repo.loadQuoteCalls != 1 {
		t.Fatalf("load latest quotes called %d times, want 1", repo.loadQuoteCalls)
	}

	if err := service.Refresh(context.Background()); err != nil {
		t.Fatalf("refresh failed: %v", err)
	}

	snapshot, ok := service.FundSnapshot("SZ159529")
	if !ok {
		t.Fatal("expected warmed fallback snapshot to exist")
	}
	if snapshot.Quote.Price != 1.234 {
		t.Fatalf("quote price = %.3f, want 1.234", snapshot.Quote.Price)
	}
	if snapshot.Quote.RealtimeStatus != "stale" {
		t.Fatalf("quote realtime status = %q, want stale", snapshot.Quote.RealtimeStatus)
	}
	if len(snapshot.Quote.BidLevels) != 1 || len(snapshot.Quote.AskLevels) != 1 {
		t.Fatalf("order book not preserved in warm fallback: %+v", snapshot.Quote)
	}

	historyPath := filepath.Join(root, minuteHistoryDay(now), minuteHistoryFile)
	if _, err := os.Stat(historyPath); !os.IsNotExist(err) {
		t.Fatalf("minute history file should not exist for stale warm fallback, err=%v", err)
	}
}

func TestYesterdayRedemptionBoardUsesLatestDisclosureDate(t *testing.T) {
	store := sharesync.NewCSVStore(t.TempDir())
	err := store.UpsertShareHistory(context.Background(), []domain.ShareHistoryRecord{
		{Symbol: "SH513050", Name: "中概互联ETF", FundType: "ETF", ShareDate: "2026-06-16", Shares10K: 800, Source: "sse_etf_volume"},
		{Symbol: "SH513050", Name: "中概互联ETF", FundType: "ETF", ShareDate: "2026-06-17", Shares10K: 620, ClosePremiumPct: ptrFloat(1.12), Source: "sse_etf_volume"},
		{Symbol: "SH513000", Name: "日经ETF", FundType: "ETF", ShareDate: "2026-06-16", Shares10K: 1000, Source: "sse_etf_volume"},
		{Symbol: "SH513000", Name: "日经ETF", FundType: "ETF", ShareDate: "2026-06-17", Shares10K: 1080, ClosePremiumPct: ptrFloat(-0.45), Source: "sse_etf_volume"},
		{Symbol: "SZ161226", Name: "国投白银LOF", FundType: "LOF", ShareDate: "2026-06-16", Shares10K: 3000, Source: "szse_fund_size"},
	})
	if err != nil {
		t.Fatal(err)
	}

	service := NewService(ServiceOptions{ShareHistory: store})
	board, err := service.YesterdayRedemptionBoard(context.Background())
	if err != nil {
		t.Fatal(err)
	}

	if board.ShareDate != "2026-06-17" {
		t.Fatalf("share date = %s, want 2026-06-17", board.ShareDate)
	}
	if board.IncludedSymbols != 2 {
		t.Fatalf("included symbols = %d, want 2", board.IncludedSymbols)
	}
	if board.StaleSymbols != 1 {
		t.Fatalf("stale symbols = %d, want 1", board.StaleSymbols)
	}
	if board.RedemptionCount != 1 || board.SubscriptionCount != 1 || board.FlatCount != 0 {
		t.Fatalf("unexpected counts: %+v", board)
	}
	if len(board.Rows) != 2 {
		t.Fatalf("rows = %d, want 2", len(board.Rows))
	}
	if board.Rows[0].Symbol != "SH513050" {
		t.Fatalf("first row symbol = %s, want SH513050", board.Rows[0].Symbol)
	}
	if board.Rows[0].ShareChange10K == nil || *board.Rows[0].ShareChange10K != -180 {
		t.Fatalf("first row change = %v, want -180", board.Rows[0].ShareChange10K)
	}
	if board.Rows[0].ClosePremiumPct == nil || *board.Rows[0].ClosePremiumPct != 1.12 {
		t.Fatalf("first row close premium pct = %v, want 1.12", board.Rows[0].ClosePremiumPct)
	}
	if got := len(board.Rows[0].BranchNames); got != 1 || board.Rows[0].BranchNames[0] != "混合QDII" {
		t.Fatalf("first row branch names = %v, want [混合QDII]", board.Rows[0].BranchNames)
	}
	if board.Rows[1].Symbol != "SH513000" {
		t.Fatalf("second row symbol = %s, want SH513000", board.Rows[1].Symbol)
	}
}

func TestRestartWarmsFromPersistedUploadedQuoteCache(t *testing.T) {
	root := t.TempDir()
	cachePath := filepath.Join(root, "uploaded_quotes.latest.json")
	writer := NewService(ServiceOptions{
		UploadedQuoteCachePath: cachePath,
		EnableUploadQuotes:     true,
	})
	accepted, warnings := writer.UpsertUploadedQuotes("home-mac", []domain.Quote{{
		Symbol:         "SZ159529",
		Name:           "cached quote",
		Price:          1.456,
		PrevClose:      1.4,
		LimitUp:        1.54,
		LimitDown:      1.26,
		BidLevels:      []domain.Level{{Level: 1, Price: 1.455, Volume: 3000}},
		AskLevels:      []domain.Level{{Level: 1, Price: 1.456, Volume: 4000}},
		QuoteDate:      "2026-06-06",
		QuoteTime:      "10:03:00",
		Source:         "upload:home-mac",
		SourceSymbol:   "sz159529",
		QuoteSession:   "external_upload",
		IsRealtime:     true,
		RealtimeStatus: "realtime",
		FetchedAt:      time.Date(2026, 6, 6, 10, 3, 0, 0, shanghaiLocation()),
	}})
	if accepted != 1 || len(warnings) != 0 {
		t.Fatalf("unexpected upload result accepted=%d warnings=%v", accepted, warnings)
	}

	restarted := NewService(ServiceOptions{
		UploadedQuoteCachePath: cachePath,
		EnableUploadQuotes:     true,
	})
	now := time.Date(2026, 6, 6, 10, 10, 0, 0, shanghaiLocation())
	restarted.now = func() time.Time { return now }
	if err := restarted.WarmLatestQuotes(context.Background()); err != nil {
		t.Fatalf("warm latest quotes from cache failed: %v", err)
	}
	if err := restarted.Refresh(context.Background()); err != nil {
		t.Fatalf("refresh failed: %v", err)
	}

	snapshot, ok := restarted.FundSnapshot("SZ159529")
	if !ok {
		t.Fatal("expected restarted snapshot to exist")
	}
	if snapshot.Quote.Price != 1.456 {
		t.Fatalf("quote price = %.3f, want cached 1.456", snapshot.Quote.Price)
	}
	if snapshot.Quote.RealtimeStatus != "stale" {
		t.Fatalf("quote realtime status = %q, want stale after restart", snapshot.Quote.RealtimeStatus)
	}
	if len(snapshot.Quote.BidLevels) != 1 || len(snapshot.Quote.AskLevels) != 1 {
		t.Fatalf("order book not restored from cache file: %+v", snapshot.Quote)
	}
}

func TestFreshUploadedQuotesOverrideWarmFallbackAfterRestart(t *testing.T) {
	repo := &trackingValuationRepo{
		latestQuotes: map[string]domain.Quote{
			"SZ159529": {
				Symbol:         "SZ159529",
				Name:           "warm quote",
				Price:          1.234,
				PrevClose:      1.2,
				QuoteDate:      "2026-06-05",
				QuoteTime:      "14:59:30",
				Source:         "sina",
				SourceSymbol:   "sz159529",
				QuoteSession:   "cn_regular",
				IsRealtime:     false,
				RealtimeStatus: "stale",
				StaleReason:    "latest stored quote fallback",
				FetchedAt:      time.Date(2026, 6, 5, 14, 59, 30, 0, shanghaiLocation()),
			},
		},
	}
	now := time.Date(2026, 6, 6, 10, 2, 0, 0, shanghaiLocation())
	service := NewService(ServiceOptions{
		Repository:         repo,
		EnableUploadQuotes: true,
	})
	service.now = func() time.Time { return now }

	if err := service.WarmLatestQuotes(context.Background()); err != nil {
		t.Fatalf("warm latest quotes failed: %v", err)
	}
	accepted, warnings := service.UpsertUploadedQuotes("home-mac", []domain.Quote{{
		Symbol:       "SZ159529",
		Name:         "fresh quote",
		Price:        1.345,
		PrevClose:    1.2,
		BidLevels:    []domain.Level{{Level: 1, Price: 1.344, Volume: 1200}},
		AskLevels:    []domain.Level{{Level: 1, Price: 1.345, Volume: 1300}},
		QuoteDate:    "2026-06-06",
		QuoteTime:    "10:02:00",
		Source:       "upload:home-mac",
		SourceSymbol: "sz159529",
		QuoteSession: "external_upload",
		FetchedAt:    now,
	}})
	if accepted != 1 || len(warnings) != 0 {
		t.Fatalf("unexpected upload result accepted=%d warnings=%v", accepted, warnings)
	}

	if err := service.Refresh(context.Background()); err != nil {
		t.Fatalf("refresh failed: %v", err)
	}

	snapshot, ok := service.FundSnapshot("SZ159529")
	if !ok {
		t.Fatal("expected refreshed snapshot to exist")
	}
	if snapshot.Quote.Price != 1.345 {
		t.Fatalf("quote price = %.3f, want fresh uploaded 1.345", snapshot.Quote.Price)
	}
	if snapshot.Quote.RealtimeStatus != "realtime" {
		t.Fatalf("quote realtime status = %q, want realtime", snapshot.Quote.RealtimeStatus)
	}
	if len(snapshot.Quote.BidLevels) != 1 || len(snapshot.Quote.AskLevels) != 1 {
		t.Fatalf("fresh order book missing after upload override: %+v", snapshot.Quote)
	}
}
