package web

import (
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/mail"
	"net/netip"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode"
	"unicode/utf8"

	"newnavnav/internal/domain"
)

const (
	messageAdminCookieName = "__nnn_message_admin"
	contactAdminPath       = "/contact-admin"
	contactAdminInboxPath  = "/contact-admin/inbox"
)

const (
	maxContactMessageRequestBytes = 16 << 10
	maxContactMessageRunes        = 4000
	maxContactEmailLength         = 320
	defaultContactMessageLimit    = 100
	maxContactMessageLimit        = 200
)

type ContactMessageRepository interface {
	CreateContactMessage(ctx context.Context, email string, message string) error
	LoadContactMessages(ctx context.Context, limit int) ([]domain.ContactMessage, error)
}

type createContactMessageRequest struct {
	Email   string `json:"email"`
	Message string `json:"message"`
}

type contactMessageListResponse struct {
	Limit int                     `json:"limit"`
	Rows  []domain.ContactMessage `json:"rows"`
}

type contactMessageLimiter struct {
	mu      sync.Mutex
	window  time.Duration
	limit   int
	attempt map[string][]time.Time
}

func newContactMessageLimiter(limit int, window time.Duration) *contactMessageLimiter {
	if limit <= 0 {
		limit = 3
	}
	if window <= 0 {
		window = 10 * time.Minute
	}
	return &contactMessageLimiter{
		window:  window,
		limit:   limit,
		attempt: make(map[string][]time.Time),
	}
}

func (l *contactMessageLimiter) Allow(key string, now time.Time) (bool, time.Duration) {
	if l == nil || strings.TrimSpace(key) == "" {
		return true, 0
	}

	l.mu.Lock()
	defer l.mu.Unlock()

	cutoff := now.Add(-l.window)
	existing := l.attempt[key]
	filtered := existing[:0]
	for _, attemptAt := range existing {
		if attemptAt.After(cutoff) {
			filtered = append(filtered, attemptAt)
		}
	}
	if len(filtered) >= l.limit {
		retryAfter := filtered[0].Add(l.window).Sub(now)
		if retryAfter < time.Second {
			retryAfter = time.Second
		}
		l.attempt[key] = append([]time.Time(nil), filtered...)
		return false, retryAfter
	}
	filtered = append(filtered, now)
	l.attempt[key] = append([]time.Time(nil), filtered...)
	return true, 0
}

func (s *Server) SetContactMessageRepository(repo ContactMessageRepository) {
	s.contactMessages = repo
}

func (s *Server) SetMessageAdminToken(token string) {
	token = strings.TrimSpace(token)
	if token == "" {
		s.messageAdminHash = ""
		return
	}
	sum := sha256.Sum256([]byte(token))
	s.messageAdminHash = hex.EncodeToString(sum[:])
}

func (s *Server) handleMessageAdminAccess(w http.ResponseWriter, r *http.Request) bool {
	if s.messageAdminHash == "" {
		return false
	}
	if r.URL.Query().Get("message_lock") == "1" {
		http.SetCookie(w, &http.Cookie{
			Name:     messageAdminCookieName,
			Value:    "",
			Path:     "/",
			MaxAge:   -1,
			HttpOnly: true,
			SameSite: http.SameSiteLaxMode,
		})
		if r.Method == http.MethodGet {
			http.Redirect(w, r, cleanMessageAdminURL(r), http.StatusFound)
			return true
		}
		return false
	}
	key := strings.TrimSpace(r.URL.Query().Get("message_key"))
	if key == "" {
		return false
	}
	if !s.messageAdminTokenMatches(key) {
		return false
	}
	http.SetCookie(w, &http.Cookie{
		Name:     messageAdminCookieName,
		Value:    s.messageAdminHash,
		Path:     "/",
		MaxAge:   int((12 * time.Hour).Seconds()),
		HttpOnly: true,
		SameSite: http.SameSiteLaxMode,
	})
	if r.Method == http.MethodGet && !strings.HasPrefix(r.URL.Path, "/api/") {
		http.Redirect(w, r, cleanMessageAdminURL(r), http.StatusFound)
		return true
	}
	return false
}

func (s *Server) messageAdminRestricted(path string) bool {
	return path == contactAdminInboxPath || path == "/api/v1/admin/contact-messages"
}

