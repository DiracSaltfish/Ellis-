package web

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"newnavnav/internal/domain"
	"newnavnav/internal/snapshot"
)

type fakeManualValuationUpsert struct {
	Symbol    string
	Ratio     float64
	Source    string
	UpdatedBy string
}

type fakeManualValuationRepository struct {
	mu      sync.Mutex
	data    domain.ValuationData
	upserts []fakeManualValuationUpsert
}

func newFakeManualValuationRepository() *fakeManualValuationRepository {
	return &fakeManualValuationRepository{data: domain.EmptyValuationData()}
}

func (r *fakeManualValuationRepository) LoadValuationData(context.Context) (domain.ValuationData, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.data, nil
}

func (r *fakeManualValuationRepository) UpsertDailyPrices(context.Context, []domain.DailyPrice) error {
	return nil
}

func (r *fakeManualValuationRepository) UpsertManualValuationPositionOverride(_ context.Context, symbol string, ratio float64, source string, updatedBy string) error {
	r.mu.Lock()
	defer r.mu.Unlock()

	symbol = strings.ToUpper(strings.TrimSpace(symbol))
	r.upserts = append(r.upserts, fakeManualValuationUpsert{
		Symbol:    symbol,
		Ratio:     ratio,
		Source:    source,
		UpdatedBy: updatedBy,
	})
	if r.data.ManualPositionOverrides == nil {
		r.data.ManualPositionOverrides = map[string]domain.ManualValuationPositionOverride{}
	}
	r.data.ManualPositionOverrides[symbol] = domain.ManualValuationPositionOverride{
		Symbol:    symbol,
		Ratio:     ratio,
		Source:    source,
		UpdatedBy: updatedBy,
		UpdatedAt: time.Date(2026, 6, 19, 9, 30, 0, 0, time.UTC),
	}
	return nil
}

type fakeValuationPositionApplyCall struct {
	Symbol string
	Ratio  float64
	Actor  string
}

type fakeValuationPositionController struct {
	mu       sync.Mutex
	snapshot valuationPositionStatusSnapshot
	calls    []fakeValuationPositionApplyCall
}

func newFakeValuationPositionController() *fakeValuationPositionController {
	return &fakeValuationPositionController{
		snapshot: valuationPositionStatusSnapshot{
			States: map[string]valuationPositionStateItem{},
		},
	}
}

func (c *fakeValuationPositionController) Snapshot() valuationPositionStatusSnapshot {
	c.mu.Lock()
	defer c.mu.Unlock()

	out := valuationPositionStatusSnapshot{
		Uploader: c.snapshot.Uploader,
		States:   make(map[string]valuationPositionStateItem, len(c.snapshot.States)),
	}
	for symbol, state := range c.snapshot.States {
		out.States[symbol] = state
	}
	return out
}

func (c *fakeValuationPositionController) ApplyOverride(_ context.Context, symbol string, ratio float64, actor string) (valuationPositionApplyResult, error) {
	c.mu.Lock()
	defer c.mu.Unlock()

	symbol = strings.ToUpper(strings.TrimSpace(symbol))
	updatedAt := time.Date(2026, 6, 19, 9, 31, 0, 0, time.UTC).Format(time.RFC3339Nano)
	c.calls = append(c.calls, fakeValuationPositionApplyCall{
		Symbol: symbol,
		Ratio:  ratio,
		Actor:  actor,
	})
	c.snapshot.Uploader = valuationPositionUploaderStatus{
		Connected:   true,
		ClientID:    "uploader-1",
		Source:      "home-mac",
		LastSeenAt:  updatedAt,
		KnownStates: 1,
	}
	c.snapshot.States[symbol] = valuationPositionStateItem{
		Symbol:    symbol,
		Ratio:     ratio,
		Source:    "home-mac",
		UpdatedAt: updatedAt,
	}
	return valuationPositionApplyResult{
		RequestID:    "req-1",
		ClientID:     "uploader-1",
		ClientSource: "home-mac",
		States: map[string]valuationPositionStateItem{
			symbol: c.snapshot.States[symbol],
		},
	}, nil
}

func findValuationPositionRow(t *testing.T, rows []valuationPositionRatioStatus, symbol string) valuationPositionRatioStatus {
	t.Helper()
	for _, row := range rows {
		if row.Symbol == symbol {
			return row
		}
	}
	t.Fatalf("missing row for %s", symbol)
	return valuationPositionRatioStatus{}
}

func TestUpdateValuationPositionRequiresDebugAuth(t *testing.T) {
	service := snapshot.NewService(snapshot.ServiceOptions{
		Repository: newFakeManualValuationRepository(),
	})
	server := NewServer(service, "", "")
	server.valuationPositions = newFakeValuationPositionController()

	req := httptest.NewRequest(http.MethodPost, "/api/v1/navsettings/valuation-positions", strings.NewReader(`{"symbol":"SH501018","ratio":0.73}`))
	req.Header.Set("Content-Type", "application/json")
	res := httptest.NewRecorder()

	server.ServeHTTP(res, req)

	if res.Code != http.StatusUnauthorized {
		t.Fatalf("status = %d, want %d; body=%s", res.Code, http.StatusUnauthorized, res.Body.String())
	}
	if !strings.Contains(res.Body.String(), "debug_login_required") {
		t.Fatalf("body = %s, want debug_login_required", res.Body.String())
	}
}

