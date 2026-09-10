package web

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"newnavnav/internal/snapshot"
)

func TestIntradayRebuildJobsRequireDebugSession(t *testing.T) {
	server := NewServer(snapshot.NewService(snapshot.ServiceOptions{}), "", "")

	locked := httptest.NewRecorder()
	server.ServeHTTP(locked, httptest.NewRequest(http.MethodGet, "/api/v1/debug/private-intraday-rebuild/jobs", nil))
	if locked.Code != http.StatusUnauthorized {
		t.Fatalf("locked status = %d, want %d; body=%s", locked.Code, http.StatusUnauthorized, locked.Body.String())
	}

	req := httptest.NewRequest(http.MethodGet, "/api/v1/debug/private-intraday-rebuild/jobs", nil)
	req.AddCookie(loginDebugSession(t, server))
	res := httptest.NewRecorder()
	server.ServeHTTP(res, req)
	if res.Code != http.StatusOK {
		t.Fatalf("authorized status = %d, want %d; body=%s", res.Code, http.StatusOK, res.Body.String())
	}
	var payload intradayRebuildStatusResponse
	if err := json.NewDecoder(res.Body).Decode(&payload); err != nil {
		t.Fatal(err)
	}
	if payload.Agent.Connected {
		t.Fatal("agent unexpectedly connected")
	}
}

func TestIntradayRebuildWebSocketUsesDedicatedToken(t *testing.T) {
	server := NewServer(snapshot.NewService(snapshot.ServiceOptions{}), "", "upload-token")
	server.SetIntradayRebuildAgentToken("agent-token")

	if server.authorizeIntradayRebuildAgent(httptest.NewRequest(http.MethodGet, "/api/v1/private/intraday-rebuild/ws", nil)) {
		t.Fatal("missing token was accepted")
	}
	req := httptest.NewRequest(http.MethodGet, "/api/v1/private/intraday-rebuild/ws", nil)
	req.Header.Set("X-Intraday-Rebuild-Token", "agent-token")
	if !server.authorizeIntradayRebuildAgent(req) {
		t.Fatal("dedicated token was not accepted")
	}
	uploadReq := httptest.NewRequest(http.MethodGet, "/api/v1/private/intraday-rebuild/ws?token=upload-token", nil)
	uploadReq.Header.Set("X-Upload-Token", "upload-token")
	if server.authorizeIntradayRebuildAgent(uploadReq) {
		t.Fatal("generic upload token was accepted for rebuild websocket")
	}
}

func TestIntradayRebuildJobCreatesAndPersists(t *testing.T) {
	server := NewServer(snapshot.NewService(snapshot.ServiceOptions{}), "", "")
	storePath := t.TempDir() + "/intraday-rebuild-jobs.json"
	if err := server.SetIntradayRebuildStorePath(storePath); err != nil {
		t.Fatal(err)
	}

	location, err := time.LoadLocation("Asia/Shanghai")
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now().In(location)
	if now.Weekday() == time.Saturday || now.Weekday() == time.Sunday {
		t.Skip("intraday jobs intentionally only allow a current Shanghai trading weekday")
	}
	body := strings.NewReader(`{"trade_date":"` + now.Format("2006-01-02") + `","symbols":["SZ159518"],"fx_policy":"final_cfets"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/v1/debug/private-intraday-rebuild/jobs", body)
	req.Header.Set("Content-Type", "application/json")
	req.AddCookie(loginDebugSession(t, server))
	res := httptest.NewRecorder()
	server.ServeHTTP(res, req)
	if res.Code != http.StatusAccepted {
		t.Fatalf("create status = %d, want %d; body=%s", res.Code, http.StatusAccepted, res.Body.String())
	}
	var job intradayRebuildJob
	if err := json.NewDecoder(res.Body).Decode(&job); err != nil {
		t.Fatal(err)
	}
	if job.State != intradayRebuildStateQueued || job.TradeDate != now.Format("2006-01-02") {
		t.Fatalf("unexpected job: %#v", job)
	}

	reloaded := newIntradayRebuildController()
	if err := reloaded.setStorePath(storePath); err != nil {
		t.Fatal(err)
	}
	if restored, ok := reloaded.job(job.ID); !ok || restored.TradeDate != job.TradeDate {
		t.Fatalf("persisted job not restored: %#v, found=%v", restored, ok)
	}
}
