package web

import (
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"newnavnav/internal/domain"
	"newnavnav/internal/hkconnectfx"
	"newnavnav/internal/snapshot"
)

type Server struct {
	snapshots          *snapshot.Service
	privateValuation   PrivateValuationService
	hkConnectFX        *hkconnectfx.Service
	frontendDist       string
	fileServer         http.Handler
	uploadToken        string
	dataViewHash       string
	navSettingsHash    string
	messageAdminHash   string
	debugAccess        *debugAccessManager
	valuationPositions valuationPositionController
	visits             VisitRepository
	contactMessages    ContactMessageRepository
	contactLimiter     *contactMessageLimiter
	intradayRebuild    *intradayRebuildController
	intradayAgentHash  string
	privateShareMu     sync.RWMutex
	privateShareReady  bool
	privateShareCache  map[string]privateShareChange
}

const dataViewCookieName = "__nnn_data_view"
const navSettingsCookieName = "__nnn_nav_settings"
const defaultShareHistoryRows = 60

type VisitRepository interface {
	IncrementVisitCount(ctx context.Context, when time.Time, kind string, target string, method string) error
	LoadVisitCounts(ctx context.Context, days int) ([]domain.VisitCount, error)
}

func NewServer(snapshots *snapshot.Service, frontendDist string, uploadToken string, visits ...VisitRepository) *Server {
	var visitRepository VisitRepository
	if len(visits) > 0 {
		visitRepository = visits[0]
	}
	return &Server{
		snapshots:          snapshots,
		frontendDist:       frontendDist,
		fileServer:         http.FileServer(http.Dir(frontendDist)),
		uploadToken:        strings.TrimSpace(uploadToken),
		debugAccess:        newDebugAccessManager(defaultDebugUsername, defaultDebugPassword),
		valuationPositions: newWSValuationPositionController(),
		visits:             visitRepository,
		contactLimiter:     newContactMessageLimiter(3, 10*time.Minute),
		intradayRebuild:    newIntradayRebuildController(),
		privateShareCache:  make(map[string]privateShareChange),
	}
}

func (s *Server) SetDataViewToken(token string) {
	token = strings.TrimSpace(token)
	if token == "" {
		s.dataViewHash = ""
		return
	}
	sum := sha256.Sum256([]byte(token))
	s.dataViewHash = hex.EncodeToString(sum[:])
}

func (s *Server) SetNavSettingsToken(token string) {
	token = strings.TrimSpace(token)
	if token == "" {
		s.navSettingsHash = ""
		return
	}
	sum := sha256.Sum256([]byte(token))
	s.navSettingsHash = hex.EncodeToString(sum[:])
}

// SetIntradayRebuildAgentToken configures the dedicated credential accepted by
// the Mac-home intraday rebuild agent. It is intentionally independent from
// the generic upload token used by the normal quote uploader.
func (s *Server) SetIntradayRebuildAgentToken(token string) {
	token = strings.TrimSpace(token)
	if token == "" {
		s.intradayAgentHash = ""
		return
	}
	sum := sha256.Sum256([]byte(token))
	s.intradayAgentHash = hex.EncodeToString(sum[:])
}

