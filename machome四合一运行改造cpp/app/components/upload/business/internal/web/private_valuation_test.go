package web

import (
	"bytes"
	"compress/gzip"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"newnavnav/internal/privatevaluation"
	"newnavnav/internal/snapshot"
)

type fakePrivateValuationService struct {
	input           privatevaluation.Input
	batchInputs     []privatevaluation.Input
	replacedDay     bool
	finalNAVRows    int
	niftyReviewDays int
	silverCloseDays int
}

func (s *fakePrivateValuationService) List() privatevaluation.ListResponse {
	return privatevaluation.ListResponse{
		SchemaVersion: privatevaluation.SchemaVersion,
		AsOf:          time.Date(2026, 7, 10, 10, 0, 0, 0, time.UTC),
		Funds: []privatevaluation.ListItem{{
			Symbol:       privatevaluation.TargetSymbol,
			Name:         privatevaluation.TargetName,
			ModelVersion: privatevaluation.ModelVersion,
		}, {
			Symbol:       privatevaluation.SH513050Symbol,
			Name:         "中概互联ETF",
			ModelVersion: privatevaluation.SH513050ModelVersion,
		}, {
			Symbol:       privatevaluation.SH513000Symbol,
			Name:         "225ETF",
			ModelVersion: privatevaluation.N225MProxyModelVersion,
		}},
	}
}

func (s *fakePrivateValuationService) Fund(symbol string) (privatevaluation.Snapshot, bool) {
	if symbol != privatevaluation.TargetSymbol {
		return privatevaluation.Snapshot{}, false
	}
	return privatevaluation.Snapshot{
		SchemaVersion: privatevaluation.SchemaVersion,
		Symbol:        symbol,
		Name:          privatevaluation.TargetName,
		ModelVersion:  privatevaluation.ModelVersion,
	}, true
}

func (s *fakePrivateValuationService) MinuteHistory(_ context.Context, symbol string, days int) (privatevaluation.MinuteHistoryResponse, error) {
	return privatevaluation.MinuteHistoryResponse{
		Symbol: symbol,
		Days:   days,
		Rows: []privatevaluation.MinuteHistoryPoint{{
			Minute:                   time.Date(2026, 7, 10, 10, 0, 0, 0, time.FixedZone("Asia/Shanghai", 8*60*60)),
			MarketPrice:              1.069,
			BasketBidNAV:             1.0718059923,
			BasketAskNAV:             1.0798377712,
			BuyDirectionPremiumRate:  -0.002618004,
			SellDirectionPremiumRate: -0.010963,
		}},
	}, nil
}

func (s *fakePrivateValuationService) ImportHistoricalMinutes(_ context.Context, symbol string, rows []privatevaluation.HistoricalMinuteInput) (int, error) {
	if symbol != privatevaluation.TargetSymbol {
		return 0, nil
	}
	return len(rows), nil
}

func (s *fakePrivateValuationService) ReplaceHistoricalMinutes(_ context.Context, symbol string, rows []privatevaluation.HistoricalMinuteInput) (int, error) {
	s.replacedDay = true
	if symbol != privatevaluation.TargetSymbol {
		return 0, nil
	}
	return len(rows), nil
}
func (s *fakePrivateValuationService) MinuteHistoryDate(ctx context.Context, symbol, _ string) (privatevaluation.MinuteHistoryResponse, error) {
	return s.MinuteHistory(ctx, symbol, 1)
}

func (s *fakePrivateValuationService) MinuteHistoryDates(context.Context, string, int) ([]string, error) {
	return []string{"20260710", "20260527"}, nil
}

func (s *fakePrivateValuationService) UpdateInput(_ context.Context, input privatevaluation.Input) (privatevaluation.Snapshot, error) {
	s.input = input
	return privatevaluation.Snapshot{SchemaVersion: privatevaluation.SchemaVersion, Symbol: privatevaluation.TargetSymbol}, nil
}

func (s *fakePrivateValuationService) UpdateInputs(_ context.Context, inputs []privatevaluation.Input) (privatevaluation.BatchUpdateResult, error) {
	s.batchInputs = append([]privatevaluation.Input(nil), inputs...)
	accepted := make([]string, 0, len(inputs))
	for _, input := range inputs {
		accepted = append(accepted, input.Symbol)
	}
	return privatevaluation.BatchUpdateResult{Accepted: accepted, Rejected: map[string]string{}}, nil
}

