package web

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"net/http"
	"strings"
	"sync"
	"time"
)

const (
	debugSessionCookieName   = "__nnn_debug_session"
	defaultDebugUsername     = "Dirac"
	defaultDebugPassword     = "727272"
	debugSessionTTL          = 12 * time.Hour
	maxDebugLoginRequestSize = 8 << 10
)

type debugLoginRequest struct {
	Username string `json:"username"`
	Password string `json:"password"`
}

type debugAuthStatusResponse struct {
	Authenticated bool   `json:"authenticated"`
	AuthMode      string `json:"auth_mode,omitempty"`
	Username      string `json:"username,omitempty"`
	ExpiresAt     string `json:"expires_at,omitempty"`
}

type debugSession struct {
	Username  string
	ExpiresAt time.Time
}

type debugAccessManager struct {
	mu           sync.Mutex
	username     string
	passwordHash string
	sessions     map[string]debugSession
}

func newDebugAccessManager(username string, password string) *debugAccessManager {
	username = strings.TrimSpace(username)
	if username == "" {
		username = defaultDebugUsername
	}
	if password == "" {
		password = defaultDebugPassword
	}
	return &debugAccessManager{
		username:     username,
		passwordHash: hashDebugPassword(password),
		sessions:     make(map[string]debugSession),
	}
}

func (m *debugAccessManager) login(username string, password string, now time.Time) (string, debugSession, bool) {
	if m == nil {
		return "", debugSession{}, false
	}
	username = strings.TrimSpace(username)
	password = strings.TrimSpace(password)
	if !constantTimeEqual(username, m.username) || !constantTimeEqual(hashDebugPassword(password), m.passwordHash) {
		return "", debugSession{}, false
	}
	token, err := newDebugSessionToken()
	if err != nil {
		return "", debugSession{}, false
	}
	session := debugSession{
		Username:  m.username,
		ExpiresAt: now.Add(debugSessionTTL),
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	m.pruneExpiredLocked(now)
	m.sessions[token] = session
	return token, session, true
}

func (m *debugAccessManager) authorize(token string, now time.Time) (debugSession, bool) {
	if m == nil {
		return debugSession{}, false
	}
	token = strings.TrimSpace(token)
	if token == "" {
		return debugSession{}, false
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	session, ok := m.sessions[token]
	if !ok {
		return debugSession{}, false
	}
	if !session.ExpiresAt.After(now) {
		delete(m.sessions, token)
		return debugSession{}, false
	}
	return session, true
}

func (m *debugAccessManager) revoke(token string) {
	if m == nil {
		return
	}
	token = strings.TrimSpace(token)
	if token == "" {
		return
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	delete(m.sessions, token)
}

func (m *debugAccessManager) pruneExpiredLocked(now time.Time) {
	for token, session := range m.sessions {
		if !session.ExpiresAt.After(now) {
			delete(m.sessions, token)
		}
	}
}

func hashDebugPassword(password string) string {
	sum := sha256.Sum256([]byte(password))
	return hex.EncodeToString(sum[:])
}

func newDebugSessionToken() (string, error) {
	buf := make([]byte, 32)
	if _, err := rand.Read(buf); err != nil {
		return "", err
	}
	return hex.EncodeToString(buf), nil
}

func (s *Server) handleDebugAuthStatus(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "private, no-store")
	writeJSON(w, http.StatusOK, s.debugAuthStatus(r, time.Now()))
}

func (s *Server) handleDebugLogin(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "private, no-store")
	if s.debugAccess == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"error": "debug access is unavailable"})
		return
	}
	var req debugLoginRequest
	if err := decodeStrictJSONBody(w, r, maxDebugLoginRequestSize, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	now := time.Now()
	token, session, ok := s.debugAccess.login(req.Username, req.Password, now)
	if !ok {
		writeJSON(w, http.StatusUnauthorized, map[string]any{
			"error": "invalid debug credentials",
			"code":  "debug_login_failed",
		})
		return
	}
	setDebugSessionCookie(w, token, session.ExpiresAt)
	writeJSON(w, http.StatusOK, debugAuthStatusResponse{
		Authenticated: true,
		AuthMode:      "debug_session",
		Username:      session.Username,
		ExpiresAt:     session.ExpiresAt.Format(time.RFC3339Nano),
	})
}