// SetIntradayRebuildStorePath enables durable, local job metadata. Rebuild
// result persistence remains the responsibility of the existing private
// valuation import endpoints and database transaction flow.
func (s *Server) SetIntradayRebuildStorePath(path string) error {
	if s.intradayRebuild == nil {
		s.intradayRebuild = newIntradayRebuildController()
	}
	return s.intradayRebuild.setStorePath(path)
}

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
	w.Header().Set("Access-Control-Allow-Headers", "Content-Type,Authorization,X-Upload-Token,X-Data-View-Token,X-Message-Admin-Token,X-Intraday-Rebuild-Token")
	if r.Method == http.MethodOptions {
		w.WriteHeader(http.StatusNoContent)
		return
	}

	path := r.URL.Path
	if s.handleDataViewAccess(w, r) {
		return
	}
	if s.handleNavSettingsAccess(w, r) {
		return
	}
	if s.handleMessageAdminAccess(w, r) {
		return
	}
	switch {
	case path == "/api/v1/debug/auth/status" && r.Method == http.MethodGet:
		s.handleDebugAuthStatus(w, r)
		return
	case path == "/api/v1/debug/auth/login" && r.Method == http.MethodPost:
		s.handleDebugLogin(w, r)
		return
	case path == "/api/v1/debug/auth/logout" && r.Method == http.MethodPost:
		s.handleDebugLogout(w, r)
		return
	}
	if s.debugAccessRestricted(path) && !s.debugAccessAuthorized(r) {
		s.writeDebugAccessLocked(w, r)
		return
	}
	if s.dataViewRestricted(path) && !s.dataViewAuthorized(r) {
		s.writeDataViewLocked(w, r)
		return
	}
	if s.navSettingsRestricted(path) && !s.navSettingsAuthorized(r) {
		s.writeNavSettingsLocked(w, r)
		return
	}
	if s.messageAdminRestricted(path) && !s.messageAdminAuthorized(r) {
		s.writeMessageAdminLocked(w, r)
		return
	}
	if apiTarget := normalizeAPITarget(path); apiTarget != "" {
		s.recordVisit("api", apiTarget, r.Method)
	}
	switch {
	case path == "/api/v1/health":
		writeJSON(w, http.StatusOK, map[string]any{"ok": true})
	case path == "/api/v1/contact-messages" && r.Method == http.MethodPost:
		s.handleCreateContactMessage(w, r)
	case path == "/api/v1/admin/contact-messages" && r.Method == http.MethodGet:
		s.handleListContactMessages(w, r)
	case path == "/api/v1/visits/track" && r.Method == http.MethodPost:
		s.handleTrackVisit(w, r)
	case path == "/api/v1/visits":
		s.handleVisitCounts(w, r)
	case path == "/api/v1/private/funds":
		s.handlePrivateFundList(w, r)
	case path == "/api/v1/private/premiums/snapshot":
		s.handlePrivatePremiumSnapshot(w, r)
	case path == "/api/v1/private/premiums/stream":
		s.handlePrivatePremiumStream(w, r)
	case path == "/api/v1/private/hk-connect-fx":
		s.handleHKConnectFX(w, r)
	case path == "/api/v1/private/hk-connect-fx/history":
		s.handleHKConnectFXHistory(w, r)
	case path == "/api/v1/private/hk-connect-fx/daily-history":
		s.handleHKConnectFXDailyHistory(w, r)
	case path == "/api/v1/private/hk-connect-fx/close-history":
		s.handleHKConnectFXCloseHistory(w, r)
	case path == "/api/v1/private/hk-connect-fx/ib" || path == "/api/v1/private/hk-connect-fx/ib-backfill-tasks" || path == "/api/v1/private/hk-connect-fx/ib-anchors":
		writeJSON(w, http.StatusNotFound, map[string]any{"error": "HK Connect FX IBKR endpoint removed; backend now uses CFETS HKD/CNY"})
	case path == "/api/v1/private/inputs/batch":
		s.handlePrivateValuationInputBatch(w, r)
	case path == "/api/v1/private/a-share-broad-market/funds":
		s.handlePrivateChinaBroadMarketFundList(w, r)
	case strings.HasPrefix(path, "/api/v1/private/a-share-broad-market/funds/") && strings.HasSuffix(path, "/share-history"):
		s.handlePrivateChinaBroadMarketShareHistory(w, r)
	case strings.HasPrefix(path, "/api/v1/private/funds/") && strings.HasSuffix(path, "/nifty-bridge-history-review"):
		s.handlePrivateIndiaNiftyBridgeHistoryReview(w, r)
	case strings.HasPrefix(path, "/api/v1/private/funds/") && strings.HasSuffix(path, "/india-history-review"):
		s.handlePrivateIndiaHistoryReview(w, r)
	case strings.HasPrefix(path, "/api/v1/private/funds/") && strings.HasSuffix(path, "/close-history"):
		s.handlePrivateSilverCloseHistory(w, r)
	case strings.HasPrefix(path, "/api/v1/private/funds/") && strings.HasSuffix(path, "/final-nav-history/import"):
		s.handlePrivateIndiaFinalNAVHistoryImport(w, r)
	case strings.HasPrefix(path, "/api/v1/private/funds/") && strings.HasSuffix(path, "/minute-history/dates"):
		s.handlePrivateMinuteHistoryDates(w, r)
	case strings.HasPrefix(path, "/api/v1/private/funds/") && strings.HasSuffix(path, "/minute-history/import"):
		s.handlePrivateMinuteHistoryImport(w, r)
	case strings.HasPrefix(path, "/api/v1/private/funds/") && strings.HasSuffix(path, "/minute-history"):
		s.handlePrivateFundMinuteHistory(w, r)
	case strings.HasPrefix(path, "/api/v1/private/funds/"):
		s.handlePrivateFund(w, r)
	case strings.HasPrefix(path, "/api/v1/private/inputs/"):
		s.handlePrivateValuationInput(w, r)
	case path == "/api/v1/home":
		w.Header().Set("Cache-Control", "public, max-age=5, stale-while-revalidate=30")
		writeJSON(w, http.StatusOK, s.snapshots.HomeSnapshot())
	case path == "/api/v1/branches":
		writeJSON(w, http.StatusOK, s.snapshots.Branches())
	case path == "/api/v1/funds/yesterday-redemption-board":
		s.handleYesterdayRedemptionBoard(w, r)
	case strings.HasPrefix(path, "/api/v1/branches/"):
		key := strings.TrimPrefix(path, "/api/v1/branches/")
		s.handleBranch(w, key)
	case strings.HasPrefix(path, "/api/v1/funds/") && strings.HasSuffix(path, "/effective-ratio-history"):
		s.handleFundEffectiveRatioHistory(w, r)
	case strings.HasPrefix(path, "/api/v1/funds/") && strings.HasSuffix(path, "/share-history"):
		s.handleFundShareHistory(w, r)
	case strings.HasPrefix(path, "/api/v1/funds/") && strings.HasSuffix(path, "/minute-history/dates"):
		s.handleFundMinuteHistoryDates(w, r)
	case strings.HasPrefix(path, "/api/v1/funds/") && strings.HasSuffix(path, "/minute-history"):
		s.handleFundMinuteHistory(w, r)
	case strings.HasPrefix(path, "/api/v1/funds/"):
		symbol := strings.ToUpper(strings.TrimPrefix(path, "/api/v1/funds/"))
		s.handleFund(w, symbol)
	case path == "/api/v1/snapshots/status":
		writeJSON(w, http.StatusOK, s.snapshots.Status())
	case path == "/api/v1/navsettings/status":
		s.handleNavSettingsStatus(w, r)
	case path == "/api/v1/navsettings/refresh" && r.Method == http.MethodPost:
		s.handleNavSettingsRefresh(w, r)
	case path == "/api/v1/navsettings/recompute-today" && r.Method == http.MethodPost:
		s.handleNavSettingsRecomputeToday(w, r)
	case path == "/api/v1/navsettings/minute-history/delete-day" && r.Method == http.MethodPost:
		s.handleNavSettingsDeleteMinuteHistoryDay(w, r)
	case path == "/api/v1/navsettings/valuation-positions" && r.Method == http.MethodPost:
		s.handleNavSettingsUpdateValuationPosition(w, r)
	case path == "/api/v1/uploads/quotes" && r.Method == http.MethodPost:
		s.handleUploadQuotes(w, r)
	case path == "/api/v1/uploads/holdings" && r.Method == http.MethodPost:
		s.handleUploadHoldings(w, r)
	case path == "/api/v1/minute-history/backfill" && r.Method == http.MethodPost:
		s.handleMinuteHistoryBackfill(w, r)
	case path == "/api/v1/minute-history/reference-backfill" && r.Method == http.MethodPost:
		s.handleMinuteHistoryReferenceBackfill(w, r)
	case path == "/api/v1/minute-history/historical-rebuild" && r.Method == http.MethodPost:
		s.handleMinuteHistoryHistoricalRebuild(w, r)
	case path == "/api/v1/valuation-anchors/prices" && r.Method == http.MethodPost:
		s.handleValuationAnchorPrices(w, r)
	case path == "/api/v1/uploads/quotes/status":
		includeQuotes := r.URL.Query().Get("include_quotes") == "1"
		writeJSON(w, http.StatusOK, s.snapshots.UploadedQuoteStatus(includeQuotes))
	case path == "/api/v1/uploads/quotes/required-symbols":
		s.handleRequiredQuoteSymbols(w, r)
	case path == "/api/v1/uploads/quotes/ws":
		s.handleQuoteWebSocket(w, r)
	case path == "/api/v1/private/intraday-rebuild/ws":
		s.handleIntradayRebuildWebSocket(w, r)
	case path == "/api/v1/debug/status":
		includeQuotes := r.URL.Query().Get("include_quotes") == "1"
		writeJSON(w, http.StatusOK, s.snapshots.DebugStatus(includeQuotes))
	case path == "/api/v1/debug/private-intraday-rebuild/jobs":
		s.handleDebugIntradayRebuildJobs(w, r)
	case path == "/api/v1/refresh" && r.Method == http.MethodPost:
		err := s.snapshots.Refresh(r.Context())
		if err != nil {
			writeJSON(w, http.StatusAccepted, map[string]any{"ok": false, "error": err.Error()})
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{"ok": true})
	case strings.HasPrefix(path, "/woody/res/"):
		s.handleLegacyPage(w, r)
	case path == "/private" || strings.HasPrefix(path, "/private/"):
		s.servePrivateFrontend(w, r)
	case path == "/" || strings.HasPrefix(path, "/funds"):
		s.serveFrontend(w, r)
	default:
		s.serveFrontend(w, r)
	}
}

