package web

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"strconv"
	"strings"
	"time"

	"newnavnav/internal/privatevaluation"
)

const (
	privatePremiumDefaultInterval = 5 * time.Second
	privatePremiumMinInterval     = 5 * time.Second
	privatePremiumMaxInterval     = 60 * time.Second
	privatePremiumHeartbeat       = 15 * time.Second
)

type privatePremiumLevel struct {
	Price                 float64  `json:"price"`
	BidPremiumRate        float64  `json:"bid_premium_rate"`
	AskPremiumRate        float64  `json:"ask_premium_rate"`
	SettlementPremiumRate *float64 `json:"settlement_premium_rate,omitempty"`
}

type privatePremiumQuotes struct {
	Ask2 *privatePremiumLevel `json:"ask2"`
	Ask1 *privatePremiumLevel `json:"ask1"`
	Bid1 *privatePremiumLevel `json:"bid1"`
	Bid2 *privatePremiumLevel `json:"bid2"`
}

type privatePremiumItem struct {
	Symbol    string               `json:"symbol"`
	Timestamp *int64               `json:"timestamp"`
	Quotes    privatePremiumQuotes `json:"quotes"`
}

func (s *Server) handlePrivatePremiumSnapshot(w http.ResponseWriter, r *http.Request) {
	setPrivateResponseHeaders(w)
	if r.Method != http.MethodGet {
		w.Header().Set("Allow", http.MethodGet)
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	if s.privateValuation == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"error": "private valuation service unavailable"})
		return
	}
	symbols, err := parsePrivatePremiumSymbols(r)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, s.privatePremiumItems(symbols))
}

func (s *Server) handlePrivatePremiumStream(w http.ResponseWriter, r *http.Request) {
	setPrivateResponseHeaders(w)
	if r.Method != http.MethodGet {
		w.Header().Set("Allow", http.MethodGet)
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	if s.privateValuation == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"error": "private valuation service unavailable"})
		return
	}
	symbols, err := parsePrivatePremiumSymbols(r)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	interval, err := parsePrivatePremiumInterval(r)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	flusher, ok := w.(http.Flusher)
	if !ok {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": "streaming unsupported"})
		return
	}

	w.Header().Set("Content-Type", "text/event-stream; charset=utf-8")
	w.Header().Set("Cache-Control", "no-cache, no-store")
	w.Header().Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)

	writeEvent := func() error {
		payload, marshalErr := json.Marshal(s.privatePremiumItems(symbols))
		if marshalErr != nil {
			return marshalErr
		}
		if _, writeErr := fmt.Fprintf(w, "event: premiums\nid: %d\ndata: %s\n\n", time.Now().UnixMilli(), payload); writeErr != nil {
			return writeErr
		}
		flusher.Flush()
		return nil
	}
	if err := writeEvent(); err != nil {
		return
	}

	updates := time.NewTicker(interval)
	heartbeats := time.NewTicker(privatePremiumHeartbeat)
	defer updates.Stop()
	defer heartbeats.Stop()
	for {
		select {
		case <-r.Context().Done():
			return
		case <-updates.C:
			if err := writeEvent(); err != nil {
				return
			}
		case <-heartbeats.C:
			if _, err := io.WriteString(w, ": heartbeat\n\n"); err != nil {
				return
			}
			flusher.Flush()
		}
	}
}

func parsePrivatePremiumSymbols(r *http.Request) ([]string, error) {
	query := r.URL.Query()
	scope := strings.ToLower(strings.TrimSpace(query.Get("scope")))
	rawSymbols := strings.TrimSpace(strings.Join(query["symbols"], ","))
	if scope != "" && scope != "all" {
		return nil, errors.New("scope must be all")
	}
	if scope == "all" && rawSymbols != "" {
		return nil, errors.New("scope=all and symbols cannot be used together")
	}
	if scope == "all" {
		definitions := privatevaluation.Definitions()
		symbols := make([]string, 0, len(definitions))
		for _, definition := range definitions {
			symbols = append(symbols, definition.Symbol)
		}
		return symbols, nil
	}
	if rawSymbols == "" {
		return nil, errors.New("symbols is required unless scope=all")
	}

	seen := make(map[string]struct{})
	symbols := make([]string, 0)
	for _, raw := range strings.Split(rawSymbols, ",") {
		symbol := strings.ToUpper(strings.TrimSpace(raw))
		if symbol == "" {
			continue
		}
		if !privatevaluation.Supported(symbol) {
			return nil, fmt.Errorf("unsupported symbol: %s", symbol)
		}
		if _, exists := seen[symbol]; exists {
			continue
		}
		seen[symbol] = struct{}{}
		symbols = append(symbols, symbol)
	}
	if len(symbols) == 0 {
		return nil, errors.New("symbols must contain at least one supported symbol")
	}
	return symbols, nil
}

