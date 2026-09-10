package web

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"newnavnav/internal/domain"
	"newnavnav/internal/privatevaluation"
	"newnavnav/internal/sharesync"
	"newnavnav/internal/snapshot"
)

type recordedVisit struct {
	kind   string
	target string
	method string
}

type fakeVisitRepository struct {
	visits chan recordedVisit
}

type fakeContactMessageRepository struct {
	rows []domain.ContactMessage
}

func newFakeVisitRepository() *fakeVisitRepository {
	return &fakeVisitRepository{visits: make(chan recordedVisit, 4)}
}

func newFakeContactMessageRepository() *fakeContactMessageRepository {
	return &fakeContactMessageRepository{}
}

func (r *fakeVisitRepository) IncrementVisitCount(ctx context.Context, when time.Time, kind string, target string, method string) error {
	r.visits <- recordedVisit{kind: kind, target: target, method: method}
	return nil
}

func (r *fakeVisitRepository) LoadVisitCounts(ctx context.Context, days int) ([]domain.VisitCount, error) {
	return []domain.VisitCount{{Date: "2026-06-02", Kind: "page", Target: "home", Count: 3}}, nil
}

func (r *fakeContactMessageRepository) CreateContactMessage(ctx context.Context, email string, message string) error {
	r.rows = append(r.rows, domain.ContactMessage{
		ID:        int64(len(r.rows) + 1),
		Email:     email,
		Message:   message,
		CreatedAt: time.Date(2026, 6, 11, 12, 0, 0, 0, time.UTC),
	})
	return nil
}

func (r *fakeContactMessageRepository) LoadContactMessages(ctx context.Context, limit int) ([]domain.ContactMessage, error) {
	if limit <= 0 || limit > len(r.rows) {
		limit = len(r.rows)
	}
	out := make([]domain.ContactMessage, 0, limit)
	for i := len(r.rows) - 1; i >= 0 && len(out) < limit; i-- {
		out = append(out, r.rows[i])
	}
	return out, nil
}

func (r *fakeVisitRepository) wait(t *testing.T) recordedVisit {
	t.Helper()
	select {
	case visit := <-r.visits:
		return visit
	case <-time.After(time.Second):
		t.Fatal("timed out waiting for visit count")
		return recordedVisit{}
	}
}

func loginDebugSession(t *testing.T, server *Server) *http.Cookie {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, "/api/v1/debug/auth/login", strings.NewReader(`{"username":"Dirac","password":"727272"}`))
	req.Header.Set("Content-Type", "application/json")
	res := httptest.NewRecorder()
	server.ServeHTTP(res, req)
	if res.Code != http.StatusOK {
		t.Fatalf("login status = %d, want %d; body=%s", res.Code, http.StatusOK, res.Body.String())
	}
	cookies := res.Result().Cookies()
	if len(cookies) == 0 {
		t.Fatal("expected debug session cookie")
	}
	return cookies[0]
}

func TestUploadQuotesRequiresToken(t *testing.T) {
	service := snapshot.NewService(snapshot.ServiceOptions{})
	server := NewServer(service, "", "")
	req := httptest.NewRequest(http.MethodPost, "/api/v1/uploads/quotes", strings.NewReader(`{"quotes":[{"symbol":"hf_CL","price":91.35}]}`))
	req.Header.Set("Content-Type", "application/json")
	res := httptest.NewRecorder()

	server.ServeHTTP(res, req)

	if res.Code != http.StatusUnauthorized {
		t.Fatalf("status = %d, want %d", res.Code, http.StatusUnauthorized)
	}
}

func TestAPIAccessRecordsNormalizedVisitCount(t *testing.T) {
	service := snapshot.NewService(snapshot.ServiceOptions{})
	visits := newFakeVisitRepository()
	server := NewServer(service, "", "", visits)
	req := httptest.NewRequest(http.MethodGet, "/api/v1/funds/SZ159529", nil)
	res := httptest.NewRecorder()

	server.ServeHTTP(res, req)

	visit := visits.wait(t)
	if visit.kind != "api" || visit.target != "/api/v1/funds/:symbol" || visit.method != http.MethodGet {
		t.Fatalf("visit = %+v, want normalized fund API visit", visit)
	}
}