func (s *Server) handleDataViewAccess(w http.ResponseWriter, r *http.Request) bool {
	if s.dataViewHash == "" {
		return false
	}
	if r.URL.Query().Get("view_lock") == "1" {
		http.SetCookie(w, &http.Cookie{
			Name:     dataViewCookieName,
			Value:    "",
			Path:     "/",
			MaxAge:   -1,
			HttpOnly: true,
			SameSite: http.SameSiteLaxMode,
		})
		if r.Method == http.MethodGet {
			http.Redirect(w, r, cleanDataViewURL(r), http.StatusFound)
			return true
		}
		return false
	}
	key := strings.TrimSpace(r.URL.Query().Get("view_key"))
	if key == "" {
		return false
	}
	if !s.dataViewTokenMatches(key) {
		writeJSON(w, http.StatusForbidden, map[string]any{
			"error": "invalid data view key",
			"code":  "data_view_forbidden",
		})
		return true
	}
	http.SetCookie(w, &http.Cookie{
		Name:     dataViewCookieName,
		Value:    s.dataViewHash,
		Path:     "/",
		MaxAge:   int((12 * time.Hour).Seconds()),
		HttpOnly: true,
		SameSite: http.SameSiteLaxMode,
	})
	if r.Method == http.MethodGet && !strings.HasPrefix(r.URL.Path, "/api/") {
		http.Redirect(w, r, cleanDataViewURL(r), http.StatusFound)
		return true
	}
	return false
}

func (s *Server) handleNavSettingsAccess(w http.ResponseWriter, r *http.Request) bool {
	if s.navSettingsHash == "" {
		return false
	}
	if r.URL.Query().Get("view_lock") == "1" {
		http.SetCookie(w, &http.Cookie{
			Name:     navSettingsCookieName,
			Value:    "",
			Path:     "/",
			MaxAge:   -1,
			HttpOnly: true,
			SameSite: http.SameSiteLaxMode,
		})
		if r.Method == http.MethodGet {
			http.Redirect(w, r, cleanDataViewURL(r), http.StatusFound)
			return true
		}
		return false
	}
	key := strings.TrimSpace(r.URL.Query().Get("view_key"))
	if key == "" {
		return false
	}
	if !s.navSettingsTokenMatches(key) {
		if s.navSettingsRestricted(r.URL.Path) {
			writeJSON(w, http.StatusForbidden, map[string]any{
				"error": "invalid nav settings key",
				"code":  "navsettings_forbidden",
			})
			return true
		}
		return false
	}
	http.SetCookie(w, &http.Cookie{
		Name:     navSettingsCookieName,
		Value:    s.navSettingsHash,
		Path:     "/",
		MaxAge:   int((12 * time.Hour).Seconds()),
		HttpOnly: true,
		SameSite: http.SameSiteLaxMode,
	})
	if r.Method == http.MethodGet && !strings.HasPrefix(r.URL.Path, "/api/") {
		http.Redirect(w, r, cleanDataViewURL(r), http.StatusFound)
		return true
	}
	return false
}

func (s *Server) dataViewRestricted(path string) bool {
	if s.dataViewHash == "" {
		return false
	}
	switch {
	case path == "/api/v1/home",
		path == "/api/v1/branches",
		path == "/api/v1/snapshots/status",
		path == "/api/v1/debug/status",
		path == "/api/v1/visits",
		path == "/api/v1/refresh":
		return true
	case strings.HasPrefix(path, "/api/v1/branches/"):
		return true
	case strings.HasPrefix(path, "/api/v1/funds/"):
		return true
	case strings.HasPrefix(path, "/woody/res/"):
		return true
	default:
		return false
	}
}

func (s *Server) navSettingsRestricted(path string) bool {
	if s.navSettingsHash == "" {
		return false
	}
	return path == "/navsettings" || strings.HasPrefix(path, "/api/v1/navsettings/")
}

func (s *Server) dataViewAuthorized(r *http.Request) bool {
	if s.debugAuthorized(r) {
		return true
	}
	if s.dataViewHash == "" {
		return true
	}
	if s.dataViewTokenMatches(r.URL.Query().Get("view_key")) {
		return true
	}
	if s.dataViewTokenMatches(r.Header.Get("X-Data-View-Token")) {
		return true
	}
	if cookie, err := r.Cookie(dataViewCookieName); err == nil {
		return constantTimeEqual(strings.TrimSpace(cookie.Value), s.dataViewHash)
	}
	return false
}