func (s *fakePrivateValuationService) IndiaHistoryReview(_ context.Context, symbol string, _ int) (privatevaluation.IndiaHistoryReviewResponse, error) {
	return privatevaluation.IndiaHistoryReviewResponse{
		Symbol: symbol,
		Name:   privatevaluation.SZ164824Name,
		Rows:   []privatevaluation.IndiaHistoryReviewRow{{TargetDate: "2026-07-31", Status: "ok"}},
	}, nil
}

func (s *fakePrivateValuationService) SilverCloseHistory(_ context.Context, symbol string, days int) (privatevaluation.SilverCloseHistoryResponse, error) {
	s.silverCloseDays = days
	return privatevaluation.SilverCloseHistoryResponse{
		Symbol: symbol,
		Name:   privatevaluation.SZ161226Name,
		Rows: []privatevaluation.SilverCloseHistoryRow{{
			TradingDay:            "2026-08-20",
			CloseMinute:           time.Date(2026, 8, 20, 15, 0, 0, 0, time.FixedZone("Asia/Shanghai", 8*60*60)),
			SettlementNAV:         1.8204,
			SettlementPremiumRate: 0.0459,
			TradingNAV:            1.8487,
			TradingPremiumRate:    0.0299,
		}},
	}, nil
}

func (s *fakePrivateValuationService) IndiaNiftyBridgeHistoryReview(_ context.Context, symbol string, days int) (privatevaluation.IndiaNiftyBridgeReviewResponse, error) {
	s.niftyReviewDays = days
	return privatevaluation.IndiaNiftyBridgeReviewResponse{
		Symbol: symbol,
		Name:   privatevaluation.SZ164824Name,
		Rows: []privatevaluation.IndiaNiftyBridgeReviewRow{{
			TradingDay: "2026-07-31",
			Checkpoint: "14:55",
			State:      "ordinary",
		}},
	}, nil
}

func (s *fakePrivateValuationService) ImportIndiaFinalNAVHistory(_ context.Context, symbol string, rows []privatevaluation.IndiaFinalNAVHistoryInput) (int, error) {
	if symbol != privatevaluation.SZ164824Symbol {
		return 0, nil
	}
	s.finalNAVRows = len(rows)
	return len(rows), nil
}

func TestPrivateGETRemainsUnlockedWhenPublicDataViewIsLocked(t *testing.T) {
	server := NewServer(snapshot.NewService(snapshot.ServiceOptions{}), "", "secret")
	server.SetDataViewToken("locked-public")
	server.SetPrivateValuationService(&fakePrivateValuationService{})

	publicRes := httptest.NewRecorder()
	server.ServeHTTP(publicRes, httptest.NewRequest(http.MethodGet, "/api/v1/funds/SZ159518", nil))
	if publicRes.Code != http.StatusServiceUnavailable {
		t.Fatalf("public status = %d, want locked 503", publicRes.Code)
	}

	privateRes := httptest.NewRecorder()
	server.ServeHTTP(privateRes, httptest.NewRequest(http.MethodGet, "/api/v1/private/funds", nil))
	if privateRes.Code != http.StatusOK {
		t.Fatalf("private status = %d, want 200; body=%s", privateRes.Code, privateRes.Body.String())
	}
	if got := privateRes.Header().Get("Cache-Control"); got != "private, no-store" {
		t.Fatalf("Cache-Control = %q", got)
	}
	if !strings.Contains(privateRes.Header().Get("X-Robots-Tag"), "noindex") {
		t.Fatalf("X-Robots-Tag = %q", privateRes.Header().Get("X-Robots-Tag"))
	}
}

func TestPrivateInputUploadRequiresToken(t *testing.T) {
	server := NewServer(snapshot.NewService(snapshot.ServiceOptions{}), "", "secret")
	server.SetPrivateValuationService(&fakePrivateValuationService{})
	req := httptest.NewRequest(http.MethodPost, "/api/v1/private/inputs/SZ159518", strings.NewReader(`{}`))
	res := httptest.NewRecorder()
	server.ServeHTTP(res, req)
	if res.Code != http.StatusUnauthorized {
		t.Fatalf("status = %d, want 401", res.Code)
	}
}