func TestNormalizeAPITargetCoversFundSubresourceAPIs(t *testing.T) {
	cases := map[string]string{
		"/api/v1/funds/yesterday-redemption-board":       "/api/v1/funds/yesterday-redemption-board",
		"/api/v1/funds/SZ159529/effective-ratio-history": "/api/v1/funds/:symbol/effective-ratio-history",
		"/api/v1/funds/SZ164701/share-history":           "/api/v1/funds/:symbol/share-history",
		"/api/v1/funds/SZ159529/minute-history/dates":    "/api/v1/funds/:symbol/minute-history/dates",
		"/api/v1/funds/SZ159529/minute-history":          "/api/v1/funds/:symbol/minute-history",
		"/api/v1/funds/SZ159529":                         "/api/v1/funds/:symbol",
	}

	for path, want := range cases {
		if got := normalizeAPITarget(path); got != want {
			t.Fatalf("normalizeAPITarget(%q) = %q, want %q", path, got, want)
		}
	}
}

func TestShareHistoryReturnsAllRequestedRows(t *testing.T) {
	store := sharesync.NewCSVStore(t.TempDir())
	rows := make([]domain.ShareHistoryRecord, 0, 61)
	start := time.Date(2026, 4, 1, 0, 0, 0, 0, time.UTC)
	for i := 0; i < 61; i++ {
		rows = append(rows, domain.ShareHistoryRecord{
			Symbol:    "SZ164701",
			Name:      "黄金LOF",
			FundType:  "LOF",
			ShareDate: start.AddDate(0, 0, i).Format("2006-01-02"),
			Shares10K: float64(1000 + i),
			Source:    "szse_fund_size",
		})
	}
	if err := store.UpsertShareHistory(context.Background(), rows); err != nil {
		t.Fatal(err)
	}
	service := snapshot.NewService(snapshot.ServiceOptions{ShareHistory: store})
	server := NewServer(service, "", "")
	req := httptest.NewRequest(http.MethodGet, "/api/v1/funds/SZ164701/share-history?days=3660", nil)
	res := httptest.NewRecorder()

	server.ServeHTTP(res, req)

	if res.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d; body=%s", res.Code, http.StatusOK, res.Body.String())
	}
	var payload domain.ShareHistoryResponse
	if err := json.Unmarshal(res.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	var raw map[string]any
	if err := json.Unmarshal(res.Body.Bytes(), &raw); err != nil {
		t.Fatal(err)
	}
	if _, ok := raw["fund_type"]; ok {
		t.Fatal("share history response should not expose fund_type")
	}
	if rawRows, ok := raw["rows"].([]any); ok && len(rawRows) > 0 {
		rawRow, ok := rawRows[0].(map[string]any)
		if !ok {
			t.Fatalf("first row type = %T, want object", rawRows[0])
		}
		if _, ok := rawRow["fund_type"]; ok {
			t.Fatal("share history rows should not expose fund_type")
		}
		if _, ok := rawRow["source"]; ok {
			t.Fatal("share history rows should not expose source")
		}
	}
	if len(payload.Rows) != 61 {
		t.Fatalf("rows = %d, want %d", len(payload.Rows), 61)
	}
	if payload.Rows[0].ShareDate != "2026-05-31" {
		t.Fatalf("latest date = %s, want 2026-05-31", payload.Rows[0].ShareDate)
	}
	last := payload.Rows[len(payload.Rows)-1]
	if last.ShareDate != "2026-04-01" {
		t.Fatalf("oldest returned date = %s, want 2026-04-01", last.ShareDate)
	}
	if last.PreviousShares10K != nil {
		t.Fatalf("oldest previous shares = %v, want nil for earliest row", last.PreviousShares10K)
	}
}