func (s *Server) navSettingsAuthorized(r *http.Request) bool {
	if s.debugAuthorized(r) {
		return true
	}
	if s.navSettingsHash == "" {
		return true
	}
	if s.navSettingsTokenMatches(r.URL.Query().Get("view_key")) {
		return true
	}
	if s.navSettingsTokenMatches(r.Header.Get("X-Data-View-Token")) {
		return true
	}
	if cookie, err := r.Cookie(navSettingsCookieName); err == nil {
		return constantTimeEqual(strings.TrimSpace(cookie.Value), s.navSettingsHash)
	}
	return false
}

func (s *Server) dataViewTokenMatches(token string) bool {
	token = strings.TrimSpace(token)
	if token == "" || s.dataViewHash == "" {
		return false
	}
	sum := sha256.Sum256([]byte(token))
	return constantTimeEqual(hex.EncodeToString(sum[:]), s.dataViewHash)
}

func (s *Server) navSettingsTokenMatches(token string) bool {
	token = strings.TrimSpace(token)
	if token == "" || s.navSettingsHash == "" {
		return false
	}
	sum := sha256.Sum256([]byte(token))
	return constantTimeEqual(hex.EncodeToString(sum[:]), s.navSettingsHash)
}

func constantTimeEqual(left string, right string) bool {
	if left == "" || right == "" {
		return false
	}
	return subtle.ConstantTimeCompare([]byte(left), []byte(right)) == 1
}

func cleanDataViewURL(r *http.Request) string {
	query := r.URL.Query()
	query.Del("view_key")
	query.Del("view_lock")
	clean := r.URL.Path
	if encoded := query.Encode(); encoded != "" {
		clean += "?" + encoded
	}
	if r.URL.Fragment != "" {
		clean += "#" + r.URL.Fragment
	}
	return clean
}

func (s *Server) writeDataViewLocked(w http.ResponseWriter, r *http.Request) {
	if strings.HasPrefix(r.URL.Path, "/woody/res/") {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.WriteHeader(http.StatusServiceUnavailable)
		_, _ = w.Write([]byte(`<!doctype html><html><head><meta charset="utf-8"><title>数据暂时隐藏</title></head><body><main style="font-family:sans-serif;max-width:720px;margin:48px auto;line-height:1.7"><h1>数据暂时隐藏</h1><p>当前估值数据正在校验，普通入口暂不展示。请使用调试入口打开。</p></main></body></html>`))
		return
	}
	writeJSON(w, http.StatusServiceUnavailable, map[string]any{
		"error": "当前估值数据正在校验，普通入口暂不展示。请使用调试入口打开。",
		"code":  "data_view_locked",
	})
}

func (s *Server) writeNavSettingsLocked(w http.ResponseWriter, r *http.Request) {
	if strings.HasPrefix(r.URL.Path, "/api/") {
		writeJSON(w, http.StatusForbidden, map[string]any{
			"error": "nav settings page requires debug access",
			"code":  "navsettings_locked",
		})
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.WriteHeader(http.StatusForbidden)
	_, _ = w.Write([]byte(`<!doctype html><html><head><meta charset="utf-8"><title>Nav Settings Locked</title></head><body><main style="font-family:sans-serif;max-width:720px;margin:48px auto;line-height:1.7"><h1>Nav Settings 已锁定</h1><p>该页面仅用于调试。请携带授权 key 打开。</p></main></body></html>`))
}

type trackVisitRequest struct {
	Kind   string `json:"kind"`
	Target string `json:"target"`
}

func (s *Server) handleTrackVisit(w http.ResponseWriter, r *http.Request) {
	var req trackVisitRequest
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096))
	if err := decoder.Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	target := sanitizeVisitTarget(req.Target)
	if target == "" {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "target is required"})
		return
	}
	kind := sanitizeVisitKind(req.Kind)
	if kind == "" {
		kind = "page"
	}
	if kind != "page" {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "unsupported visit kind"})
		return
	}
	s.recordVisit(kind, target, "")
	writeJSON(w, http.StatusAccepted, map[string]any{"ok": true})
}

func (s *Server) handleVisitCounts(w http.ResponseWriter, r *http.Request) {
	if s.visits == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"error": "visit count repository unavailable"})
		return
	}
	days := 30
	if raw := strings.TrimSpace(r.URL.Query().Get("days")); raw != "" {
		if parsed, err := strconv.Atoi(raw); err == nil {
			days = parsed
		}
	}
	if days <= 0 {
		days = 30
	}
	if days > 366 {
		days = 366
	}
	rows, err := s.visits.LoadVisitCounts(r.Context(), days)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, domain.VisitCountResponse{Days: days, Rows: rows})
}

func (s *Server) recordVisit(kind string, target string, method string) {
	if s.visits == nil {
		return
	}
	kind = sanitizeVisitKind(kind)
	target = sanitizeVisitTarget(target)
	method = sanitizeVisitMethod(method)
	if kind == "" || target == "" {
		return
	}
	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		if err := s.visits.IncrementVisitCount(ctx, time.Now(), kind, target, method); err != nil {
			slog.Warn("record visit count failed", "kind", kind, "target", target, "error", err)
		}
	}()
}

func sanitizeVisitKind(value string) string {
	value = strings.ToLower(strings.TrimSpace(value))
	switch value {
	case "page", "api":
		return value
	default:
		return ""
	}
}

func sanitizeVisitMethod(value string) string {
	value = strings.ToUpper(strings.TrimSpace(value))
	switch value {
	case "GET", "POST", "PUT", "PATCH", "DELETE", "HEAD":
		return value
	default:
		return ""
	}
}

func sanitizeVisitTarget(value string) string {
	value = strings.TrimSpace(value)
	if value == "" {
		return ""
	}
	if len(value) > 255 {
		value = value[:255]
	}
	return value
}

