package hkconnectfx

import (
	"context"
	"math"
	"net/http"
	"testing"
	"time"
)

func TestFetchCFETSSpotParsesDirectHKDCNYPair(t *testing.T) {
	now := localTime(t, "2026-08-21 14:35:30")
	service := NewService(Options{DataDir: t.TempDir(), Now: func() time.Time { return now }, HTTPClient: &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		return jsonResponse(`{"head":{"rep_code":"200","ts":1787294130000},"data":{"showDateCN":"2026-08-21 14:35:30"},"records":[{"ccyPair":"USD/CNY","bidPrc":"6.72","askPrc":"6.73"},{"ccyPair":"HKD/CNY","bidPrc":"0.85720","askPrc":"0.85722"},{"ccyPair":"EUR/CNY","bidPrc":"7.84","askPrc":"7.85"},{"ccyPair":"100JPY/CNY","bidPrc":"4.22","askPrc":"4.23"}]}`), nil
	})}})
	quotes, err := service.fetchCFETSSpot(context.Background(), now)
	if err != nil {
		t.Fatal(err)
	}
	quote := quotes["HKD/CNY"]
	if quote.Pair != "HKD/CNY" || quote.Bid == nil || quote.Ask == nil || *quote.Bid != .85720 || *quote.Ask != .85722 || !quote.Healthy {
		t.Fatalf("unexpected direct quote: %+v", quote)
	}
	if quote.ObservedAt == nil || quote.ObservedAt.Format("2006-01-02 15:04:05") != "2026-08-21 14:35:30" {
		t.Fatalf("source timestamp was not preserved: %+v", quote)
	}
	if len(quotes) != 4 || quotes["USD/CNY"].Bid == nil || *quotes["USD/CNY"].Bid != 6.72 || quotes["100JPY/CNY"].Ask == nil || *quotes["100JPY/CNY"].Ask != 4.23 {
		t.Fatalf("single ChinaMoney response must populate all private FX pairs: %+v", quotes)
	}
}

func TestFetchCFETSSpotRejectsMissingPairAndInvertedSides(t *testing.T) {
	now := localTime(t, "2026-08-21 14:35:30")
	for name, body := range map[string]string{
		"missing required pairs": `{"head":{"rep_code":"200"},"data":{"showDateCN":"2026-08-21 14:35:30"},"records":[{"ccyPair":"HKD/CNY","bidPrc":"0.85720","askPrc":"0.85722"}]}`,
		"ask below bid":          `{"head":{"rep_code":"200"},"data":{"showDateCN":"2026-08-21 14:35:30"},"records":[{"ccyPair":"HKD/CNY","bidPrc":"0.85722","askPrc":"0.85720"}]}`,
	} {
		t.Run(name, func(t *testing.T) {
			service := NewService(Options{DataDir: t.TempDir(), Now: func() time.Time { return now }, HTTPClient: &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
				return jsonResponse(body), nil
			})}})
			quotes, err := service.fetchCFETSSpot(context.Background(), now)
			if err != nil {
				t.Fatal(err)
			}
			if quotes["USD/CNY"].Healthy {
				t.Fatal("missing USD pair must fail closed")
			}
			if quotes["HKD/CNY"].Healthy != (name == "missing required pairs") {
				t.Fatal("HKD health must depend only on its own sides")
			}
		})
	}
}

func TestDirectionalSelectionUsesAskForNetBuyAndBidForNetSell(t *testing.T) {
	now := localTime(t, "2026-08-21 14:35:00")
	buyService := seededService(t, now, 300, 200)
	buy := buyService.Snapshot().Estimate
	if buy == nil || math.Abs(buy.SelectedHKDCNY-.85722) > 1e-12 {
		t.Fatalf("net buy must use ask: %+v", buy)
	}
	sellService := seededService(t, now, 100, 300)
	sell := sellService.Snapshot().Estimate
	if sell == nil || math.Abs(sell.SelectedHKDCNY-.85720) > 1e-12 {
		t.Fatalf("net sell must use bid: %+v", sell)
	}
}

func TestCFETSSchedulerIncludesLunchAndExactClose(t *testing.T) {
	for _, minute := range []int{9*60 + 10, 12 * 60, 14*60 + 30, 16 * 60} {
		if !isScheduledCFETSPollMinute(minute) {
			t.Fatalf("minute %d must be scheduled", minute)
		}
	}
	for _, minute := range []int{9*60 + 9, 16*60 + 1} {
		if isScheduledCFETSPollMinute(minute) {
			t.Fatalf("minute %d must be outside CFETS polling", minute)
		}
	}
}

func TestCFETSHistorySchedulerRetriesAfterMorningFailure(t *testing.T) {
	for _, minute := range []int{9*60 + 5, 9*60 + 35, 10*60 + 5, 16*60 + 5} {
		if !isScheduledCFETSHistoryMinute(minute) {
			t.Fatalf("minute %d must retry CFETS history", minute)
		}
	}
	for _, minute := range []int{9*60 + 4, 9*60 + 6, 10 * 60, 16*60 + 6} {
		if isScheduledCFETSHistoryMinute(minute) {
			t.Fatalf("minute %d must be outside CFETS history retries", minute)
		}
	}

	service := NewService(Options{DataDir: t.TempDir()})
	if !service.shouldPollCFETSHistory("2026-08-27", "2026-08-27T09:05") {
		t.Fatal("history must be requested until a successful fetch is recorded")
	}
	if service.shouldPollCFETSHistory("2026-08-27", "2026-08-27T09:05") {
		t.Fatal("history must run at most once per scheduled retry minute")
	}
	if !service.shouldPollCFETSHistory("2026-08-27", "2026-08-27T09:35") {
		t.Fatal("history must retry at the next scheduled minute after failure")
	}
	service.lastCFETSHistoryDate = "2026-08-27"
	if service.shouldPollCFETSHistory("2026-08-27", "2026-08-27T10:05") {
		t.Fatal("history must stop retrying after a successful fetch")
	}
}