func TestPrivateMinuteHistoryReplaceAcceptsOneGzipDay(t *testing.T) {
	service := &fakePrivateValuationService{}
	server := NewServer(snapshot.NewService(snapshot.ServiceOptions{}), "", "secret")
	server.SetPrivateValuationService(service)
	var body bytes.Buffer
	writer := gzip.NewWriter(&body)
	if err := json.NewEncoder(writer).Encode(map[string]any{
		"replace_day": true,
		"rows":        []map[string]any{{}},
	}); err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	req := httptest.NewRequest(
		http.MethodPost,
		"/api/v1/private/funds/SZ159518/minute-history/import",
		&body,
	)
	req.Header.Set("X-Upload-Token", "secret")
	req.Header.Set("Content-Encoding", "gzip")
	res := httptest.NewRecorder()
	server.ServeHTTP(res, req)
	if res.Code != http.StatusOK || !service.replacedDay {
		t.Fatalf("status=%d replaced=%v body=%s", res.Code, service.replacedDay, res.Body.String())
	}
}

func TestPrivateInputUploadUsesIsolatedEndpoint(t *testing.T) {
	service := &fakePrivateValuationService{}
	server := NewServer(snapshot.NewService(snapshot.ServiceOptions{}), "", "secret")
	server.SetPrivateValuationService(service)
	payload := map[string]any{
		"schema_version": 1,
		"symbol":         "SZ159518",
		"model_version":  privatevaluation.ModelVersion,
		"pcf": map[string]any{
			"security_id": "159518", "trading_day": "2026-07-10", "redemption": "Y",
			"creation_redemption_unit": 1000000, "estimate_cash_component_cny": 1284.61,
		},
		"fx": map[string]any{
			"pair": "USD/CNY", "rate": 6.7765, "trading_day": "2026-07-10", "quote_time": "18:00",
			"source": "CFETS_REFERENCE_RATE", "fetched_at": "2026-07-10T18:46:00+08:00",
		},
		"ib": map[string]any{
			"symbol": "XOP", "bid": 158.61, "ask": 159.80, "last": 159.40,
			"market_data_type": "Live", "source": "IBKR_TWS", "observed_at": "2026-07-10T18:46:00+08:00",
		},
		"source": "mac-home-private-uploader", "generated_at": "2026-07-10T18:46:00+08:00",
	}
	body, _ := json.Marshal(payload)
	req := httptest.NewRequest(http.MethodPost, "/api/v1/private/inputs/SZ159518", strings.NewReader(string(body)))
	req.Header.Set("X-Upload-Token", "secret")
	res := httptest.NewRecorder()
	server.ServeHTTP(res, req)
	if res.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body=%s", res.Code, res.Body.String())
	}
	if service.input.Symbol != privatevaluation.TargetSymbol {
		t.Fatalf("uploaded symbol = %q", service.input.Symbol)
	}
	var response map[string]any
	if err := json.Unmarshal(res.Body.Bytes(), &response); err != nil {
		t.Fatal(err)
	}
	if response["ok"] != true || response["symbol"] != privatevaluation.TargetSymbol {
		t.Fatalf("upload response = %#v", response)
	}
	if _, exists := response["snapshot"]; exists {
		t.Fatalf("lightweight acknowledgement unexpectedly contains snapshot: %#v", response)
	}
}

func TestPrivateInputUploadAcceptsGzip(t *testing.T) {
	service := &fakePrivateValuationService{}
	server := NewServer(snapshot.NewService(snapshot.ServiceOptions{}), "", "secret")
	server.SetPrivateValuationService(service)
	payload := map[string]any{
		"schema_version": 1,
		"symbol":         privatevaluation.TargetSymbol,
		"model_version":  privatevaluation.ModelVersion,
		"pcf": map[string]any{
			"security_id": "159518", "trading_day": "2026-07-10", "redemption": "Y",
			"creation_redemption_unit": 1000000, "estimate_cash_component_cny": 1284.61,
		},
		"fx": map[string]any{
			"pair": "USD/CNY", "rate": 6.7765, "trading_day": "2026-07-10", "quote_time": "18:00",
			"source": "CFETS_REFERENCE_RATE", "fetched_at": "2026-07-10T18:46:00+08:00",
		},
		"ib": map[string]any{
			"symbol": "XOP", "bid": 158.61, "ask": 159.80, "last": 159.40,
			"market_data_type": "Live", "source": "IBKR_TWS", "observed_at": "2026-07-10T18:46:00+08:00",
		},
		"source": "mac-home-private-uploader", "generated_at": "2026-07-10T18:46:00+08:00",
	}
	var body bytes.Buffer
	writer := gzip.NewWriter(&body)
	if err := json.NewEncoder(writer).Encode(payload); err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	req := httptest.NewRequest(http.MethodPost, "/api/v1/private/inputs/SZ159518", &body)
	req.Header.Set("X-Upload-Token", "secret")
	req.Header.Set("Content-Encoding", "gzip")
	res := httptest.NewRecorder()
	server.ServeHTTP(res, req)
	if res.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body=%s", res.Code, res.Body.String())
	}
	if service.input.Symbol != privatevaluation.TargetSymbol {
		t.Fatalf("uploaded symbol = %q", service.input.Symbol)
	}
}