func parsePrivatePremiumInterval(r *http.Request) (time.Duration, error) {
	raw := strings.TrimSpace(r.URL.Query().Get("interval_ms"))
	if raw == "" {
		return privatePremiumDefaultInterval, nil
	}
	milliseconds, err := strconv.Atoi(raw)
	if err != nil {
		return 0, errors.New("interval_ms must be an integer between 5000 and 60000")
	}
	interval := time.Duration(milliseconds) * time.Millisecond
	if interval < privatePremiumMinInterval || interval > privatePremiumMaxInterval {
		return 0, errors.New("interval_ms must be between 5000 and 60000")
	}
	return interval, nil
}

func (s *Server) privatePremiumItems(symbols []string) []privatePremiumItem {
	items := make([]privatePremiumItem, 0, len(symbols))
	for _, symbol := range symbols {
		snapshot, ok := s.privateValuation.Fund(symbol)
		if !ok {
			items = append(items, privatePremiumItem{Symbol: symbol})
			continue
		}
		items = append(items, buildPrivatePremiumItem(symbol, snapshot))
	}
	return items
}

func buildPrivatePremiumItem(symbol string, snapshot privatevaluation.Snapshot) privatePremiumItem {
	item := privatePremiumItem{
		Symbol:    symbol,
		Timestamp: privatePremiumTimestamp(snapshot),
	}
	orderBook := snapshot.OrderBook
	if symbol == privatevaluation.SZ164824Symbol {
		orderBook = nil
		if snapshot.IndiaValuations != nil && snapshot.IndiaValuations.NiftyBridge != nil {
			orderBook = snapshot.IndiaValuations.NiftyBridge.OrderBook
		}
	}
	for _, row := range orderBook {
		if row.Level < 1 || row.Level > 2 || (row.Side != "ask" && row.Side != "bid") {
			continue
		}
		if !finitePrivatePremium(row.Price) || row.Price <= 0 ||
			!finitePrivatePremium(row.PremiumRateVsBasketBidNAV) ||
			!finitePrivatePremium(row.PremiumRateVsBasketAskNAV) {
			continue
		}
		level := &privatePremiumLevel{
			Price:          row.Price,
			BidPremiumRate: row.PremiumRateVsBasketBidNAV,
			AskPremiumRate: row.PremiumRateVsBasketAskNAV,
		}
		if symbol == privatevaluation.SZ161226Symbol && snapshot.SilverValuation != nil {
			settlementNAV := snapshot.SilverValuation.SettlementNAV
			if finitePrivatePremium(settlementNAV) && settlementNAV > 0 {
				settlementPremium := row.Price/settlementNAV - 1
				if finitePrivatePremium(settlementPremium) {
					level.SettlementPremiumRate = &settlementPremium
				}
			}
		}
		switch {
		case row.Side == "ask" && row.Level == 2:
			item.Quotes.Ask2 = level
		case row.Side == "ask" && row.Level == 1:
			item.Quotes.Ask1 = level
		case row.Side == "bid" && row.Level == 1:
			item.Quotes.Bid1 = level
		case row.Side == "bid" && row.Level == 2:
			item.Quotes.Bid2 = level
		}
	}
	return item
}

func privatePremiumTimestamp(snapshot privatevaluation.Snapshot) *int64 {
	var watermark time.Time
	if snapshot.DomesticQuote != nil && !snapshot.DomesticQuote.FetchedAt.IsZero() {
		watermark = snapshot.DomesticQuote.FetchedAt
	}
	if snapshot.Input != nil && !snapshot.Input.ReceivedAt.IsZero() &&
		(watermark.IsZero() || snapshot.Input.ReceivedAt.Before(watermark)) {
		watermark = snapshot.Input.ReceivedAt
	}
	if watermark.IsZero() {
		return nil
	}
	value := watermark.UnixMilli()
	return &value
}

func finitePrivatePremium(value float64) bool {
	return !math.IsNaN(value) && !math.IsInf(value, 0)
}