func (s *Server) handleDebugLogout(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "private, no-store")
	if cookie, err := r.Cookie(debugSessionCookieName); err == nil && s.debugAccess != nil {
		s.debugAccess.revoke(cookie.Value)
	}
	clearCookie(w, debugSessionCookieName)
	clearCookie(w, dataViewCookieName)
	clearCookie(w, navSettingsCookieName)
	writeJSON(w, http.StatusOK, debugAuthStatusResponse{})
}

func (s *Server) debugAuthStatus(r *http.Request, now time.Time) debugAuthStatusResponse {
	if session, ok := s.debugSession(r, now); ok {
		return debugAuthStatusResponse{
			Authenticated: true,
			AuthMode:      "debug_session",
			Username:      session.Username,
			ExpiresAt:     session.ExpiresAt.Format(time.RFC3339Nano),
		}
	}
	if s.dataViewCredentialAuthorized(r) || s.navSettingsCredentialAuthorized(r) {
		return debugAuthStatusResponse{
			Authenticated: true,
			AuthMode:      "legacy_token",
			Username:      "legacy-token",
		}
	}
	return debugAuthStatusResponse{}
}

func (s *Server) debugSession(r *http.Request, now time.Time) (debugSession, bool) {
	if s.debugAccess == nil {
		return debugSession{}, false
	}
	cookie, err := r.Cookie(debugSessionCookieName)
	if err != nil {
		return debugSession{}, false
	}
	return s.debugAccess.authorize(cookie.Value, now)
}

func (s *Server) debugAuthorized(r *http.Request) bool {
	_, ok := s.debugSession(r, time.Now())
	return ok
}

func (s *Server) debugActorName(r *http.Request) string {
	if session, ok := s.debugSession(r, time.Now()); ok && strings.TrimSpace(session.Username) != "" {
		return session.Username
	}
	if s.dataViewCredentialAuthorized(r) || s.navSettingsCredentialAuthorized(r) {
		return "legacy-token"
	}
	return "debug"
}

func (s *Server) debugAccessRestricted(path string) bool {
	switch {
	case path == "/api/v1/debug/status":
		return true
	case strings.HasPrefix(path, "/api/v1/debug/private-intraday-rebuild/"):
		return true
	case strings.HasPrefix(path, "/api/v1/navsettings/"):
		return true
	default:
		return false
	}
}

func (s *Server) debugAccessAuthorized(r *http.Request) bool {
	if s.debugAuthorized(r) {
		return true
	}
	return s.dataViewCredentialAuthorized(r) || s.navSettingsCredentialAuthorized(r)
}

func (s *Server) dataViewCredentialAuthorized(r *http.Request) bool {
	if s.dataViewHash == "" {
		return false
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

func (s *Server) navSettingsCredentialAuthorized(r *http.Request) bool {
	if s.navSettingsHash == "" {
		return false
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

func (s *Server) writeDebugAccessLocked(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "private, no-store")
	writeJSON(w, http.StatusUnauthorized, map[string]any{
		"error": "debug access requires login",
		"code":  "debug_login_required",
	})
}

func setDebugSessionCookie(w http.ResponseWriter, token string, expiresAt time.Time) {
	http.SetCookie(w, &http.Cookie{
		Name:     debugSessionCookieName,
		Value:    token,
		Path:     "/",
		MaxAge:   int(debugSessionTTL.Seconds()),
		Expires:  expiresAt,
		HttpOnly: true,
		SameSite: http.SameSiteLaxMode,
	})
}

func clearCookie(w http.ResponseWriter, name string) {
	http.SetCookie(w, &http.Cookie{
		Name:     name,
		Value:    "",
		Path:     "/",
		MaxAge:   -1,
		Expires:  time.Unix(0, 0),
		HttpOnly: true,
		SameSite: http.SameSiteLaxMode,
	})
}