func (s *Server) messageAdminAuthorized(r *http.Request) bool {
	if s.debugAccessAuthorized(r) {
		return true
	}
	if s.messageAdminHash == "" {
		return false
	}
	if s.messageAdminTokenMatches(r.URL.Query().Get("message_key")) {
		return true
	}
	if s.messageAdminTokenMatches(r.Header.Get("X-Message-Admin-Token")) {
		return true
	}
	auth := strings.TrimSpace(r.Header.Get("Authorization"))
	if strings.HasPrefix(auth, "Bearer ") && s.messageAdminTokenMatches(strings.TrimSpace(strings.TrimPrefix(auth, "Bearer "))) {
		return true
	}
	if cookie, err := r.Cookie(messageAdminCookieName); err == nil {
		return subtle.ConstantTimeCompare([]byte(strings.TrimSpace(cookie.Value)), []byte(s.messageAdminHash)) == 1
	}
	return false
}

func (s *Server) messageAdminTokenMatches(token string) bool {
	token = strings.TrimSpace(token)
	if token == "" || s.messageAdminHash == "" {
		return false
	}
	sum := sha256.Sum256([]byte(token))
	return subtle.ConstantTimeCompare([]byte(hex.EncodeToString(sum[:])), []byte(s.messageAdminHash)) == 1
}

func cleanMessageAdminURL(r *http.Request) string {
	query := r.URL.Query()
	query.Del("message_key")
	query.Del("message_lock")
	clean := r.URL.Path
	if encoded := query.Encode(); encoded != "" {
		clean += "?" + encoded
	}
	if r.URL.Fragment != "" {
		clean += "#" + r.URL.Fragment
	}
	return clean
}

func (s *Server) writeMessageAdminLocked(w http.ResponseWriter, r *http.Request) {
	status := http.StatusForbidden
	errorMessage := "contact message inbox requires admin access"
	if s.messageAdminHash == "" {
		status = http.StatusServiceUnavailable
		errorMessage = "contact message inbox is disabled until MESSAGE_ADMIN_TOKEN is configured"
	}
	w.Header().Set("X-Robots-Tag", "noindex, nofollow")
	w.Header().Set("Cache-Control", "private, no-store")
	if strings.HasPrefix(r.URL.Path, "/api/") {
		writeJSON(w, status, map[string]any{
			"error": errorMessage,
			"code":  "contact_messages_locked",
		})
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.WriteHeader(status)
	if s.messageAdminHash == "" {
		_, _ = w.Write([]byte(`<!doctype html><html><head><meta charset="utf-8"><title>留言后台未启用</title><meta name="robots" content="noindex,nofollow"></head><body><main style="font-family:sans-serif;max-width:720px;margin:48px auto;line-height:1.7"><h1>留言后台未启用</h1><p>请先配置 <code>MESSAGE_ADMIN_TOKEN</code>，再通过隐藏入口访问该页面。</p></main></body></html>`))
		return
	}
	_, _ = w.Write([]byte(`<!doctype html><html><head><meta charset="utf-8"><title>留言后台已锁定</title><meta name="robots" content="noindex,nofollow"></head><body><main style="font-family:sans-serif;max-width:720px;margin:48px auto;line-height:1.7"><h1>留言后台已锁定</h1><p>该页面不在公开导航中，且需要携带管理员入口 key 才能访问。</p></main></body></html>`))
}

func (s *Server) handleCreateContactMessage(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	if s.contactMessages == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"error": "contact message repository unavailable"})
		return
	}

	if allowed, retryAfter := s.contactLimiter.Allow(requestClientIPKey(r), time.Now()); !allowed {
		w.Header().Set("Retry-After", strconv.Itoa(int(retryAfter.Round(time.Second).Seconds())))
		writeJSON(w, http.StatusTooManyRequests, map[string]any{
			"error": "too many contact submissions from the same address; please retry later",
			"code":  "contact_messages_rate_limited",
		})
		return
	}

	var req createContactMessageRequest
	if err := decodeStrictJSONBody(w, r, maxContactMessageRequestBytes, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	email, err := normalizeContactEmail(req.Email)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	message, err := normalizeContactMessage(req.Message)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}

	if err := s.contactMessages.CreateContactMessage(r.Context(), email, message); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusCreated, map[string]any{"ok": true})
}