func normalizeAPITarget(path string) string {
	if !strings.HasPrefix(path, "/api/") {
		return ""
	}
	switch {
	case path == "/api/v1/visits/track":
		return ""
	case path == "/api/v1/health":
		return ""
	case path == "/api/v1/contact-messages":
		return "/api/v1/contact-messages"
	case path == "/api/v1/admin/contact-messages":
		return "/api/v1/admin/contact-messages"
	case path == "/api/v1/home":
		return "/api/v1/home"
	case path == "/api/v1/branches":
		return "/api/v1/branches"
	case path == "/api/v1/private/funds":
		return "/api/v1/private/funds"
	case strings.HasPrefix(path, "/api/v1/private/funds/"):
		return "/api/v1/private/funds/:symbol"
	case strings.HasPrefix(path, "/api/v1/private/inputs/"):
		return ""
	case path == "/api/v1/funds/yesterday-redemption-board":
		return "/api/v1/funds/yesterday-redemption-board"
	case strings.HasPrefix(path, "/api/v1/branches/"):
		return "/api/v1/branches/:key"
	case strings.HasPrefix(path, "/api/v1/funds/") && strings.HasSuffix(path, "/effective-ratio-history"):
		return "/api/v1/funds/:symbol/effective-ratio-history"
	case strings.HasPrefix(path, "/api/v1/funds/") && strings.HasSuffix(path, "/share-history"):
		return "/api/v1/funds/:symbol/share-history"
	case strings.HasPrefix(path, "/api/v1/funds/") && strings.HasSuffix(path, "/minute-history/dates"):
		return "/api/v1/funds/:symbol/minute-history/dates"
	case strings.HasPrefix(path, "/api/v1/funds/") && strings.HasSuffix(path, "/minute-history"):
		return "/api/v1/funds/:symbol/minute-history"
	case strings.HasPrefix(path, "/api/v1/funds/"):
		return "/api/v1/funds/:symbol"
	case path == "/api/v1/snapshots/status":
		return "/api/v1/snapshots/status"
	case path == "/api/v1/navsettings/status":
		return "/api/v1/navsettings/status"
	case path == "/api/v1/navsettings/refresh":
		return "/api/v1/navsettings/refresh"
	case path == "/api/v1/navsettings/recompute-today":
		return "/api/v1/navsettings/recompute-today"
	case path == "/api/v1/navsettings/valuation-positions":
		return "/api/v1/navsettings/valuation-positions"
	case path == "/api/v1/uploads/quotes":
		return "/api/v1/uploads/quotes"
	case path == "/api/v1/uploads/holdings":
		return "/api/v1/uploads/holdings"
	case path == "/api/v1/minute-history/backfill":
		return "/api/v1/minute-history/backfill"
	case path == "/api/v1/minute-history/reference-backfill":
		return "/api/v1/minute-history/reference-backfill"
	case path == "/api/v1/minute-history/historical-rebuild":
		return "/api/v1/minute-history/historical-rebuild"
	case path == "/api/v1/valuation-anchors/prices":
		return "/api/v1/valuation-anchors/prices"
	case path == "/api/v1/uploads/quotes/status":
		return "/api/v1/uploads/quotes/status"
	case path == "/api/v1/uploads/quotes/required-symbols":
		return "/api/v1/uploads/quotes/required-symbols"
	case path == "/api/v1/uploads/quotes/ws":
		return "/api/v1/uploads/quotes/ws"
	case path == "/api/v1/debug/status":
		return "/api/v1/debug/status"
	case path == "/api/v1/refresh":
		return "/api/v1/refresh"
	case path == "/api/v1/visits":
		return "/api/v1/visits"
	default:
		return "/api/unknown"
	}
}

func (s *Server) handleNavSettingsRefresh(w http.ResponseWriter, r *http.Request) {
	err := s.snapshots.Refresh(r.Context())
	if err != nil {
		writeJSON(w, http.StatusAccepted, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true})
}

func (s *Server) handleNavSettingsRecomputeToday(w http.ResponseWriter, r *http.Request) {
	result, err := s.snapshots.RecomputeTodayMinuteHistory(time.Now())
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, result)
}

func (s *Server) handleRequiredQuoteSymbols(w http.ResponseWriter, r *http.Request) {
	if !s.authorizeUpload(r) {
		writeJSON(w, http.StatusUnauthorized, map[string]any{"error": "upload token required"})
		return
	}
	writeJSON(w, http.StatusOK, s.snapshots.RequiredQuoteSymbols())
}

func (s *Server) handleBranch(w http.ResponseWriter, key string) {
	snapshot, ok := s.snapshots.BranchSnapshot(key)
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]any{"error": "branch not found"})
		return
	}
	w.Header().Set("Cache-Control", "public, max-age=5, stale-while-revalidate=30")
	writeJSON(w, http.StatusOK, domain.NewBranchListSnapshot(snapshot))
}

func (s *Server) handleYesterdayRedemptionBoard(w http.ResponseWriter, r *http.Request) {
	payload, err := s.snapshots.YesterdayRedemptionBoard(r.Context())
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	s.cachePrivateShareChanges(payload)
	w.Header().Set("Cache-Control", "public, max-age=30, stale-while-revalidate=120")
	writeJSON(w, http.StatusOK, payload)
}

func (s *Server) handleFund(w http.ResponseWriter, symbol string) {
	snapshot, ok := s.snapshots.FundSnapshot(symbol)
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]any{"error": "fund not found"})
		return
	}
	w.Header().Set("Cache-Control", "public, max-age=5, stale-while-revalidate=30")
	writeJSON(w, http.StatusOK, snapshot)
}

func (s *Server) handleFundMinuteHistory(w http.ResponseWriter, r *http.Request) {
	rest := strings.TrimPrefix(r.URL.Path, "/api/v1/funds/")
	symbol := strings.TrimSuffix(rest, "/minute-history")
	symbol = strings.ToUpper(strings.Trim(symbol, "/"))
	if symbol == "" || strings.Contains(symbol, "/") {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid symbol"})
		return
	}
	if len(domain.BranchesForSymbol(symbol)) == 0 {
		writeJSON(w, http.StatusNotFound, map[string]any{"error": "fund not found"})
		return
	}
	if rawDate := strings.TrimSpace(r.URL.Query().Get("date")); rawDate != "" {
		payload, err := s.snapshots.MinuteHistoryForDate(symbol, rawDate)
		if err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
			return
		}
		if payload.Date == "" {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid date"})
			return
		}
		w.Header().Set("Cache-Control", "public, max-age=30, stale-while-revalidate=120")
		writeJSON(w, http.StatusOK, payload)
		return
	}
	days := 2
	if raw := strings.TrimSpace(r.URL.Query().Get("days")); raw != "" {
		if parsed, err := strconv.Atoi(raw); err == nil {
			days = parsed
		}
	}
	payload, err := s.snapshots.MinuteHistory(symbol, days)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	w.Header().Set("Cache-Control", "public, max-age=5, stale-while-revalidate=30")
	writeJSON(w, http.StatusOK, payload)
}