func TestYesterdayRedemptionBoardEndpoint(t *testing.T) {
	store := sharesync.NewCSVStore(t.TempDir())
	closePremiumA := 1.12
	closePremiumB := -0.45
	err := store.UpsertShareHistory(context.Background(), []domain.ShareHistoryRecord{
		{Symbol: "SH513050", Name: "中概互联ETF", FundType: "ETF", ShareDate: "2026-06-16", Shares10K: 800, Source: "sse_etf_volume"},
		{Symbol: "SH513050", Name: "中概互联ETF", FundType: "ETF", ShareDate: "2026-06-17", Shares10K: 620, ClosePremiumPct: &closePremiumA, Source: "sse_etf_volume"},
		{Symbol: "SH513000", Name: "日经ETF", FundType: "ETF", ShareDate: "2026-06-16", Shares10K: 1000, Source: "sse_etf_volume"},
		{Symbol: "SH513000", Name: "日经ETF", FundType: "ETF", ShareDate: "2026-06-17", Shares10K: 1080, ClosePremiumPct: &closePremiumB, Source: "sse_etf_volume"},
		{Symbol: "SZ161226", Name: "国投白银LOF", FundType: "LOF", ShareDate: "2026-06-16", Shares10K: 3000, Source: "szse_fund_size"},
	})
	if err != nil {
		t.Fatal(err)
	}
	service := snapshot.NewService(snapshot.ServiceOptions{ShareHistory: store})
	server := NewServer(service, "", "")
	server.SetPrivateValuationService(&fakePrivateValuationService{})
	req := httptest.NewRequest(http.MethodGet, "/api/v1/funds/yesterday-redemption-board", nil)
	res := httptest.NewRecorder()

	server.ServeHTTP(res, req)

	if res.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d; body=%s", res.Code, http.StatusOK, res.Body.String())
	}
	var payload domain.YesterdayRedemptionBoardResponse
	if err := json.Unmarshal(res.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if payload.ShareDate != "2026-06-17" {
		t.Fatalf("share date = %s, want 2026-06-17", payload.ShareDate)
	}
	if payload.IncludedSymbols != 2 {
		t.Fatalf("included symbols = %d, want 2", payload.IncludedSymbols)
	}
	if payload.StaleSymbols != 1 {
		t.Fatalf("stale symbols = %d, want 1", payload.StaleSymbols)
	}
	if payload.MissingChangeRows != 0 {
		t.Fatalf("missing change rows = %d, want 0", payload.MissingChangeRows)
	}
	if payload.RedemptionCount != 1 || payload.SubscriptionCount != 1 || payload.FlatCount != 0 {
		t.Fatalf("unexpected counts: %+v", payload)
	}
	if len(payload.Rows) != 2 {
		t.Fatalf("rows = %d, want 2", len(payload.Rows))
	}
	if payload.Rows[0].Symbol != "SH513050" {
		t.Fatalf("first row symbol = %s, want SH513050", payload.Rows[0].Symbol)
	}
	if payload.Rows[0].ShareChange10K == nil || *payload.Rows[0].ShareChange10K != -180 {
		t.Fatalf("first row change = %v, want -180", payload.Rows[0].ShareChange10K)
	}
	if payload.Rows[0].ClosePremiumPct == nil || *payload.Rows[0].ClosePremiumPct != 1.12 {
		t.Fatalf("first row close premium pct = %v, want 1.12", payload.Rows[0].ClosePremiumPct)
	}
	if got := strings.Join(payload.Rows[0].BranchNames, ","); got != "混合QDII" {
		t.Fatalf("first row branch names = %q, want 混合QDII", got)
	}
	if payload.Rows[1].Symbol != "SH513000" {
		t.Fatalf("second row symbol = %s, want SH513000", payload.Rows[1].Symbol)
	}

	privateReq := httptest.NewRequest(http.MethodGet, "/api/v1/private/funds", nil)
	privateRes := httptest.NewRecorder()
	server.ServeHTTP(privateRes, privateReq)
	if privateRes.Code != http.StatusOK {
		t.Fatalf("private list status = %d; body=%s", privateRes.Code, privateRes.Body.String())
	}
	var privatePayload privatevaluation.ListResponse
	if err := json.Unmarshal(privateRes.Body.Bytes(), &privatePayload); err != nil {
		t.Fatal(err)
	}
	bySymbol := make(map[string]privatevaluation.ListItem, len(privatePayload.Funds))
	for _, item := range privatePayload.Funds {
		bySymbol[item.Symbol] = item
	}
	item := bySymbol[privatevaluation.SH513050Symbol]
	if item.ShareDate != "2026-06-17" || item.ShareChange10K == nil || *item.ShareChange10K != -180 {
		t.Fatalf("private cached share change = %+v", item)
	}
	if item.ShareChangePct == nil || *item.ShareChangePct != -22.5 {
		t.Fatalf("private cached share change pct = %v, want -22.5", item.ShareChangePct)
	}
}

func TestHealthDoesNotRecordVisitCount(t *testing.T) {
	service := snapshot.NewService(snapshot.ServiceOptions{})
	visits := newFakeVisitRepository()
	server := NewServer(service, "", "", visits)
	req := httptest.NewRequest(http.MethodGet, "/api/v1/health", nil)
	res := httptest.NewRecorder()

	server.ServeHTTP(res, req)

	if res.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d", res.Code, http.StatusOK)
	}
	select {
	case visit := <-visits.visits:
		t.Fatalf("unexpected visit recorded: %+v", visit)
	case <-time.After(100 * time.Millisecond):
	}
}

