package web

import (
	"net/http"
	"strconv"

	"newnavnav/internal/hkconnectfx"
)

func (s *Server) SetHKConnectFXService(service *hkconnectfx.Service) {
	s.hkConnectFX = service
}

func (s *Server) handleHKConnectFX(w http.ResponseWriter, r *http.Request) {
	setPrivateResponseHeaders(w)
	if r.Method != http.MethodGet {
		w.Header().Set("Allow", http.MethodGet)
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	if s.hkConnectFX == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"error": "hk-connect fx service unavailable"})
		return
	}
	writeJSON(w, http.StatusOK, s.hkConnectFX.Snapshot())
}

func (s *Server) handleHKConnectFXHistory(w http.ResponseWriter, r *http.Request) {
	setPrivateResponseHeaders(w)
	if r.Method != http.MethodGet {
		w.Header().Set("Allow", http.MethodGet)
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	if s.hkConnectFX == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"error": "hk-connect fx service unavailable"})
		return
	}
	response, err := s.hkConnectFX.History(r.URL.Query().Get("date"), r.URL.Query().Get("market"))
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, response)
}

func (s *Server) handleHKConnectFXDailyHistory(w http.ResponseWriter, r *http.Request) {
	setPrivateResponseHeaders(w)
	if r.Method != http.MethodGet {
		w.Header().Set("Allow", http.MethodGet)
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	if s.hkConnectFX == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"error": "hk-connect fx service unavailable"})
		return
	}
	days := 7
	if raw := r.URL.Query().Get("days"); raw != "" {
		parsed, err := strconv.Atoi(raw)
		if err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": "days must be an integer"})
			return
		}
		days = parsed
	}
	response, err := s.hkConnectFX.DailyHistory(days, r.URL.Query().Get("market"))
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, response)
}

func (s *Server) handleHKConnectFXCloseHistory(w http.ResponseWriter, r *http.Request) {
	setPrivateResponseHeaders(w)
	if r.Method != http.MethodGet {
		w.Header().Set("Allow", http.MethodGet)
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	if s.hkConnectFX == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"error": "hk-connect fx service unavailable"})
		return
	}
	days := 60
	if raw := r.URL.Query().Get("days"); raw != "" {
		parsed, err := strconv.Atoi(raw)
		if err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": "days must be an integer"})
			return
		}
		days = parsed
	}
	response, err := s.hkConnectFX.CloseAuditHistory(days, r.URL.Query().Get("market"))
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, response)
}