func (s *Server) handleListContactMessages(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("X-Robots-Tag", "noindex, nofollow")
	w.Header().Set("Cache-Control", "private, no-store")
	if s.contactMessages == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"error": "contact message repository unavailable"})
		return
	}
	limit := defaultContactMessageLimit
	if raw := strings.TrimSpace(r.URL.Query().Get("limit")); raw != "" {
		if parsed, err := strconv.Atoi(raw); err == nil {
			limit = parsed
		}
	}
	if limit <= 0 {
		limit = defaultContactMessageLimit
	}
	if limit > maxContactMessageLimit {
		limit = maxContactMessageLimit
	}
	rows, err := s.contactMessages.LoadContactMessages(r.Context(), limit)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, contactMessageListResponse{Limit: limit, Rows: rows})
}

func decodeStrictJSONBody(w http.ResponseWriter, r *http.Request, maxBytes int64, target any) error {
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, maxBytes))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return err
	}
	if err := decoder.Decode(&struct{}{}); err != io.EOF {
		if err == nil {
			return errors.New("request body must contain a single JSON object")
		}
		return err
	}
	return nil
}

func normalizeContactEmail(value string) (string, error) {
	value = strings.TrimSpace(value)
	if value == "" {
		return "", errors.New("email is required")
	}
	if len(value) > maxContactEmailLength {
		return "", errors.New("email is too long")
	}
	parsed, err := mail.ParseAddress(value)
	if err != nil {
		return "", errors.New("email is invalid")
	}
	if parsed.Address != value {
		return "", errors.New("email must contain only a single address")
	}
	return parsed.Address, nil
}

func normalizeContactMessage(value string) (string, error) {
	value = strings.ReplaceAll(value, "\r\n", "\n")
	value = strings.ReplaceAll(value, "\r", "\n")
	value = strings.TrimSpace(strings.ReplaceAll(value, "\x00", ""))
	if value == "" {
		return "", errors.New("message is required")
	}
	if utf8.RuneCountInString(value) > maxContactMessageRunes {
		return "", errors.New("message is too long")
	}
	for _, r := range value {
		if unicode.IsControl(r) && r != '\n' && r != '\t' {
			return "", errors.New("message contains unsupported control characters")
		}
	}
	return value, nil
}

func requestClientIPKey(r *http.Request) string {
	if ip, trustProxy := parseRequestRemoteIP(r.RemoteAddr); ip.IsValid() {
		if trustProxy {
			if proxyIP := parseProxyClientIP(r); proxyIP.IsValid() {
				return proxyIP.String()
			}
		}
		return ip.String()
	}
	if proxyIP := parseProxyClientIP(r); proxyIP.IsValid() {
		return proxyIP.String()
	}
	return strings.TrimSpace(r.RemoteAddr)
}

func parseRequestRemoteIP(remoteAddr string) (netip.Addr, bool) {
	remoteAddr = strings.TrimSpace(remoteAddr)
	if remoteAddr == "" {
		return netip.Addr{}, false
	}
	host := remoteAddr
	if parsedHost, _, err := net.SplitHostPort(remoteAddr); err == nil {
		host = parsedHost
	}
	ip, err := netip.ParseAddr(host)
	if err != nil {
		return netip.Addr{}, false
	}
	return ip.Unmap(), ip.IsLoopback() || ip.IsPrivate()
}

func parseProxyClientIP(r *http.Request) netip.Addr {
	for _, candidate := range []string{
		strings.TrimSpace(r.Header.Get("CF-Connecting-IP")),
		firstForwardedIP(r.Header.Get("X-Forwarded-For")),
		strings.TrimSpace(r.Header.Get("X-Real-IP")),
	} {
		if candidate == "" {
			continue
		}
		ip, err := netip.ParseAddr(candidate)
		if err == nil {
			return ip.Unmap()
		}
	}
	return netip.Addr{}
}

func firstForwardedIP(value string) string {
	for _, part := range strings.Split(value, ",") {
		part = strings.TrimSpace(part)
		if part != "" {
			return part
		}
	}
	return ""
}

func isContactAdminInboxPath(path string) bool {
	return path == contactAdminInboxPath
}