func TestUpdateValuationPositionPersistsAndReturnsSyncedState(t *testing.T) {
	repository := newFakeManualValuationRepository()
	service := snapshot.NewService(snapshot.ServiceOptions{Repository: repository})
	server := NewServer(service, "", "")
	controller := newFakeValuationPositionController()
	server.valuationPositions = controller

	req := httptest.NewRequest(http.MethodPost, "/api/v1/navsettings/valuation-positions", strings.NewReader(`{"symbol":"SH501018","ratio":0.73}`))
	req.Header.Set("Content-Type", "application/json")
	req.AddCookie(loginDebugSession(t, server))
	res := httptest.NewRecorder()

	server.ServeHTTP(res, req)

	if res.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d; body=%s", res.Code, http.StatusOK, res.Body.String())
	}

	var payload struct {
		OK             bool                         `json:"ok"`
		Symbol         string                       `json:"symbol"`
		EffectiveRatio float64                      `json:"effective_ratio"`
		UploaderSource string                       `json:"uploader_source"`
		SyncStatus     string                       `json:"sync_status"`
		Row            valuationPositionRatioStatus `json:"row"`
		Status         navSettingsStatusResponse    `json:"status"`
	}
	if err := json.Unmarshal(res.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if !payload.OK || payload.Symbol != "SH501018" {
		t.Fatalf("payload = %+v, want success for SH501018", payload)
	}
	if payload.EffectiveRatio != 0.73 {
		t.Fatalf("effective_ratio = %.6f, want 0.730000", payload.EffectiveRatio)
	}
	if payload.UploaderSource != "home-mac" {
		t.Fatalf("uploader_source = %q, want home-mac", payload.UploaderSource)
	}
	if payload.SyncStatus != valuationPositionSyncSynced {
		t.Fatalf("sync_status = %q, want %q", payload.SyncStatus, valuationPositionSyncSynced)
	}
	if len(repository.upserts) != 1 {
		t.Fatalf("upserts = %d, want 1", len(repository.upserts))
	}
	if repository.upserts[0].Source != "debug_ws:home-mac" {
		t.Fatalf("source = %q, want debug_ws:home-mac", repository.upserts[0].Source)
	}
	if repository.upserts[0].UpdatedBy != "Dirac" {
		t.Fatalf("updated_by = %q, want Dirac", repository.upserts[0].UpdatedBy)
	}
	if len(controller.calls) != 1 {
		t.Fatalf("controller calls = %d, want 1", len(controller.calls))
	}
	if controller.calls[0].Actor != "Dirac" {
		t.Fatalf("controller actor = %q, want Dirac", controller.calls[0].Actor)
	}

	row := payload.Row
	if row.EffectiveRatio != 0.73 {
		t.Fatalf("row.effective_ratio = %.6f, want 0.730000", row.EffectiveRatio)
	}
	if row.EffectiveRatioSource != domain.EffectiveRatioSourceManualOverride {
		t.Fatalf("row.effective_ratio_source = %q, want %q", row.EffectiveRatioSource, domain.EffectiveRatioSourceManualOverride)
	}
	if row.ManualOverrideRatio == nil || *row.ManualOverrideRatio != 0.73 {
		t.Fatalf("row.manual_override_ratio = %+v, want 0.73", row.ManualOverrideRatio)
	}
	if row.UploaderLocalRatio == nil || *row.UploaderLocalRatio != 0.73 {
		t.Fatalf("row.uploader_local_ratio = %+v, want 0.73", row.UploaderLocalRatio)
	}
	if row.UploaderSyncStatus != valuationPositionSyncSynced {
		t.Fatalf("row.uploader_sync_status = %q, want %q", row.UploaderSyncStatus, valuationPositionSyncSynced)
	}
	if !payload.Status.Uploader.Connected || payload.Status.Uploader.Source != "home-mac" {
		t.Fatalf("uploader status = %+v, want connected home-mac", payload.Status.Uploader)
	}

	statusRow := findValuationPositionRow(t, payload.Status.Ratios, "SH501018")
	if statusRow.EffectiveRatio != 0.73 {
		t.Fatalf("status row effective_ratio = %.6f, want 0.730000", statusRow.EffectiveRatio)
	}
	if statusRow.ManualOverrideRatio == nil || *statusRow.ManualOverrideRatio != 0.73 {
		t.Fatalf("status row manual_override_ratio = %+v, want 0.73", statusRow.ManualOverrideRatio)
	}
}
