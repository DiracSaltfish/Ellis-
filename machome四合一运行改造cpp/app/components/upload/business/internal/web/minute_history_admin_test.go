package web

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"newnavnav/internal/snapshot"
)

func TestDeleteMinuteHistoryDayRequiresDebugAuth(t *testing.T) {
	service := snapshot.NewService(snapshot.ServiceOptions{MinuteHistoryDir: t.TempDir()})
	server := NewServer(service, "", "")
	req := httptest.NewRequest(http.MethodPost, "/api/v1/navsettings/minute-history/delete-day", strings.NewReader(`{"date":"2026-06-19"}`))
	req.Header.Set("Content-Type", "application/json")
	res := httptest.NewRecorder()
	server.ServeHTTP(res, req)
	if res.Code != http.StatusUnauthorized || !strings.Contains(res.Body.String(), "debug_login_required") {
		t.Fatalf("status=%d body=%s, want debug auth failure", res.Code, res.Body.String())
	}
}

func TestDeleteMinuteHistoryDayRemovesRequestedDate(t *testing.T) {
	root := t.TempDir()
	dayPath := filepath.Join(root, "20260619")
	if err := os.MkdirAll(dayPath, 0o755); err != nil {
		t.Fatal(err)
	}
	csvBody := "symbol,minute,timestamp,market_price,estimated_nav,premium_pct,official_est,fair_est,realtime_est,effective_ratio,quote_source,quote_status,model_version,reference_symbol,upload_source\n" +
		"SZ164824,2026-06-19 12:35,2026-06-19T12:35:05+08:00,1.6730,1.6250,2.98,1.62,1.62,1.6250,0.9725,sina,realtime,test,GC,holiday\n"
	if err := os.WriteFile(filepath.Join(dayPath, "SZ164824.csv"), []byte(csvBody), 0o644); err != nil {
		t.Fatal(err)
	}
	service := snapshot.NewService(snapshot.ServiceOptions{MinuteHistoryDir: root})
	server := NewServer(service, "", "")
	req := httptest.NewRequest(http.MethodPost, "/api/v1/navsettings/minute-history/delete-day", strings.NewReader(`{"date":"2026-06-19"}`))
	req.Header.Set("Content-Type", "application/json")
	req.AddCookie(loginDebugSession(t, server))
	res := httptest.NewRecorder()
	server.ServeHTTP(res, req)
	if res.Code != http.StatusOK {
		t.Fatalf("status = %d; body=%s", res.Code, res.Body.String())
	}
	var payload struct {
		OK          bool   `json:"ok"`
		Day         string `json:"day"`
		Deleted     bool   `json:"deleted"`
		SymbolCount int    `json:"symbol_count"`
		RowCount    int    `json:"row_count"`
		Message     string `json:"message"`
	}
	if err := json.Unmarshal(res.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if !payload.OK || !payload.Deleted || payload.Day != "20260619" || payload.SymbolCount != 1 || payload.RowCount != 1 || !strings.Contains(payload.Message, "2026-06-19") {
		t.Fatalf("payload = %+v", payload)
	}
	if _, err := os.Stat(dayPath); !os.IsNotExist(err) {
		t.Fatalf("expected day dir removed, got err=%v", err)
	}
}