func TestPrivateInputBatchAcceptsGzipAndUsesExactRoute(t *testing.T) {
	service := &fakePrivateValuationService{}
	server := NewServer(snapshot.NewService(snapshot.ServiceOptions{}), "", "secret")
	server.SetPrivateValuationService(service)
	generatedAt := "2026-07-10T10:00:00+08:00"
	payload := map[string]any{
		"schema_version": 1,
		"batch_id":       "xop-family-20260710T100000",
		"source":         "test-xop-family",
		"generated_at":   generatedAt,
		"inputs": []map[string]any{{
			"schema_version": 1, "symbol": privatevaluation.TargetSymbol,
			"model_version": privatevaluation.ModelVersion,
			"generated_at":  generatedAt, "source": "test",
		}},
	}
	var body bytes.Buffer
	writer := gzip.NewWriter(&body)
	if err := json.NewEncoder(writer).Encode(payload); err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	req := httptest.NewRequest(http.MethodPost, "/api/v1/private/inputs/batch", &body)
	req.Header.Set("X-Upload-Token", "secret")
	req.Header.Set("Content-Encoding", "gzip")
	res := httptest.NewRecorder()
	server.ServeHTTP(res, req)
	if res.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body=%s", res.Code, res.Body.String())
	}
	if len(service.batchInputs) != 1 || service.batchInputs[0].Symbol != privatevaluation.TargetSymbol {
		t.Fatalf("batch inputs = %+v", service.batchInputs)
	}
	var response struct {
		OK       bool              `json:"ok"`
		BatchID  string            `json:"batch_id"`
		Accepted []string          `json:"accepted"`
		Rejected map[string]string `json:"rejected"`
	}
	if err := json.Unmarshal(res.Body.Bytes(), &response); err != nil {
		t.Fatal(err)
	}
	if !response.OK || response.BatchID != payload["batch_id"] || len(response.Accepted) != 1 || len(response.Rejected) != 0 {
		t.Fatalf("batch response = %+v", response)
	}
}

func TestPrivateInputBatchRejectsMissingMetadataBeforeService(t *testing.T) {
	service := &fakePrivateValuationService{}
	server := NewServer(snapshot.NewService(snapshot.ServiceOptions{}), "", "secret")
	server.SetPrivateValuationService(service)
	req := httptest.NewRequest(http.MethodPost, "/api/v1/private/inputs/batch", strings.NewReader(`{"inputs":[]}`))
	req.Header.Set("X-Upload-Token", "secret")
	res := httptest.NewRecorder()
	server.ServeHTTP(res, req)
	if res.Code != http.StatusBadRequest || len(service.batchInputs) != 0 {
		t.Fatalf("status=%d inputs=%+v body=%s", res.Code, service.batchInputs, res.Body.String())
	}
}