func TestTrackVisitRecordsPageTarget(t *testing.T) {
	service := snapshot.NewService(snapshot.ServiceOptions{})
	visits := newFakeVisitRepository()
	server := NewServer(service, "", "", visits)
	req := httptest.NewRequest(http.MethodPost, "/api/v1/visits/track", strings.NewReader(`{"kind":"page","target":"fund:SZ159529"}`))
	req.Header.Set("Content-Type", "application/json")
	res := httptest.NewRecorder()

	server.ServeHTTP(res, req)

	if res.Code != http.StatusAccepted {
		t.Fatalf("status = %d, want %d; body=%s", res.Code, http.StatusAccepted, res.Body.String())
	}
	visit := visits.wait(t)
	if visit.kind != "page" || visit.target != "fund:SZ159529" || visit.method != "" {
		t.Fatalf("visit = %+v, want page visit", visit)
	}
}

func TestCreateContactMessageStoresValidatedPayload(t *testing.T) {
	service := snapshot.NewService(snapshot.ServiceOptions{})
	repo := newFakeContactMessageRepository()
	server := NewServer(service, "", "")
	server.SetContactMessageRepository(repo)

	req := httptest.NewRequest(http.MethodPost, "/api/v1/contact-messages", strings.NewReader(`{"email":"user@example.com","message":"  第一行\n第二行  "}`))
	req.Header.Set("Content-Type", "application/json")
	res := httptest.NewRecorder()

	server.ServeHTTP(res, req)

	if res.Code != http.StatusCreated {
		t.Fatalf("status = %d, want %d; body=%s", res.Code, http.StatusCreated, res.Body.String())
	}
	if len(repo.rows) != 1 {
		t.Fatalf("stored rows = %d, want 1", len(repo.rows))
	}
	if repo.rows[0].Email != "user@example.com" {
		t.Fatalf("email = %q, want user@example.com", repo.rows[0].Email)
	}
	if repo.rows[0].Message != "第一行\n第二行" {
		t.Fatalf("message = %q, want trimmed multiline body", repo.rows[0].Message)
	}
}

func TestCreateContactMessageRejectsInvalidEmail(t *testing.T) {
	service := snapshot.NewService(snapshot.ServiceOptions{})
	repo := newFakeContactMessageRepository()
	server := NewServer(service, "", "")
	server.SetContactMessageRepository(repo)

	req := httptest.NewRequest(http.MethodPost, "/api/v1/contact-messages", strings.NewReader(`{"email":"Admin <user@example.com>","message":"hello"}`))
	req.Header.Set("Content-Type", "application/json")
	res := httptest.NewRecorder()

	server.ServeHTTP(res, req)

	if res.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want %d; body=%s", res.Code, http.StatusBadRequest, res.Body.String())
	}
	if len(repo.rows) != 0 {
		t.Fatalf("stored rows = %d, want 0", len(repo.rows))
	}
}

func TestContactMessageInboxRequiresMessageAdminToken(t *testing.T) {
	service := snapshot.NewService(snapshot.ServiceOptions{})
	repo := newFakeContactMessageRepository()
	_ = repo.CreateContactMessage(context.Background(), "user@example.com", "hello")
	server := NewServer(service, "", "")
	server.SetContactMessageRepository(repo)
	server.SetMessageAdminToken("message-secret")

	req := httptest.NewRequest(http.MethodGet, "/api/v1/admin/contact-messages", nil)
	res := httptest.NewRecorder()
	server.ServeHTTP(res, req)
	if res.Code != http.StatusForbidden {
		t.Fatalf("locked status = %d, want %d; body=%s", res.Code, http.StatusForbidden, res.Body.String())
	}

	req = httptest.NewRequest(http.MethodGet, "/contact-admin/inbox?message_key=message-secret", nil)
	res = httptest.NewRecorder()
	server.ServeHTTP(res, req)
	if res.Code != http.StatusFound {
		t.Fatalf("unlock status = %d, want %d", res.Code, http.StatusFound)
	}
	cookies := res.Result().Cookies()
	if len(cookies) == 0 {
		t.Fatal("expected admin inbox cookie")
	}

	req = httptest.NewRequest(http.MethodGet, "/api/v1/admin/contact-messages", nil)
	req.AddCookie(cookies[0])
	res = httptest.NewRecorder()
	server.ServeHTTP(res, req)
	if res.Code != http.StatusOK {
		t.Fatalf("inbox status = %d, want %d; body=%s", res.Code, http.StatusOK, res.Body.String())
	}
	if !strings.Contains(res.Body.String(), "user@example.com") {
		t.Fatalf("inbox body = %s, want stored email", res.Body.String())
	}
}