func (s *Server) handleFundMinuteHistoryDates(w http.ResponseWriter, r *http.Request) {
	rest := strings.TrimPrefix(r.URL.Path, "/api/v1/funds/")
	symbol := strings.TrimSuffix(rest, "/minute-history/dates")
	symbol = strings.ToUpper(strings.Trim(symbol, "/"))
	if symbol == "" || strings.Contains(symbol, "/") {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid symbol"})
		return
	}
	if len(domain.BranchesForSymbol(symbol)) == 0 {
		writeJSON(w, http.StatusNotFound, map[string]any{"error": "fund not found"})
		return
	}
	limit := 45
	if raw := strings.TrimSpace(r.URL.Query().Get("limit")); raw != "" {
		if parsed, err := strconv.Atoi(raw); err == nil {
			limit = parsed
		}
	}
	payload, err := s.snapshots.MinuteHistoryDates(symbol, limit)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	w.Header().Set("Cache-Control", "public, max-age=30, stale-while-revalidate=120")
	writeJSON(w, http.StatusOK, payload)
}

func (s *Server) handleFundEffectiveRatioHistory(w http.ResponseWriter, r *http.Request) {
	rest := strings.TrimPrefix(r.URL.Path, "/api/v1/funds/")
	symbol := strings.TrimSuffix(rest, "/effective-ratio-history")
	symbol = strings.ToUpper(strings.Trim(symbol, "/"))
	if symbol == "" || strings.Contains(symbol, "/") {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid symbol"})
		return
	}
	if len(domain.BranchesForSymbol(symbol)) == 0 {
		writeJSON(w, http.StatusNotFound, map[string]any{"error": "fund not found"})
		return
	}
	days := 120
	if raw := strings.TrimSpace(r.URL.Query().Get("days")); raw != "" {
		if parsed, err := strconv.Atoi(raw); err == nil {
			days = parsed
		}
	}
	payload, err := s.snapshots.EffectiveRatioFitHistory(r.Context(), symbol, days)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	w.Header().Set("Cache-Control", "public, max-age=30, stale-while-revalidate=120")
	writeJSON(w, http.StatusOK, payload)
}

func (s *Server) handleFundShareHistory(w http.ResponseWriter, r *http.Request) {
	rest := strings.TrimPrefix(r.URL.Path, "/api/v1/funds/")
	symbol := strings.TrimSuffix(rest, "/share-history")
	symbol = strings.ToUpper(strings.Trim(symbol, "/"))
	if symbol == "" || strings.Contains(symbol, "/") {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid symbol"})
		return
	}
	if len(domain.BranchesForSymbol(symbol)) == 0 {
		writeJSON(w, http.StatusNotFound, map[string]any{"error": "fund not found"})
		return
	}
	if !strings.HasPrefix(symbol, "SZ") && !strings.HasPrefix(symbol, "SH") {
		writeJSON(w, http.StatusOK, domain.ShareHistoryResponse{
			Symbol: symbol,
			Unit:   "万份",
			Rows:   []domain.ShareHistoryRecord{},
		})
		return
	}
	days := defaultShareHistoryRows
	if raw := strings.TrimSpace(r.URL.Query().Get("days")); raw != "" {
		if parsed, err := strconv.Atoi(raw); err == nil {
			days = parsed
		}
	}
	if days <= 0 {
		days = defaultShareHistoryRows
	}
	payload, err := s.snapshots.ShareHistory(r.Context(), symbol, days)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	w.Header().Set("Cache-Control", "public, max-age=30, stale-while-revalidate=120")
	writeJSON(w, http.StatusOK, payload)
}

type uploadQuotesRequest struct {
	Source string            `json:"source"`
	Quotes []uploadQuoteItem `json:"quotes"`
}

type uploadQuoteItem struct {
	Symbol       string         `json:"symbol"`
	Name         string         `json:"name"`
	Price        float64        `json:"price"`
	PrevClose    float64        `json:"prev_close"`
	Open         float64        `json:"open"`
	High         float64        `json:"high"`
	Low          float64        `json:"low"`
	Volume       float64        `json:"volume"`
	Amount       float64        `json:"amount"`
	ChangePct    float64        `json:"change_pct"`
	LimitUp      float64        `json:"limit_up"`
	LimitDown    float64        `json:"limit_down"`
	BidLevels    []domain.Level `json:"bid_levels"`
	AskLevels    []domain.Level `json:"ask_levels"`
	QuoteDate    string         `json:"quote_date"`
	QuoteTime    string         `json:"quote_time"`
	Source       string         `json:"source"`
	SourceSymbol string         `json:"source_symbol"`
	QuoteSession string         `json:"quote_session"`
}

type uploadHoldingsRequest struct {
	Source      string              `json:"source"`
	FundSymbol  string              `json:"fund_symbol"`
	HoldingDate string              `json:"holding_date"`
	Position    float64             `json:"position"`
	Holdings    []uploadHoldingItem `json:"holdings"`
	Rows        []uploadHoldingItem `json:"rows"`
}

type uploadHoldingItem struct {
	Symbol   string  `json:"symbol"`
	Name     string  `json:"name"`
	Ratio    float64 `json:"ratio"`
	FXAdjust float64 `json:"fx_adjust"`
	Currency string  `json:"currency"`
}

type minuteHistoryBackfillRequest struct {
	Source string               `json:"source"`
	Rows   []snapshot.MinuteBar `json:"rows"`
	Bars   []snapshot.MinuteBar `json:"bars"`
}

