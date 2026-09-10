package web

import (
	"net/http"
	"strconv"
	"strings"

	"newnavnav/internal/domain"
)

type chinaBroadMarketFundListResponse struct {
	Funds []domain.ChinaBroadMarketETF `json:"funds"`
}

func (s *Server) handlePrivateChinaBroadMarketFundList(w http.ResponseWriter, r *http.Request) {
	setPrivateResponseHeaders(w)
	if r.Method != http.MethodGet {
		w.Header().Set("Allow", http.MethodGet)
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	writeJSON(w, http.StatusOK, chinaBroadMarketFundListResponse{Funds: domain.ChinaBroadMarketETFs()})
}

func (s *Server) handlePrivateChinaBroadMarketShareHistory(w http.ResponseWriter, r *http.Request) {
	setPrivateResponseHeaders(w)
	if r.Method != http.MethodGet {
		w.Header().Set("Allow", http.MethodGet)
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	if s.snapshots == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"error": "share history service unavailable"})
		return
	}
	path := strings.TrimSuffix(r.URL.Path, "/share-history")
	symbol := strings.ToUpper(strings.Trim(strings.TrimPrefix(path, "/api/v1/private/a-share-broad-market/funds/"), "/"))
	fund, ok := domain.ChinaBroadMarketETFBySymbol(symbol)
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]any{"error": "A-share broad-market fund not found"})
		return
	}
	days := defaultShareHistoryRows
	if raw := strings.TrimSpace(r.URL.Query().Get("days")); raw != "" {
		if parsed, err := strconv.Atoi(raw); err == nil && parsed > 0 {
			days = parsed
		}
	}
	payload, err := s.snapshots.ShareHistory(r.Context(), symbol, days)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	payload.Name = fund.Name
	w.Header().Set("Cache-Control", "public, max-age=30, stale-while-revalidate=120")
	writeJSON(w, http.StatusOK, payload)
}
