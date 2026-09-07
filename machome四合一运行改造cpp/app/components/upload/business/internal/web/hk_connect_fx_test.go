package web

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"newnavnav/internal/hkconnectfx"
)

func TestHKConnectFXHistoryValidatesDate(t *testing.T) {
	service := hkconnectfx.NewService(hkconnectfx.Options{DataDir: t.TempDir()})
	server := NewServer(nil, t.TempDir(), "secret")
	server.SetHKConnectFXService(service)
	recorder := httptest.NewRecorder()
	server.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/api/v1/private/hk-connect-fx/history?date=not-a-date", nil))
	if recorder.Code != http.StatusBadRequest {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	marketRecorder := httptest.NewRecorder()
	server.ServeHTTP(marketRecorder, httptest.NewRequest(http.MethodGet, "/api/v1/private/hk-connect-fx/history?market=other", nil))
	if marketRecorder.Code != http.StatusBadRequest {
		t.Fatalf("invalid market status=%d body=%s", marketRecorder.Code, marketRecorder.Body.String())
	}
}

func TestHKConnectFXDailyHistoryDefaultsToSevenTradingDays(t *testing.T) {
	service := hkconnectfx.NewService(hkconnectfx.Options{DataDir: t.TempDir()})
	server := NewServer(nil, t.TempDir(), "secret")
	server.SetHKConnectFXService(service)

	recorder := httptest.NewRecorder()
	server.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/api/v1/private/hk-connect-fx/daily-history", nil))
	if recorder.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	var response hkconnectfx.DailyHistoryResponse
	if err := json.Unmarshal(recorder.Body.Bytes(), &response); err != nil {
		t.Fatal(err)
	}
	if response.RequestedDays != 7 || response.Rows == nil {
		t.Fatalf("unexpected response: %+v", response)
	}
	if response.Market != "shanghai" {
		t.Fatalf("default market=%q", response.Market)
	}

	shenzhen := httptest.NewRecorder()
	server.ServeHTTP(shenzhen, httptest.NewRequest(http.MethodGet, "/api/v1/private/hk-connect-fx/daily-history?market=shenzhen", nil))
	if shenzhen.Code != http.StatusOK {
		t.Fatalf("Shenzhen status=%d body=%s", shenzhen.Code, shenzhen.Body.String())
	}
	var shenzhenResponse hkconnectfx.DailyHistoryResponse
	if err := json.Unmarshal(shenzhen.Body.Bytes(), &shenzhenResponse); err != nil {
		t.Fatal(err)
	}
	if shenzhenResponse.Market != "shenzhen" {
		t.Fatalf("Shenzhen market=%q", shenzhenResponse.Market)
	}

	invalid := httptest.NewRecorder()
	server.ServeHTTP(invalid, httptest.NewRequest(http.MethodGet, "/api/v1/private/hk-connect-fx/daily-history?days=bad", nil))
	if invalid.Code != http.StatusBadRequest {
		t.Fatalf("invalid status=%d body=%s", invalid.Code, invalid.Body.String())
	}
	invalidMarket := httptest.NewRecorder()
	server.ServeHTTP(invalidMarket, httptest.NewRequest(http.MethodGet, "/api/v1/private/hk-connect-fx/daily-history?market=other", nil))
	if invalidMarket.Code != http.StatusBadRequest {
		t.Fatalf("invalid market status=%d body=%s", invalidMarket.Code, invalidMarket.Body.String())
	}
}

func TestHKConnectFXCloseHistoryValidatesMarketAndDefaults(t *testing.T) {
	service := hkconnectfx.NewService(hkconnectfx.Options{DataDir: t.TempDir()})
	server := NewServer(nil, t.TempDir(), "secret")
	server.SetHKConnectFXService(service)

	recorder := httptest.NewRecorder()
	server.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/api/v1/private/hk-connect-fx/close-history", nil))
	if recorder.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	var response hkconnectfx.CloseAuditHistoryResponse
	if err := json.Unmarshal(recorder.Body.Bytes(), &response); err != nil {
		t.Fatal(err)
	}
	if response.Market != "shanghai" || response.RequestedDays != 60 || response.Rows == nil {
		t.Fatalf("unexpected response: %+v", response)
	}

	invalid := httptest.NewRecorder()
	server.ServeHTTP(invalid, httptest.NewRequest(http.MethodGet, "/api/v1/private/hk-connect-fx/close-history?market=other", nil))
	if invalid.Code != http.StatusBadRequest {
		t.Fatalf("invalid market status=%d body=%s", invalid.Code, invalid.Body.String())
	}
}

func TestHKConnectFXDedicatedIBRoutesAreRemoved(t *testing.T) {
	server := NewServer(nil, t.TempDir(), "secret")
	for _, path := range []string{
		"/api/v1/private/hk-connect-fx/ib",
		"/api/v1/private/hk-connect-fx/ib-backfill-tasks",
		"/api/v1/private/hk-connect-fx/ib-anchors",
	} {
		recorder := httptest.NewRecorder()
		server.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, path, nil))
		if recorder.Code != http.StatusNotFound {
			t.Fatalf("%s status=%d body=%s", path, recorder.Code, recorder.Body.String())
		}
	}
}