type minuteHistoryHistoricalRebuildRequest struct {
	Source        string               `json:"source"`
	Rows          []snapshot.MinuteBar `json:"rows"`
	Bars          []snapshot.MinuteBar `json:"bars"`
	FundRows      []snapshot.MinuteBar `json:"fund_rows"`
	ReferenceRows []snapshot.MinuteBar `json:"reference_rows"`
	ReferenceBars []snapshot.MinuteBar `json:"reference_bars"`
}

type valuationAnchorPricesRequest struct {
	Source string                       `json:"source"`
	Prices []valuationAnchorPriceUpload `json:"prices"`
	Rows   []valuationAnchorPriceUpload `json:"rows"`
}

type valuationAnchorPriceUpload struct {
	FundSymbol      string  `json:"fund_symbol"`
	AnchorDate      string  `json:"anchor_date"`
	AnchorKey       string  `json:"anchor_key"`
	ReferenceSymbol string  `json:"reference_symbol"`
	TargetAt        string  `json:"target_at"`
	TargetTimezone  string  `json:"target_timezone"`
	ObservedAt      string  `json:"observed_at"`
	Price           float64 `json:"price"`
	Weight          float64 `json:"weight"`
	Source          string  `json:"source"`
	CaptureStatus   string  `json:"capture_status"`
}

func (s *Server) handleUploadQuotes(w http.ResponseWriter, r *http.Request) {
	if !s.authorizeUpload(r) {
		writeJSON(w, http.StatusUnauthorized, map[string]any{"error": "upload token required"})
		return
	}

	var req uploadQuotesRequest
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	if len(req.Quotes) == 0 {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "quotes is required"})
		return
	}
	if len(req.Quotes) > 500 {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "quotes limit is 500 per request"})
		return
	}

	quotes := uploadItemsToQuotes(req.Quotes, time.Now())
	accepted, warnings := s.snapshots.UpsertUploadedQuotes(req.Source, quotes)
	writeJSON(w, http.StatusAccepted, map[string]any{
		"ok":       accepted > 0,
		"accepted": accepted,
		"warnings": warnings,
		"enabled":  s.snapshots.UploadedQuoteStatus(false).Enabled,
		"note":     "uploaded quotes are cached; they affect valuation only when ENABLE_UPLOAD_QUOTES=true",
	})
}

func (s *Server) handleUploadHoldings(w http.ResponseWriter, r *http.Request) {
	if !s.authorizeUpload(r) {
		writeJSON(w, http.StatusUnauthorized, map[string]any{"error": "upload token required"})
		return
	}

	var req uploadHoldingsRequest
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4<<20))
	if err := decoder.Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	rows := req.Holdings
	if len(rows) == 0 {
		rows = req.Rows
	}
	if len(rows) == 0 {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "holdings is required"})
		return
	}
	if len(rows) > 2000 {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "holdings limit is 2000 per request"})
		return
	}

	accepted, err := s.snapshots.UpsertUploadedHoldingSnapshot(
		r.Context(),
		req.Source,
		req.FundSymbol,
		req.HoldingDate,
		req.Position,
		uploadHoldingsToDomain(rows),
	)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusAccepted, map[string]any{"ok": accepted > 0, "accepted": accepted})
}

func (s *Server) handleMinuteHistoryBackfill(w http.ResponseWriter, r *http.Request) {
	if !s.authorizeUpload(r) {
		writeJSON(w, http.StatusUnauthorized, map[string]any{"error": "upload token required"})
		return
	}

	var req minuteHistoryBackfillRequest
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 32<<20))
	if err := decoder.Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	rows := req.Rows
	if len(rows) == 0 {
		rows = req.Bars
	}
	if len(rows) == 0 {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "rows is required"})
		return
	}
	if len(rows) > 100000 {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "rows limit is 100000 per request"})
		return
	}

	result, err := s.snapshots.BackfillMinuteBars(req.Source, rows)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusAccepted, result)
}

func (s *Server) handleMinuteHistoryReferenceBackfill(w http.ResponseWriter, r *http.Request) {
	if !s.authorizeUpload(r) {
		writeJSON(w, http.StatusUnauthorized, map[string]any{"error": "upload token required"})
		return
	}

	var req minuteHistoryBackfillRequest
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 64<<20))
	if err := decoder.Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	rows := req.Rows
	if len(rows) == 0 {
		rows = req.Bars
	}
	if len(rows) == 0 {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "rows is required"})
		return
	}
	if len(rows) > 300000 {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "rows limit is 300000 per request"})
		return
	}

	result, err := s.snapshots.BackfillReferenceMinuteBars(req.Source, rows)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusAccepted, result)
}

func (s *Server) handleMinuteHistoryHistoricalRebuild(w http.ResponseWriter, r *http.Request) {
	if !s.authorizeUpload(r) {
		writeJSON(w, http.StatusUnauthorized, map[string]any{"error": "upload token required"})
		return
	}

	var req minuteHistoryHistoricalRebuildRequest
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 128<<20))
	if err := decoder.Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	fundRows := req.FundRows
	if len(fundRows) == 0 {
		fundRows = req.Rows
	}
	if len(fundRows) == 0 {
		fundRows = req.Bars
	}
	referenceRows := req.ReferenceRows
	if len(referenceRows) == 0 {
		referenceRows = req.ReferenceBars
	}
	if len(fundRows) == 0 {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "fund_rows is required"})
		return
	}
	if len(fundRows) > 300000 {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "fund_rows limit is 300000 per request"})
		return
	}
	if len(referenceRows) > 300000 {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "reference_rows limit is 300000 per request"})
		return
	}

	result, err := s.snapshots.RebuildHistoricalMinuteBars(req.Source, fundRows, referenceRows)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusAccepted, result)
}

func (s *Server) handleValuationAnchorPrices(w http.ResponseWriter, r *http.Request) {
	if !s.authorizeUpload(r) {
		writeJSON(w, http.StatusUnauthorized, map[string]any{"error": "upload token required"})
		return
	}

	var req valuationAnchorPricesRequest
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 16<<20))
	if err := decoder.Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	rows := req.Prices
	if len(rows) == 0 {
		rows = req.Rows
	}
	if len(rows) == 0 {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "prices is required"})
		return
	}
	if len(rows) > 10000 {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "prices limit is 10000 per request"})
		return
	}

	result, err := s.snapshots.UpsertValuationAnchorPrices(r.Context(), req.Source, valuationAnchorUploadsToDomain(rows))
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusAccepted, result)
}