func TestPrivateSZ162411UploaderContractPassesStrictGzipDecoder(t *testing.T) {
	service := &fakePrivateValuationService{}
	server := NewServer(snapshot.NewService(snapshot.ServiceOptions{}), "", "secret")
	server.SetPrivateValuationService(service)
	payload := map[string]any{
		"schema_version": 1,
		"symbol":         privatevaluation.SZ162411Symbol,
		"model_version":  privatevaluation.SZ162411ModelVersion,
		"valuation_kind": privatevaluation.CalculationModeLOFWeightedAnchor,
		"lof": map[string]any{
			"base_nav":            0.8804,
			"base_nav_date":       "2026-08-07",
			"base_nav_source":     "eastmoney",
			"base_nav_fetched_at": "2026-08-11T13:10:00+08:00",
			"base_reference": map[string]any{
				"symbol": "XOP", "price": 166.41, "price_basis": "regular_session_close",
				"target_at": "2026-08-07T16:00:00-04:00", "observed_at": "2026-08-07T16:00:00-04:00",
				"source": "sina_us", "capture_status": "public_anchor_record",
			},
			"base_fx": map[string]any{
				"pair": "USD/CNY", "rate": 6.7904, "trading_day": "2026-08-07", "quote_time": "09:15",
				"source": "SAFE_CENTRAL_PARITY", "fetched_at": "2026-08-11T13:10:00+08:00",
			},
			"current_fx": map[string]any{
				"pair": "USD/CNY", "rate": 6.79, "trading_day": "2026-08-11", "quote_time": "09:15",
				"source": "SAFE_CENTRAL_PARITY", "fetched_at": "2026-08-11T13:10:00+08:00",
			},
			"effective_ratio": map[string]any{
				"value": 0.955, "source": "weighted_anchor", "default_value": 0.955, "default_source": "weighted_anchor",
			},
		},
		"ib": map[string]any{
			"symbol": "XOP", "contract": "XOP STK SMART/ARCA conId=413951498",
			"bid": 174.19, "ask": 174.64, "last": 174.58, "market_data_type": "Live",
			"quote_session": "us_overnight_live", "source": "IBKR_TWS", "observed_at": "2026-08-11T13:10:00+08:00",
		},
		"source": "mac-home-private-162411-lof-uploader", "generated_at": "2026-08-11T13:10:00+08:00",
	}
	var body bytes.Buffer
	writer := gzip.NewWriter(&body)
	if err := json.NewEncoder(writer).Encode(payload); err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	req := httptest.NewRequest(http.MethodPost, "/api/v1/private/inputs/SZ162411", &body)
	req.Header.Set("X-Upload-Token", "secret")
	req.Header.Set("Content-Encoding", "gzip")
	res := httptest.NewRecorder()
	server.ServeHTTP(res, req)
	if res.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body=%s", res.Code, res.Body.String())
	}
	if service.input.Symbol != privatevaluation.SZ162411Symbol || service.input.ValuationKind != privatevaluation.CalculationModeLOFWeightedAnchor || service.input.LOF == nil {
		t.Fatalf("decoded uploader contract = %+v", service.input)
	}
	if service.input.IB.QuoteSession != "us_overnight_live" || service.input.LOF.BaseReference.PriceBasis != privatevaluation.LOFReferencePriceRegularClose {
		t.Fatalf("decoded audit fields = %+v", service.input)
	}
}

func TestPrivateFrontendUsesSeparateEntryAndNoIndexHeaders(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "private.html"), []byte("private-entry"), 0o644); err != nil {
		t.Fatal(err)
	}
	server := NewServer(snapshot.NewService(snapshot.ServiceOptions{}), dir, "")
	res := httptest.NewRecorder()
	server.ServeHTTP(res, httptest.NewRequest(http.MethodGet, "/private/SZ159518", nil))
	if res.Code != http.StatusOK || !strings.Contains(res.Body.String(), "private-entry") {
		t.Fatalf("status/body = %d %q", res.Code, res.Body.String())
	}
	if !strings.Contains(res.Header().Get("X-Robots-Tag"), "noindex") {
		t.Fatalf("X-Robots-Tag = %q", res.Header().Get("X-Robots-Tag"))
	}
}

func TestPrivateMinuteHistoryUsesPrivateNamespaceAndRemainsUnlocked(t *testing.T) {
	server := NewServer(snapshot.NewService(snapshot.ServiceOptions{}), "", "secret")
	server.SetDataViewToken("locked-public")
	server.SetPrivateValuationService(&fakePrivateValuationService{})
	res := httptest.NewRecorder()
	server.ServeHTTP(res, httptest.NewRequest(http.MethodGet, "/api/v1/private/funds/SZ159518/minute-history?days=3", nil))
	if res.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body=%s", res.Code, res.Body.String())
	}
	if got := res.Header().Get("Cache-Control"); got != "private, no-store" {
		t.Fatalf("Cache-Control = %q", got)
	}
	var body privatevaluation.MinuteHistoryResponse
	if err := json.Unmarshal(res.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if body.Symbol != privatevaluation.TargetSymbol || body.Days != 3 || len(body.Rows) != 1 {
		t.Fatalf("history response = %+v", body)
	}
}