func TestContactMessageInboxAllowsDebugSession(t *testing.T) {
	service := snapshot.NewService(snapshot.ServiceOptions{})
	repo := newFakeContactMessageRepository()
	_ = repo.CreateContactMessage(context.Background(), "user@example.com", "hello")
	server := NewServer(service, "", "")
	server.SetContactMessageRepository(repo)
	server.SetMessageAdminToken("message-secret")

	req := httptest.NewRequest(http.MethodGet, "/api/v1/admin/contact-messages", nil)
	req.AddCookie(loginDebugSession(t, server))
	res := httptest.NewRecorder()
	server.ServeHTTP(res, req)

	if res.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d; body=%s", res.Code, http.StatusOK, res.Body.String())
	}
	if !strings.Contains(res.Body.String(), "user@example.com") {
		t.Fatalf("body = %s, want stored email", res.Body.String())
	}
}

func TestContactMessageInboxAllowsLegacyDebugAccess(t *testing.T) {
	service := snapshot.NewService(snapshot.ServiceOptions{})
	repo := newFakeContactMessageRepository()
	_ = repo.CreateContactMessage(context.Background(), "user@example.com", "hello")
	server := NewServer(service, "", "")
	server.SetContactMessageRepository(repo)
	server.SetMessageAdminToken("message-secret")
	server.SetDataViewToken("view-secret")

	req := httptest.NewRequest(http.MethodGet, "/debug/contact-inbox?view_key=view-secret", nil)
	res := httptest.NewRecorder()
	server.ServeHTTP(res, req)
	if res.Code != http.StatusFound {
		t.Fatalf("unlock status = %d, want %d", res.Code, http.StatusFound)
	}
	cookies := res.Result().Cookies()
	if len(cookies) == 0 {
		t.Fatal("expected legacy debug cookie")
	}

	req = httptest.NewRequest(http.MethodGet, "/api/v1/admin/contact-messages", nil)
	req.AddCookie(cookies[0])
	res = httptest.NewRecorder()
	server.ServeHTTP(res, req)

	if res.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d; body=%s", res.Code, http.StatusOK, res.Body.String())
	}
	if !strings.Contains(res.Body.String(), "user@example.com") {
		t.Fatalf("body = %s, want stored email", res.Body.String())
	}
}

func TestUploadQuotesCachesWhenAuthorized(t *testing.T) {
	service := snapshot.NewService(snapshot.ServiceOptions{})
	server := NewServer(service, "", "secret")
	req := httptest.NewRequest(http.MethodPost, "/api/v1/uploads/quotes", strings.NewReader(`{"source":"home-mac","quotes":[{"symbol":"SH513210","price":1.531,"prev_close":1.506,"limit_up":1.657,"limit_down":1.355,"bid_levels":[{"level":1,"price":1.531,"volume":183400}],"ask_levels":[{"level":1,"price":1.532,"volume":692600}]}]}`))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Upload-Token", "secret")
	res := httptest.NewRecorder()

	server.ServeHTTP(res, req)

	if res.Code != http.StatusAccepted {
		t.Fatalf("status = %d, want %d; body=%s", res.Code, http.StatusAccepted, res.Body.String())
	}
	status := service.UploadedQuoteStatus(true)
	if status.Enabled {
		t.Fatal("uploaded quotes should be disabled by default")
	}
	if status.Count != 1 || len(status.Quotes) != 1 || status.Quotes[0].Symbol != "SH513210" {
		t.Fatalf("unexpected upload status: %+v", status)
	}
	quote := status.Quotes[0]
	if quote.LimitUp != 1.657 || quote.LimitDown != 1.355 || len(quote.BidLevels) != 1 || len(quote.AskLevels) != 1 {
		t.Fatalf("order book fields were not cached: %+v", quote)
	}

	req = httptest.NewRequest(http.MethodPost, "/api/v1/uploads/quotes", strings.NewReader(`{"source":"home-mac","quotes":[{"symbol":"SH513210","price":1.532,"prev_close":1.506}]}`))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Upload-Token", "secret")
	res = httptest.NewRecorder()

	server.ServeHTTP(res, req)

	if res.Code != http.StatusAccepted {
		t.Fatalf("second status = %d, want %d; body=%s", res.Code, http.StatusAccepted, res.Body.String())
	}
	status = service.UploadedQuoteStatus(true)
	quote = status.Quotes[0]
	if quote.Price != 1.532 || quote.LimitUp != 1.657 || quote.LimitDown != 1.355 || len(quote.BidLevels) != 1 || len(quote.AskLevels) != 1 {
		t.Fatalf("order book fields were not preserved after partial upload: %+v", quote)
	}
}

