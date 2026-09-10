package closesync

import (
	"context"
	"testing"
	"time"

	"newnavnav/internal/domain"
)

type fakeQuoteProvider struct {
	quotes map[string]domain.Quote
}

func (p fakeQuoteProvider) CurrentQuotes() map[string]domain.Quote {
	return p.quotes
}

type fakeDailyPriceRepo struct {
	prices []domain.DailyPrice
}

func (r *fakeDailyPriceRepo) UpsertDailyPrices(ctx context.Context, prices []domain.DailyPrice) error {
	r.prices = append(r.prices, prices...)
	return nil
}

func TestNextRunTimeUsesMarketDST(t *testing.T) {
	ny, err := time.LoadLocation("America/New_York")
	if err != nil {
		t.Fatal(err)
	}

	summer := nextRunTime(time.Date(2026, 7, 1, 15, 0, 0, 0, ny), ny, 16, 20)
	if got := summer.Format("-0700"); got != "-0400" {
		t.Fatalf("summer offset = %s, want -0400", got)
	}
	if got := summer.Format("15:04"); got != "16:20" {
		t.Fatalf("summer local time = %s, want 16:20", got)
	}

	winter := nextRunTime(time.Date(2026, 1, 2, 15, 0, 0, 0, ny), ny, 16, 20)
	if got := winter.Format("-0700"); got != "-0500" {
		t.Fatalf("winter offset = %s, want -0500", got)
	}
	if got := winter.Format("15:04"); got != "16:20" {
		t.Fatalf("winter local time = %s, want 16:20", got)
	}
}

func TestPersistMarketCloseUsesRegularCloseForUSAfterHours(t *testing.T) {
	repo := &fakeDailyPriceRepo{}
	service := NewService(Options{
		Quotes: fakeQuoteProvider{quotes: map[string]domain.Quote{
			"QQQ": {
				Symbol:       "QQQ",
				Price:        520,
				PrevClose:    510,
				QuoteDate:    "2026-06-03",
				QuoteTime:    "05:10:00",
				Source:       "sina_us_after_hours",
				QuoteSession: "extended",
			},
			"SH518880": {
				Symbol: "SH518880",
				Price:  9.5,
				Source: "sina",
			},
		}},
		Repository: repo,
	})

	ny, err := time.LoadLocation("America/New_York")
	if err != nil {
		t.Fatal(err)
	}
	runAt := time.Date(2026, 6, 2, 16, 20, 0, 0, ny)
	if err := service.PersistMarketClose(context.Background(), "us", runAt); err != nil {
		t.Fatal(err)
	}
	if len(repo.prices) != 1 {
		t.Fatalf("len(prices) = %d, want 1", len(repo.prices))
	}
	price := repo.prices[0]
	if price.Symbol != "QQQ" || price.Date != "2026-06-02" || price.Close != 510 {
		t.Fatalf("price = %+v, want QQQ 2026-06-02 close 510", price)
	}
}

func TestPersistUSCommodityFuturesBenchmarkAtStockClose(t *testing.T) {
	repo := &fakeDailyPriceRepo{}
	service := NewService(Options{
		Quotes: fakeQuoteProvider{quotes: map[string]domain.Quote{
			"HF_GC": {
				Symbol:    "HF_GC",
				Price:     4510.25,
				PrevClose: 4400,
				Source:    "sina_hf",
			},
			"QQQ": {
				Symbol: "QQQ",
				Price:  510,
				Source: "sina_us",
			},
			"HF_NQ": {
				Symbol:    "HF_NQ",
				Price:     30707.195,
				PrevClose: 30712.750,
				Source:    "sina_hf",
			},
		}},
		Repository: repo,
	})

	ny, err := time.LoadLocation("America/New_York")
	if err != nil {
		t.Fatal(err)
	}
	runAt := time.Date(2026, 6, 2, 16, 0, 0, 0, ny)
	if err := service.PersistMarketClose(context.Background(), "us_commodity_futures", runAt); err != nil {
		t.Fatal(err)
	}
	if len(repo.prices) != 2 {
		t.Fatalf("len(prices) = %d, want 2", len(repo.prices))
	}
	got := map[string]domain.DailyPrice{}
	for _, price := range repo.prices {
		got[price.Symbol] = price
	}
	if price := got["HF_GC"]; price.Symbol != "HF_GC" || price.Date != "2026-06-02" || price.Close != 4510.25 {
		t.Fatalf("HF_GC price = %+v, want 2026-06-02 close 4510.25", price)
	}
	if price := got["HF_NQ"]; price.Symbol != "HF_NQ" || price.Date != "2026-06-02" || price.Close != 30707.195 {
		t.Fatalf("HF_NQ price = %+v, want 2026-06-02 close 30707.195", price)
	}
}

func TestPersistMarketCloseSkipsMarketWeekend(t *testing.T) {
	repo := &fakeDailyPriceRepo{}
	service := NewService(Options{
		Quotes: fakeQuoteProvider{quotes: map[string]domain.Quote{
			"QQQ": {Symbol: "QQQ", Price: 510, Source: "sina_us"},
		}},
		Repository: repo,
	})

	ny, err := time.LoadLocation("America/New_York")
	if err != nil {
		t.Fatal(err)
	}
	runAt := time.Date(2026, 6, 6, 16, 20, 0, 0, ny)
	if err := service.PersistMarketClose(context.Background(), "us", runAt); err != nil {
		t.Fatal(err)
	}
	if len(repo.prices) != 0 {
		t.Fatalf("len(prices) = %d, want 0 on market weekend", len(repo.prices))
	}
}