func TestPrivateIndiaHistoryReviewUsesDedicatedRoute(t *testing.T) {
	server := NewServer(snapshot.NewService(snapshot.ServiceOptions{}), "", "secret")
	server.SetPrivateValuationService(&fakePrivateValuationService{})
	res := httptest.NewRecorder()
	server.ServeHTTP(res, httptest.NewRequest(http.MethodGet, "/api/v1/private/funds/SZ164824/india-history-review?days=32", nil))
	if res.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body=%s", res.Code, res.Body.String())
	}
	var body privatevaluation.IndiaHistoryReviewResponse
	if err := json.Unmarshal(res.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if body.Symbol != privatevaluation.SZ164824Symbol || len(body.Rows) != 1 {
		t.Fatalf("review response = %+v", body)
	}
}

func TestPrivateSilverCloseHistoryUsesDedicatedRoute(t *testing.T) {
	service := &fakePrivateValuationService{}
	server := NewServer(snapshot.NewService(snapshot.ServiceOptions{}), "", "secret")
	server.SetPrivateValuationService(service)
	res := httptest.NewRecorder()
	server.ServeHTTP(res, httptest.NewRequest(http.MethodGet, "/api/v1/private/funds/SZ161226/close-history?days=180", nil))
	if res.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body=%s", res.Code, res.Body.String())
	}
	if service.silverCloseDays != 180 {
		t.Fatalf("days = %d, want 180", service.silverCloseDays)
	}
	var body privatevaluation.SilverCloseHistoryResponse
	if err := json.Unmarshal(res.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if body.Symbol != privatevaluation.SZ161226Symbol || len(body.Rows) != 1 {
		t.Fatalf("close history response = %+v", body)
	}
}

func TestPrivateIndiaNiftyBridgeHistoryReviewUsesDedicatedRoute(t *testing.T) {
	service := &fakePrivateValuationService{}
	server := NewServer(snapshot.NewService(snapshot.ServiceOptions{}), "", "secret")
	server.SetPrivateValuationService(service)
	res := httptest.NewRecorder()
	server.ServeHTTP(res, httptest.NewRequest(http.MethodGet, "/api/v1/private/funds/SZ164824/nifty-bridge-history-review?days=32", nil))
	if res.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body=%s", res.Code, res.Body.String())
	}
	if got := res.Header().Get("Cache-Control"); got != "private, no-store" {
		t.Fatalf("Cache-Control = %q", got)
	}
	if service.niftyReviewDays != 32 {
		t.Fatalf("days = %d, want 32", service.niftyReviewDays)
	}
	var body privatevaluation.IndiaNiftyBridgeReviewResponse
	if err := json.Unmarshal(res.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if body.Symbol != privatevaluation.SZ164824Symbol || len(body.Rows) != 1 {
		t.Fatalf("review response = %+v", body)
	}
}

func TestPrivateIndiaNiftyBridgeHistoryReviewValidatesDays(t *testing.T) {
	service := &fakePrivateValuationService{}
	server := NewServer(snapshot.NewService(snapshot.ServiceOptions{}), "", "secret")
	server.SetPrivateValuationService(service)

	defaultRes := httptest.NewRecorder()
	server.ServeHTTP(defaultRes, httptest.NewRequest(http.MethodGet, "/api/v1/private/funds/SZ164824/nifty-bridge-history-review", nil))
	if defaultRes.Code != http.StatusOK || service.niftyReviewDays != 120 {
		t.Fatalf("default status=%d days=%d, want 200/120; body=%s", defaultRes.Code, service.niftyReviewDays, defaultRes.Body.String())
	}

	for _, raw := range []string{"0", "366", "bad"} {
		t.Run(raw, func(t *testing.T) {
			res := httptest.NewRecorder()
			path := "/api/v1/private/funds/SZ164824/nifty-bridge-history-review?days=" + raw
			server.ServeHTTP(res, httptest.NewRequest(http.MethodGet, path, nil))
			if res.Code != http.StatusBadRequest {
				t.Fatalf("status = %d, want 400; body=%s", res.Code, res.Body.String())
			}
		})
	}

	otherSymbol := httptest.NewRecorder()
	server.ServeHTTP(otherSymbol, httptest.NewRequest(http.MethodGet, "/api/v1/private/funds/SZ159518/nifty-bridge-history-review", nil))
	if otherSymbol.Code != http.StatusNotFound {
		t.Fatalf("other symbol status = %d, want 404; body=%s", otherSymbol.Code, otherSymbol.Body.String())
	}
}