func TestRequiredQuoteSymbolsRequiresToken(t *testing.T) {
	service := snapshot.NewService(snapshot.ServiceOptions{})
	server := NewServer(service, "", "")
	req := httptest.NewRequest(http.MethodGet, "/api/v1/uploads/quotes/required-symbols", nil)
	res := httptest.NewRecorder()

	server.ServeHTTP(res, req)

	if res.Code != http.StatusUnauthorized {
		t.Fatalf("status = %d, want %d", res.Code, http.StatusUnauthorized)
	}
}

func TestDebugStatusIncludesRuntimeStatus(t *testing.T) {
	service := snapshot.NewService(snapshot.ServiceOptions{})
	service.SetRuntimeStatusProvider(func() any {
		return []map[string]any{{
			"key":     "purchase_status",
			"name":    "purchase status",
			"enabled": true,
			"status":  "ok",
		}}
	})
	server := NewServer(service, "", "")
	req := httptest.NewRequest(http.MethodGet, "/api/v1/debug/status", nil)
	req.AddCookie(loginDebugSession(t, server))
	res := httptest.NewRecorder()

	server.ServeHTTP(res, req)

	if res.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d; body=%s", res.Code, http.StatusOK, res.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(res.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	tasks, ok := payload["runtime_status"].([]any)
	if !ok || len(tasks) != 1 {
		t.Fatalf("runtime_status = %#v, want one task", payload["runtime_status"])
	}
	task, ok := tasks[0].(map[string]any)
	if !ok || task["key"] != "purchase_status" || task["status"] != "ok" {
		t.Fatalf("task = %#v, want purchase_status ok", tasks[0])
	}
}

func TestFundMinuteHistorySupportsDateAndDateList(t *testing.T) {
	root := t.TempDir()
	dayDir := filepath.Join(root, "20260604")
	if err := os.MkdirAll(dayDir, 0o755); err != nil {
		t.Fatal(err)
	}
	csvBody := "symbol,minute,timestamp,market_price,estimated_nav,premium_pct,official_est,fair_est,realtime_est,effective_ratio,quote_source,quote_status,model_version,reference_symbol,upload_source\n" +
		"SZ164824,2026-06-04 10:00,2026-06-04T10:00:05+08:00,1.23456,1.25049,-1.6789,1.24,1.25,1.25,0.9,sina,realtime,test,INDA,seed\n"
	if err := os.WriteFile(filepath.Join(dayDir, "SZ164824.csv"), []byte(csvBody), 0o644); err != nil {
		t.Fatal(err)
	}
	service := snapshot.NewService(snapshot.ServiceOptions{MinuteHistoryDir: root})
	server := NewServer(service, "", "")

	req := httptest.NewRequest(http.MethodGet, "/api/v1/funds/SZ164824/minute-history?date=20260604", nil)
	res := httptest.NewRecorder()
	server.ServeHTTP(res, req)
	if res.Code != http.StatusOK {
		t.Fatalf("history status = %d, want 200; body=%s", res.Code, res.Body.String())
	}
	var history snapshot.MinuteHistoryResponse
	if err := json.Unmarshal(res.Body.Bytes(), &history); err != nil {
		t.Fatal(err)
	}
	if history.Date != "20260604" || len(history.Rows) != 1 || history.Rows[0].Minute != "10:00" {
		t.Fatalf("history = %+v, want one dated row", history)
	}
	if history.Rows[0].MarketPrice != 1.235 || history.Rows[0].EstimatedNAV != 1.2505 || history.Rows[0].PremiumPct != -1.68 {
		t.Fatalf("history row = %+v, want rounded transport values", history.Rows[0])
	}
	body := res.Body.String()
	for _, hidden := range []string{"timestamp", "effective_ratio", "quote_source", "quote_status", "model_version", "reference_symbol", "upload_source"} {
		if strings.Contains(body, hidden) {
			t.Fatalf("history response exposed internal field %q: %s", hidden, body)
		}
	}
	for _, legacy := range []string{`"minute":`, `"market_price":`, `"estimated_nav":`, `"premium_pct":`} {
		if strings.Contains(body, legacy) {
			t.Fatalf("history response still exposed legacy field %q: %s", legacy, body)
		}
	}
	for _, compact := range []string{`"min":"10:00"`, `"mkp":1.235`, `"estnav":1.2505`, `"pmp":-1.68`} {
		if !strings.Contains(body, compact) {
			t.Fatalf("history response missing compact field %q: %s", compact, body)
		}
	}

	req = httptest.NewRequest(http.MethodGet, "/api/v1/funds/SZ164824/minute-history/dates", nil)
	res = httptest.NewRecorder()
	server.ServeHTTP(res, req)
	if res.Code != http.StatusOK {
		t.Fatalf("dates status = %d, want 200; body=%s", res.Code, res.Body.String())
	}
	var dates snapshot.MinuteHistoryDatesResponse
	if err := json.Unmarshal(res.Body.Bytes(), &dates); err != nil {
		t.Fatal(err)
	}
	if len(dates.Dates) != 1 || dates.Dates[0] != "20260604" {
		t.Fatalf("dates = %+v, want 20260604", dates)
	}
}