func (s *Server) authorizeUpload(r *http.Request) bool {
	if s.uploadToken == "" {
		return false
	}
	if strings.TrimSpace(r.URL.Query().Get("token")) == s.uploadToken {
		return true
	}
	if r.Header.Get("X-Upload-Token") == s.uploadToken {
		return true
	}
	auth := strings.TrimSpace(r.Header.Get("Authorization"))
	if strings.HasPrefix(auth, "Bearer ") && strings.TrimSpace(strings.TrimPrefix(auth, "Bearer ")) == s.uploadToken {
		return true
	}
	return false
}

func (s *Server) authorizeIntradayRebuildAgent(r *http.Request) bool {
	if s.intradayAgentHash == "" {
		return false
	}
	token := strings.TrimSpace(r.Header.Get("X-Intraday-Rebuild-Token"))
	if token == "" {
		return false
	}
	sum := sha256.Sum256([]byte(token))
	return constantTimeEqual(hex.EncodeToString(sum[:]), s.intradayAgentHash)
}

func uploadItemsToQuotes(items []uploadQuoteItem, now time.Time) []domain.Quote {
	quotes := make([]domain.Quote, 0, len(items))
	for _, item := range items {
		quotes = append(quotes, domain.Quote{
			Symbol:       item.Symbol,
			Name:         item.Name,
			Price:        item.Price,
			PrevClose:    item.PrevClose,
			Open:         item.Open,
			High:         item.High,
			Low:          item.Low,
			Volume:       item.Volume,
			Amount:       item.Amount,
			ChangePct:    item.ChangePct,
			LimitUp:      item.LimitUp,
			LimitDown:    item.LimitDown,
			BidLevels:    item.BidLevels,
			AskLevels:    item.AskLevels,
			QuoteDate:    item.QuoteDate,
			QuoteTime:    item.QuoteTime,
			Source:       item.Source,
			SourceSymbol: item.SourceSymbol,
			QuoteSession: item.QuoteSession,
			FetchedAt:    now,
		})
	}
	return quotes
}

func uploadHoldingsToDomain(items []uploadHoldingItem) []domain.Holding {
	holdings := make([]domain.Holding, 0, len(items))
	for _, item := range items {
		holdings = append(holdings, domain.Holding{
			HoldingSymbol: item.Symbol,
			HoldingName:   item.Name,
			Ratio:         item.Ratio,
			FXAdjust:      item.FXAdjust,
			Currency:      item.Currency,
		})
	}
	return holdings
}

func valuationAnchorUploadsToDomain(items []valuationAnchorPriceUpload) []domain.ValuationAnchorPrice {
	prices := make([]domain.ValuationAnchorPrice, 0, len(items))
	for _, item := range items {
		prices = append(prices, domain.ValuationAnchorPrice{
			FundSymbol:      item.FundSymbol,
			AnchorDate:      item.AnchorDate,
			AnchorKey:       item.AnchorKey,
			ReferenceSymbol: item.ReferenceSymbol,
			TargetAt:        parseUploadTime(item.TargetAt),
			TargetTimezone:  item.TargetTimezone,
			ObservedAt:      parseUploadTime(item.ObservedAt),
			Price:           item.Price,
			Weight:          item.Weight,
			Source:          item.Source,
			CaptureStatus:   item.CaptureStatus,
		})
	}
	return prices
}

func parseUploadTime(value string) time.Time {
	value = strings.TrimSpace(value)
	if value == "" {
		return time.Time{}
	}
	if parsed, err := time.Parse(time.RFC3339Nano, value); err == nil {
		return parsed
	}
	if parsed, err := time.Parse("2006-01-02 15:04:05", value); err == nil {
		return parsed
	}
	return time.Time{}
}

func (s *Server) handleLegacyPage(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path == "/woody/res/chinaindexcn.php" {
		http.Error(w, "page removed", http.StatusGone)
		return
	}
	branch, ok := domain.BranchByOldPath(r.URL.Path)
	if !ok {
		s.serveFrontend(w, r)
		return
	}
	snapshot, ok := s.snapshots.BranchSnapshot(branch.Key)
	if !ok {
		http.Error(w, "snapshot not found", http.StatusNotFound)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Cache-Control", "public, max-age=5, stale-while-revalidate=30")
	_, _ = w.Write([]byte(snapshot2HTML(snapshot)))
}

func snapshot2HTML(snap domain.BranchSnapshot) string {
	return snapshot.RenderBranchHTML(snap)
}

func (s *Server) serveFrontend(w http.ResponseWriter, r *http.Request) {
	if isContactAdminInboxPath(r.URL.Path) {
		w.Header().Set("X-Robots-Tag", "noindex, nofollow")
		w.Header().Set("Cache-Control", "private, no-store")
	}
	cleanPath := filepath.Clean("/" + strings.TrimPrefix(r.URL.Path, "/"))
	candidate := filepath.Join(s.frontendDist, strings.TrimPrefix(cleanPath, "/"))
	if stat, err := os.Stat(candidate); err == nil && !stat.IsDir() {
		if w.Header().Get("Cache-Control") == "" {
			if strings.HasPrefix(cleanPath, "/assets/") {
				w.Header().Set("Cache-Control", "public, max-age=31536000, immutable")
			} else {
				w.Header().Set("Cache-Control", "public, max-age=300, stale-while-revalidate=600")
			}
		}
		s.fileServer.ServeHTTP(w, r)
		return
	}

	index := filepath.Join(s.frontendDist, "index.html")
	if _, err := os.Stat(index); err == nil {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		if w.Header().Get("Cache-Control") == "" {
			w.Header().Set("Cache-Control", "public, max-age=60, stale-while-revalidate=300")
		}
		http.ServeFile(w, r, index)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"message": "frontend/dist not found; run npm install && npm run build in frontend, or use Vite dev server",
		"api":     "/api/v1/branches",
	})
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}