func TestCFETSPollFailurePreservesLastGoodQuote(t *testing.T) {
	now := localTime(t, "2026-08-21 14:35:00")
	service := seededService(t, now, 300, 200)
	oldBid := *service.fx.Bid
	service.httpClient = &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		return jsonResponse(`{"head":{"rep_code":"500"},"data":{},"records":[]}`), nil
	})}
	service.pollCFETSSpot(context.Background(), now)
	if service.fx.Healthy || service.fx.Bid == nil || *service.fx.Bid != oldBid || service.fx.Error == "" {
		t.Fatalf("last good quote should be retained with failure state: %+v", service.fx)
	}
}

func TestFetchCentralParityPaginatesAndKeepsHKDCNYDirection(t *testing.T) {
	now := localTime(t, "2026-09-02 09:46:00")
	requests := 0
	service := NewService(Options{DataDir: t.TempDir(), Now: func() time.Time { return now }, HTTPClient: &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		requests++
		if request.URL.Path != "/ags/ms/cm-u-bk-ccpr/CcprHisNew" || request.URL.Query().Get("currency") != "HKD/CNY" {
			t.Fatalf("unexpected central parity request: %s", request.URL)
		}
		if request.Header.Get("Referer") != ChinaMoneyCentralParityPage {
			t.Fatalf("unexpected central parity referer: %q", request.Header.Get("Referer"))
		}
		switch request.URL.Query().Get("pageNum") {
		case "1":
			return jsonResponse(`{"head":{"rep_code":"200"},"data":{"currency":"HKD/CNY","pageTotal":2},"records":[{"date":"2026-09-02","values":["0.86497"]}]}`), nil
		case "2":
			return jsonResponse(`{"head":{"rep_code":"200"},"data":{"currency":"HKD/CNY","pageTotal":2},"records":[{"date":"2026-09-01","values":["0.86498"]}]}`), nil
		default:
			t.Fatalf("unexpected central parity page: %s", request.URL)
			return nil, nil
		}
	})}})
	rates, err := service.fetchCentralParity(context.Background(), now.AddDate(0, 0, -120), now)
	if err != nil {
		t.Fatal(err)
	}
	if requests != 2 || len(rates) != 2 || rates[0].TradeDate != "2026-09-01" || rates[1].Rate != .86497 || rates[1].Pair != "HKD/CNY" {
		t.Fatalf("unexpected central parity history: requests=%d rates=%+v", requests, rates)
	}
}

func TestCentralParityPersistsAndStopsPollingForTheDay(t *testing.T) {
	now := localTime(t, "2026-09-02 09:46:00")
	dataDir := t.TempDir()
	requests := 0
	service := NewService(Options{DataDir: dataDir, Now: func() time.Time { return now }, HTTPClient: &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		requests++
		return jsonResponse(`{"head":{"rep_code":"200"},"data":{"currency":"HKD/CNY","pageTotal":1},"records":[{"date":"2026-09-02","values":["0.86497"]},{"date":"2026-09-01","values":["0.86498"]}]}`), nil
	})}})
	if !service.shouldPollCentralParity("2026-09-02", "2026-09-02T09:46") {
		t.Fatal("central parity should poll before today's value is cached")
	}
	service.pollCentralParity(context.Background(), now)
	if requests != 1 || service.centralParities["2026-09-02"].Rate != .86497 {
		t.Fatalf("today's central parity was not cached: requests=%d rates=%+v", requests, service.centralParities)
	}
	if service.shouldPollCentralParity("2026-09-02", "2026-09-02T10:16") {
		t.Fatal("central parity must not be requested again after a successful daily fetch")
	}
	restarted := NewService(Options{DataDir: dataDir, Now: func() time.Time { return now }})
	if restarted.shouldPollCentralParity("2026-09-02", "2026-09-02T10:16") {
		t.Fatal("persisted central parity must suppress another request after restart")
	}
	snapshot := restarted.Snapshot()
	if snapshot.CentralParity == nil || snapshot.CentralParity.Pair != "HKD/CNY" || snapshot.CentralParity.Rate != .86497 {
		t.Fatalf("central parity missing from snapshot after restart: %+v", snapshot.CentralParity)
	}
}

func TestCentralParitySchedulerStartsAfterOfficialPublication(t *testing.T) {
	for _, minute := range []int{9*60 + 16, 9*60 + 46, 16*60 + 16} {
		if !isScheduledCentralParityMinute(minute) {
			t.Fatalf("minute %d must be scheduled", minute)
		}
	}
	for _, minute := range []int{9*60 + 15, 9*60 + 17, 16*60 + 17} {
		if isScheduledCentralParityMinute(minute) {
			t.Fatalf("minute %d must be outside the central parity schedule", minute)
		}
	}
}