func TestDataViewGateBlocksPublicDataAndUnlocksWithCookie(t *testing.T) {
	service := snapshot.NewService(snapshot.ServiceOptions{})
	server := NewServer(service, "", "upload-secret")
	server.SetDataViewToken("view-secret")

	req := httptest.NewRequest(http.MethodGet, "/api/v1/branches", nil)
	res := httptest.NewRecorder()
	server.ServeHTTP(res, req)
	if res.Code != http.StatusServiceUnavailable {
		t.Fatalf("locked status = %d, want %d; body=%s", res.Code, http.StatusServiceUnavailable, res.Body.String())
	}
	if !strings.Contains(res.Body.String(), "data_view_locked") {
		t.Fatalf("locked body = %s, want data_view_locked", res.Body.String())
	}

	req = httptest.NewRequest(http.MethodGet, "/?view_key=view-secret", nil)
	res = httptest.NewRecorder()
	server.ServeHTTP(res, req)
	if res.Code != http.StatusFound {
		t.Fatalf("unlock status = %d, want %d", res.Code, http.StatusFound)
	}
	cookies := res.Result().Cookies()
	if len(cookies) == 0 {
		t.Fatal("expected unlock cookie")
	}

	req = httptest.NewRequest(http.MethodGet, "/api/v1/branches", nil)
	req.AddCookie(cookies[0])
	res = httptest.NewRecorder()
	server.ServeHTTP(res, req)
	if res.Code != http.StatusOK {
		t.Fatalf("unlocked status = %d, want %d; body=%s", res.Code, http.StatusOK, res.Body.String())
	}

	req = httptest.NewRequest(http.MethodPost, "/api/v1/uploads/quotes", strings.NewReader(`{"quotes":[{"symbol":"hf_CL","price":91.35}]}`))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Upload-Token", "upload-secret")
	res = httptest.NewRecorder()
	server.ServeHTTP(res, req)
	if res.Code != http.StatusAccepted {
		t.Fatalf("upload status = %d, want %d; body=%s", res.Code, http.StatusAccepted, res.Body.String())
	}
}

func TestBranchAPIOMitsReferenceRows(t *testing.T) {
	service := snapshot.NewService(snapshot.ServiceOptions{})
	if err := service.Refresh(context.Background()); err != nil {
		t.Fatalf("refresh failed: %v", err)
	}
	server := NewServer(service, "", "")

	req := httptest.NewRequest(http.MethodGet, "/api/v1/branches/"+domain.Branches[0].Key, nil)
	res := httptest.NewRecorder()
	server.ServeHTTP(res, req)
	if res.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d; body=%s", res.Code, http.StatusOK, res.Body.String())
	}

	var payload map[string]any
	if err := json.Unmarshal(res.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if _, ok := payload["reference_rows"]; ok {
		t.Fatalf("branch payload should omit reference_rows: %s", res.Body.String())
	}
	rows, ok := payload["estimate_rows"].([]any)
	if !ok || len(rows) == 0 {
		t.Fatalf("estimate_rows missing or empty: %+v", payload)
	}
}

