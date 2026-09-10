package web

import (
	"bytes"
	"context"
	"encoding/json"
	"math"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"newnavnav/internal/domain"
	"newnavnav/internal/privatevaluation"
)

type fakePrivatePremiumService struct {
	*fakePrivateValuationService
	funds map[string]privatevaluation.Snapshot
}

type cancelingPremiumStreamWriter struct {
	header http.Header
	body   bytes.Buffer
	status int
	cancel context.CancelFunc
}

func (w *cancelingPremiumStreamWriter) Header() http.Header {
	return w.header
}

func (w *cancelingPremiumStreamWriter) WriteHeader(status int) {
	w.status = status
}

func (w *cancelingPremiumStreamWriter) Write(payload []byte) (int, error) {
	written, err := w.body.Write(payload)
	if strings.Contains(w.body.String(), "data:") {
		w.cancel()
	}
	return written, err
}

func (w *cancelingPremiumStreamWriter) Flush() {}

func (s *fakePrivatePremiumService) Fund(symbol string) (privatevaluation.Snapshot, bool) {
	snapshot, ok := s.funds[symbol]
	return snapshot, ok
}

func premiumTestSnapshot(symbol string) privatevaluation.Snapshot {
	quoteAt := time.Date(2026, 8, 24, 2, 30, 5, 123_000_000, time.UTC)
	inputAt := quoteAt.Add(-2 * time.Second)
	return privatevaluation.Snapshot{
		Symbol: symbol,
		Input: &privatevaluation.Input{
			Symbol:     symbol,
			ReceivedAt: inputAt,
		},
		DomesticQuote: &domain.Quote{Symbol: symbol, FetchedAt: quoteAt},
		OrderBook: []privatevaluation.OrderBookValuation{
			{Side: "ask", Level: 3, Price: 1.023, PremiumRateVsBasketBidNAV: 0.023, PremiumRateVsBasketAskNAV: 0.013},
			{Side: "ask", Level: 2, Price: 1.022, Volume: 30000, PremiumRateVsBasketBidNAV: 0.022, PremiumRateVsBasketAskNAV: 0.012},
			{Side: "ask", Level: 1, Price: 1.021, Volume: 20000, PremiumRateVsBasketBidNAV: 0.021, PremiumRateVsBasketAskNAV: 0.011},
			{Side: "bid", Level: 1, Price: 1.019, Volume: 18000, PremiumRateVsBasketBidNAV: 0.019, PremiumRateVsBasketAskNAV: 0.009},
			{Side: "bid", Level: 2, Price: 1.018, Volume: 16000, PremiumRateVsBasketBidNAV: 0.018, PremiumRateVsBasketAskNAV: 0.008},
		},
	}
}

func TestPrivatePremiumSnapshotIsOpenAndMinimal(t *testing.T) {
	symbol := privatevaluation.TargetSymbol
	service := &fakePrivatePremiumService{
		fakePrivateValuationService: &fakePrivateValuationService{},
		funds:                       map[string]privatevaluation.Snapshot{symbol: premiumTestSnapshot(symbol)},
	}
	server := NewServer(nil, t.TempDir(), "")
	server.SetPrivateValuationService(service)
	recorder := httptest.NewRecorder()
	server.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/api/v1/private/premiums/snapshot?symbols=sz159518", nil))

	if recorder.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", recorder.Code, recorder.Body.String())
	}
	if recorder.Header().Get("Access-Control-Allow-Origin") != "*" {
		t.Fatalf("expected open CORS header")
	}
	var items []privatePremiumItem
	if err := json.Unmarshal(recorder.Body.Bytes(), &items); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if len(items) != 1 || items[0].Symbol != symbol || items[0].Quotes.Ask2 == nil || items[0].Quotes.Bid2 == nil {
		t.Fatalf("unexpected response: %+v", items)
	}
	if items[0].Timestamp == nil || *items[0].Timestamp != service.funds[symbol].Input.ReceivedAt.UnixMilli() {
		t.Fatalf("timestamp must use the conservative source watermark: %+v", items[0].Timestamp)
	}
	body := recorder.Body.String()
	for _, forbidden := range []string{"volume", "name", "model_version", "valuation_kind", "settlement_premium_rate"} {
		if strings.Contains(body, forbidden) {
			t.Fatalf("minimal payload unexpectedly contains %q: %s", forbidden, body)
		}
	}
}