func TestPrivateIndiaFinalNAVHistoryImportRequiresTokenAndUsesDedicatedRoute(t *testing.T) {
	service := &fakePrivateValuationService{}
	server := NewServer(snapshot.NewService(snapshot.ServiceOptions{}), "", "secret")
	server.SetPrivateValuationService(service)
	payload := `{"rows":[{"target_date":"2026-08-03","base_nav_date":"2026-07-30","base_nav":1.3146,"investment_ratio":0.8937,"static_ratio":0.1063,"base_anchor_price":52.0,"target_anchor_price":53.0,"base_fx":6.78,"target_fx":6.79,"source":"test","generated_at":"2026-08-05T09:00:00+08:00"}]}`
	unauthorized := httptest.NewRecorder()
	server.ServeHTTP(unauthorized, httptest.NewRequest(http.MethodPost, "/api/v1/private/funds/SZ164824/final-nav-history/import", strings.NewReader(payload)))
	if unauthorized.Code != http.StatusUnauthorized {
		t.Fatalf("unauthorized status = %d, want 401", unauthorized.Code)
	}
	req := httptest.NewRequest(http.MethodPost, "/api/v1/private/funds/SZ164824/final-nav-history/import", strings.NewReader(payload))
	req.Header.Set("X-Upload-Token", "secret")
	res := httptest.NewRecorder()
	server.ServeHTTP(res, req)
	if res.Code != http.StatusOK || service.finalNAVRows != 1 {
		t.Fatalf("status=%d rows=%d body=%s", res.Code, service.finalNAVRows, res.Body.String())
	}
}

func TestPrivateMinuteHistorySupportsHistoricalDateSelection(t *testing.T) {
	server := NewServer(snapshot.NewService(snapshot.ServiceOptions{}), "", "secret")
	server.SetPrivateValuationService(&fakePrivateValuationService{})

	datesRes := httptest.NewRecorder()
	server.ServeHTTP(datesRes, httptest.NewRequest(http.MethodGet, "/api/v1/private/funds/SZ159518/minute-history/dates", nil))
	if datesRes.Code != http.StatusOK {
		t.Fatalf("dates status = %d, want 200; body=%s", datesRes.Code, datesRes.Body.String())
	}
	var datesBody struct {
		Symbol string   `json:"symbol"`
		Dates  []string `json:"dates"`
	}
	if err := json.Unmarshal(datesRes.Body.Bytes(), &datesBody); err != nil {
		t.Fatal(err)
	}
	if datesBody.Symbol != privatevaluation.TargetSymbol || len(datesBody.Dates) != 2 || datesBody.Dates[1] != "20260527" {
		t.Fatalf("date response = %+v", datesBody)
	}

	historyRes := httptest.NewRecorder()
	server.ServeHTTP(historyRes, httptest.NewRequest(http.MethodGet, "/api/v1/private/funds/SZ159518/minute-history?date=20260527", nil))
	if historyRes.Code != http.StatusOK {
		t.Fatalf("historical status = %d, want 200; body=%s", historyRes.Code, historyRes.Body.String())
	}
	var history privatevaluation.MinuteHistoryResponse
	if err := json.Unmarshal(historyRes.Body.Bytes(), &history); err != nil {
		t.Fatal(err)
	}
	if history.Days != 1 || len(history.Rows) != 1 {
		t.Fatalf("historical response = %+v", history)
	}
}

func TestNormalizeAPITargetCoversPrivateNamespace(t *testing.T) {
	cases := map[string]string{
		"/api/v1/private/funds":                         "/api/v1/private/funds",
		"/api/v1/private/funds/SZ159518":                "/api/v1/private/funds/:symbol",
		"/api/v1/private/funds/SZ159518/minute-history": "/api/v1/private/funds/:symbol",
		"/api/v1/private/inputs/SZ159518":               "",
	}
	for path, want := range cases {
		if got := normalizeAPITarget(path); got != want {
			t.Fatalf("normalizeAPITarget(%q) = %q, want %q", path, got, want)
		}
	}
}
