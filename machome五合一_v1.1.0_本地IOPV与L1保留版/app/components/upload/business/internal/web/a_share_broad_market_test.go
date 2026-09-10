package web

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"newnavnav/internal/domain"
	"newnavnav/internal/sharesync"
	"newnavnav/internal/snapshot"
)

func TestPrivateChinaBroadMarketRoutesExposeResearchOnlyHistory(t *testing.T) {
	store := sharesync.NewCSVStore(t.TempDir())
	if err := store.UpsertShareHistory(context.Background(), []domain.ShareHistoryRecord{{
		Symbol:    "SZ159238",
		Name:      "交易所旧名称",
		FundType:  "ETF",
		ShareDate: "2026-08-14",
		Shares10K: 1234.5,
		Source:    "szse_fund_size",
	}}); err != nil {
		t.Fatal(err)
	}
	server := NewServer(snapshot.NewService(snapshot.ServiceOptions{ShareHistory: store}), "", "")

	listRequest := httptest.NewRequest(http.MethodGet, "/api/v1/private/a-share-broad-market/funds", nil)
	listResponse := httptest.NewRecorder()
	server.ServeHTTP(listResponse, listRequest)
	if listResponse.Code != http.StatusOK {
		t.Fatalf("list status = %d, body=%s", listResponse.Code, listResponse.Body.String())
	}
	var listed struct {
		Funds []domain.ChinaBroadMarketETF `json:"funds"`
	}
	if err := json.Unmarshal(listResponse.Body.Bytes(), &listed); err != nil {
		t.Fatal(err)
	}
	if len(listed.Funds) != 86 {
		t.Fatalf("list funds = %d, want 86", len(listed.Funds))
	}

	historyRequest := httptest.NewRequest(http.MethodGet, "/api/v1/private/a-share-broad-market/funds/SZ159238/share-history?days=60", nil)
	historyResponse := httptest.NewRecorder()
	server.ServeHTTP(historyResponse, historyRequest)
	if historyResponse.Code != http.StatusOK {
		t.Fatalf("history status = %d, body=%s", historyResponse.Code, historyResponse.Body.String())
	}
	var history domain.ShareHistoryResponse
	if err := json.Unmarshal(historyResponse.Body.Bytes(), &history); err != nil {
		t.Fatal(err)
	}
	if history.Name != "沪深300增强ETF景顺" {
		t.Fatalf("history name = %q", history.Name)
	}
	if len(history.Rows) != 1 || history.Rows[0].Symbol != "SZ159238" {
		t.Fatalf("history rows = %#v", history.Rows)
	}

	missingRequest := httptest.NewRequest(http.MethodGet, "/api/v1/private/a-share-broad-market/funds/SZ164701/share-history", nil)
	missingResponse := httptest.NewRecorder()
	server.ServeHTTP(missingResponse, missingRequest)
	if missingResponse.Code != http.StatusNotFound {
		t.Fatalf("unregistered research status = %d, want %d", missingResponse.Code, http.StatusNotFound)
	}
}