func TestPrivatePremiumIndiaAlwaysUsesNiftyBridge(t *testing.T) {
	snapshot := premiumTestSnapshot(privatevaluation.SZ164824Symbol)
	snapshot.OrderBook[1].Price = 9.999
	bridgeBook := append([]privatevaluation.OrderBookValuation(nil), snapshot.OrderBook...)
	bridgeBook[1].Price = 1.234
	snapshot.IndiaValuations = &privatevaluation.IndiaValuations{
		NiftyBridge: &privatevaluation.IndiaValuationVariant{OrderBook: bridgeBook},
	}
	item := buildPrivatePremiumItem(privatevaluation.SZ164824Symbol, snapshot)
	if item.Quotes.Ask2 == nil || item.Quotes.Ask2.Price != 1.234 {
		t.Fatalf("expected NIFTY bridge ask2, got %+v", item.Quotes.Ask2)
	}
}

func TestPrivatePremiumSilverAddsSettlementRateOnlyForSilver(t *testing.T) {
	snapshot := premiumTestSnapshot(privatevaluation.SZ161226Symbol)
	snapshot.SilverValuation = &privatevaluation.SilverSettlementValuation{SettlementNAV: 1}
	item := buildPrivatePremiumItem(privatevaluation.SZ161226Symbol, snapshot)
	if item.Quotes.Ask1 == nil || item.Quotes.Ask1.SettlementPremiumRate == nil || math.Abs(*item.Quotes.Ask1.SettlementPremiumRate-0.021) > 1e-12 {
		t.Fatalf("unexpected silver settlement premium: %+v", item.Quotes.Ask1)
	}

	normal := buildPrivatePremiumItem(privatevaluation.TargetSymbol, premiumTestSnapshot(privatevaluation.TargetSymbol))
	encoded, err := json.Marshal(normal)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(encoded), "settlement_premium_rate") {
		t.Fatalf("non-silver payload must omit settlement premium: %s", encoded)
	}
}

func TestPrivatePremiumStreamWritesImmediateEvent(t *testing.T) {
	symbol := privatevaluation.TargetSymbol
	service := &fakePrivatePremiumService{
		fakePrivateValuationService: &fakePrivateValuationService{},
		funds:                       map[string]privatevaluation.Snapshot{symbol: premiumTestSnapshot(symbol)},
	}
	server := NewServer(nil, t.TempDir(), "")
	server.SetPrivateValuationService(service)
	ctx, cancel := context.WithCancel(context.Background())
	writer := &cancelingPremiumStreamWriter{header: make(http.Header), cancel: cancel}
	request := httptest.NewRequest(http.MethodGet, "/api/v1/private/premiums/stream?symbols="+symbol+"&interval_ms=5000", nil).WithContext(ctx)
	server.ServeHTTP(writer, request)

	if writer.status != http.StatusOK || writer.header.Get("Content-Type") != "text/event-stream; charset=utf-8" {
		t.Fatalf("unexpected stream response: %d %q", writer.status, writer.header.Get("Content-Type"))
	}
	body := writer.body.String()
	if !strings.Contains(body, "event: premiums\n") || !strings.Contains(body, `"symbol":"`+symbol+`"`) {
		t.Fatalf("unexpected SSE event: %q", body)
	}
}

func TestPrivatePremiumQueryValidation(t *testing.T) {
	server := NewServer(nil, t.TempDir(), "")
	server.SetPrivateValuationService(&fakePrivatePremiumService{
		fakePrivateValuationService: &fakePrivateValuationService{},
		funds:                       map[string]privatevaluation.Snapshot{},
	})
	for _, path := range []string{
		"/api/v1/private/premiums/snapshot",
		"/api/v1/private/premiums/snapshot?scope=all&symbols=SZ159518",
		"/api/v1/private/premiums/snapshot?symbols=BAD",
		"/api/v1/private/premiums/stream?scope=all&interval_ms=1000",
	} {
		recorder := httptest.NewRecorder()
		server.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, path, nil))
		if recorder.Code != http.StatusBadRequest {
			t.Fatalf("expected 400 for %s, got %d: %s", path, recorder.Code, recorder.Body.String())
		}
	}
}