func TestNavSettingsGateBlocksRouteAndUnlocksWithCookie(t *testing.T) {
	service := snapshot.NewService(snapshot.ServiceOptions{})
	server := NewServer(service, "", "")
	server.SetNavSettingsToken("settings-secret")

	req := httptest.NewRequest(http.MethodGet, "/navsettings", nil)
	res := httptest.NewRecorder()
	server.ServeHTTP(res, req)
	if res.Code != http.StatusForbidden {
		t.Fatalf("locked status = %d, want %d; body=%s", res.Code, http.StatusForbidden, res.Body.String())
	}

	req = httptest.NewRequest(http.MethodGet, "/navsettings?view_key=settings-secret", nil)
	res = httptest.NewRecorder()
	server.ServeHTTP(res, req)
	if res.Code != http.StatusFound {
		t.Fatalf("unlock status = %d, want %d", res.Code, http.StatusFound)
	}
	cookies := res.Result().Cookies()
	if len(cookies) == 0 {
		t.Fatal("expected nav settings cookie")
	}

	req = httptest.NewRequest(http.MethodGet, "/api/v1/navsettings/status", nil)
	req.AddCookie(cookies[0])
	res = httptest.NewRecorder()
	server.ServeHTTP(res, req)
	if res.Code != http.StatusOK {
		t.Fatalf("api status = %d, want %d; body=%s", res.Code, http.StatusOK, res.Body.String())
	}
}

func TestDebugLoginUnlocksProtectedDebugRoutes(t *testing.T) {
	service := snapshot.NewService(snapshot.ServiceOptions{})
	server := NewServer(service, "", "")

	req := httptest.NewRequest(http.MethodGet, "/api/v1/debug/status", nil)
	res := httptest.NewRecorder()
	server.ServeHTTP(res, req)
	if res.Code != http.StatusUnauthorized {
		t.Fatalf("locked status = %d, want %d; body=%s", res.Code, http.StatusUnauthorized, res.Body.String())
	}
	if !strings.Contains(res.Body.String(), "debug_login_required") {
		t.Fatalf("locked body = %s, want debug_login_required", res.Body.String())
	}

	req = httptest.NewRequest(http.MethodPost, "/api/v1/debug/auth/login", strings.NewReader(`{"username":"Dirac","password":"727272"}`))
	req.Header.Set("Content-Type", "application/json")
	res = httptest.NewRecorder()
	server.ServeHTTP(res, req)
	if res.Code != http.StatusOK {
		t.Fatalf("login status = %d, want %d; body=%s", res.Code, http.StatusOK, res.Body.String())
	}
	cookies := res.Result().Cookies()
	if len(cookies) == 0 {
		t.Fatal("expected debug session cookie")
	}

	req = httptest.NewRequest(http.MethodGet, "/api/v1/debug/auth/status", nil)
	req.AddCookie(cookies[0])
	res = httptest.NewRecorder()
	server.ServeHTTP(res, req)
	if res.Code != http.StatusOK {
		t.Fatalf("auth status = %d, want %d; body=%s", res.Code, http.StatusOK, res.Body.String())
	}
	if !strings.Contains(res.Body.String(), `"authenticated":true`) {
		t.Fatalf("auth payload = %s, want authenticated true", res.Body.String())
	}

	req = httptest.NewRequest(http.MethodGet, "/api/v1/debug/status", nil)
	req.AddCookie(cookies[0])
	res = httptest.NewRecorder()
	server.ServeHTTP(res, req)
	if res.Code != http.StatusOK {
		t.Fatalf("debug status = %d, want %d; body=%s", res.Code, http.StatusOK, res.Body.String())
	}

	req = httptest.NewRequest(http.MethodGet, "/api/v1/navsettings/status", nil)
	req.AddCookie(cookies[0])
	res = httptest.NewRecorder()
	server.ServeHTTP(res, req)
	if res.Code != http.StatusOK {
		t.Fatalf("navsettings status = %d, want %d; body=%s", res.Code, http.StatusOK, res.Body.String())
	}

	req = httptest.NewRequest(http.MethodPost, "/api/v1/debug/auth/logout", nil)
	req.AddCookie(cookies[0])
	res = httptest.NewRecorder()
	server.ServeHTTP(res, req)
	if res.Code != http.StatusOK {
		t.Fatalf("logout status = %d, want %d; body=%s", res.Code, http.StatusOK, res.Body.String())
	}

	req = httptest.NewRequest(http.MethodGet, "/api/v1/navsettings/status", nil)
	res = httptest.NewRecorder()
	server.ServeHTTP(res, req)
	if res.Code != http.StatusUnauthorized {
		t.Fatalf("post-logout status = %d, want %d; body=%s", res.Code, http.StatusUnauthorized, res.Body.String())
	}
}
