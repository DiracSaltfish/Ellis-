package hkconnectfx

import (
	"testing"
	"time"
)

func TestRestoreStoredMinuteStateUsesLastLiveInputs(t *testing.T) {
	now := localTime(t, "2026-09-02 19:28:00")
	service := NewService(Options{DataDir: t.TempDir(), Now: func() time.Time { return now }})
	day := now.Format("2006-01-02")

	for _, fixture := range []struct {
		market string
		buy    float64
		sell   float64
	}{
		{market: marketShanghai, buy: 289.79, sell: 231.62},
		{market: marketShenzhen, buy: 155.25, sell: 104.75},
	} {
		oldBuy, oldSell, oldTotal := 1.0, 2.0, 3.0
		oldBid, oldAsk := .85001, .85002
		if err := appendMinuteCSV(service.minutePathForMarket(day, fixture.market), MinutePoint{
			Timestamp:             "2026-09-02T15:59:00+08:00",
			TradeDate:             day,
			FlowBuyAmountHKD100:   &oldBuy,
			FlowSellAmountHKD100:  &oldSell,
			FlowTotalAmountHKD100: &oldTotal,
			HKDCNYBid:             &oldBid,
			HKDCNYAsk:             &oldAsk,
			FlowFetchedAt:         "2026-09-02T15:59:01+08:00",
			FlowLastChangedAt:     "2026-09-02T15:58:30+08:00",
			FXObservedAt:          "2026-09-02T15:59:00+08:00",
		}); err != nil {
			t.Fatal(err)
		}

		total := fixture.buy + fixture.sell
		bid, ask := .85721, .85722
		if err := appendMinuteCSV(service.minutePathForMarket(day, fixture.market), MinutePoint{
			Timestamp:             "2026-09-02T16:00:00+08:00",
			TradeDate:             day,
			FlowBuyAmountHKD100:   &fixture.buy,
			FlowSellAmountHKD100:  &fixture.sell,
			FlowTotalAmountHKD100: &total,
			HKDCNYBid:             &bid,
			HKDCNYAsk:             &ask,
			FlowFetchedAt:         "2026-09-02T16:00:02+08:00",
			FlowLastChangedAt:     "2026-09-02T15:59:30+08:00",
			FXObservedAt:          "2026-09-02T15:59:59+08:00",
		}); err != nil {
			t.Fatal(err)
		}
	}

	currentBid, currentAsk := .86001, .86002
	service.fx = FXQuote{Pair: "HKD/CNY", Bid: &currentBid, Ask: &currentAsk}
	service.flow = &Flow{BuyAmountHKD100: 9, SellAmountHKD100: 9}
	service.shenzhenFlow = &Flow{BuyAmountHKD100: 8, SellAmountHKD100: 8}
	service.restoreStoredMinuteState(now)

	if service.flow == nil || service.flow.BuyAmountHKD100 != 289.79 || service.flow.SellAmountHKD100 != 231.62 {
		t.Fatalf("Shanghai flow was not restored from 16:00: %+v", service.flow)
	}
	if service.shenzhenFlow == nil || service.shenzhenFlow.BuyAmountHKD100 != 155.25 || service.shenzhenFlow.SellAmountHKD100 != 104.75 {
		t.Fatalf("Shenzhen flow was not restored from 16:00: %+v", service.shenzhenFlow)
	}
	if service.fx.Bid == nil || service.fx.Ask == nil || *service.fx.Bid != .85721 || *service.fx.Ask != .85722 {
		t.Fatalf("HKD/CNY quote was not restored from 16:00: %+v", service.fx)
	}
	wantObserved := localTime(t, "2026-09-02 15:59:59")
	if service.fx.ObservedAt == nil || !service.fx.ObservedAt.Equal(wantObserved) {
		t.Fatalf("observed_at=%v want %v", service.fx.ObservedAt, wantObserved)
	}
	if quote := service.fxQuotes["HKD/CNY"]; quote.Bid == nil || *quote.Bid != .85721 {
		t.Fatalf("fx_quotes HKD/CNY was not restored: %+v", quote)
	}
}
