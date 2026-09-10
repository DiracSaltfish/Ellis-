package live

import (
	"encoding/csv"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"strconv"
	"strings"
	"time"
)

func writeJSON(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	json.NewEncoder(w).Encode(v)
}
func localRequest(r *http.Request) bool {
	host, _, e := net.SplitHostPort(r.RemoteAddr)
	return e == nil && net.ParseIP(host).IsLoopback()
}
func (s *Service) Handler(assets http.Handler) http.Handler {
	mux := http.NewServeMux()
	mux.Handle("/", assets)
	mux.HandleFunc("GET /api/v1/signal-settings", s.signalSettingsHandler)
	mux.HandleFunc("POST /api/v1/manage/signal-settings", s.signalSettingsHandler)
	mux.HandleFunc("GET /api/v1/suspensions", func(w http.ResponseWriter, r *http.Request) {
		v, e := s.SuspensionImpacts(time.Now())
		if e != nil {
			http.Error(w, "storage unavailable", 503)
			return
		}
		writeJSON(w, v)
	})
	mux.HandleFunc("GET /api/v1/health", func(w http.ResponseWriter, r *http.Request) { writeJSON(w, s.Health()) })
	mux.HandleFunc("GET /api/v1/snapshots", func(w http.ResponseWriter, r *http.Request) {
		s.mu.RLock()
		settings := s.signalSettings
		s.mu.RUnlock()
		writeJSON(w, map[string]any{"schema_version": "intranet-iopv.v1", "snapshots": s.Snapshots(), "signal_settings": settings})
	})
	mux.HandleFunc("GET /api/v1/minutes", func(w http.ResponseWriter, r *http.Request) {
		symbol, date := r.URL.Query().Get("symbol"), r.URL.Query().Get("date")
		if _, e := time.Parse("2006-01-02", date); e != nil {
			http.Error(w, "bad date", 400)
			return
		}
		ps, e := s.store.History(symbol, date)
		if e != nil {
			http.Error(w, "storage unavailable", 503)
			return
		}
		writeJSON(w, ps)
	})
	mux.HandleFunc("GET /api/v1/components", func(w http.ResponseWriter, r *http.Request) {
		s.mu.RLock()
		b, ok := s.baskets[r.URL.Query().Get("symbol")]
		if !ok {
			s.mu.RUnlock()
			http.Error(w, "unknown basket", 404)
			return
		}
		out := []any{}
		for _, c := range b.Components {
			q, found := s.componentQuote(c.Symbol, time.Now())
			out = append(out, map[string]any{"component": c, "quote": q, "quote_available": found})
		}
		s.mu.RUnlock()
		writeJSON(w, out)
	})
	mux.HandleFunc("GET /api/v1/dates", func(w http.ResponseWriter, r *http.Request) {
		ds, e := s.store.Dates(r.URL.Query().Get("symbol"))
		if e != nil {
			http.Error(w, "storage unavailable", 503)
			return
		}
		writeJSON(w, ds)
	})
	mux.HandleFunc("GET /api/v1/export.csv", func(w http.ResponseWriter, r *http.Request) {
		date := r.URL.Query().Get("date")
		if _, e := time.Parse("2006-01-02", date); e != nil {
			http.Error(w, "bad date", 400)
			return
		}
		ps, e := s.store.History(r.URL.Query().Get("symbol"), date)
		if e != nil {
			http.Error(w, "storage unavailable", 503)
			return
		}
		w.Header().Set("Content-Type", "text/csv; charset=utf-8")
		w.Header().Set("Content-Disposition", "attachment; filename=iopv-"+date+".csv")
		c := csv.NewWriter(w)
		defer c.Flush()
		c.Write([]string{"symbol", "date", "minute", "midpoint_iopv", "settlement_buy_iopv", "settlement_sell_iopv", "etf_price", "midpoint_premium_pct", "settlement_buy_premium_pct", "settlement_sell_premium_pct", "pcf_sha256", "calculated_at", "quality"})
		f := func(v *float64) string {
			if v == nil {
				return ""
			}
			return strconv.FormatFloat(*v, 'g', 16, 64)
		}
		for _, p := range ps {
			c.Write([]string{p.Symbol, p.Date, p.Minute, f(p.Mid), f(p.Buy), f(p.Sell), f(p.ETF), f(p.MidPremium), f(p.BuyPremium), f(p.SellPremium), p.Hash, p.At.Format(time.RFC3339Nano), strings.Join(p.Reasons, ";")})
		}
	})
	mux.HandleFunc("GET /api/v1/stream", func(w http.ResponseWriter, r *http.Request) {
		f, ok := w.(http.Flusher)
		if !ok {
			http.Error(w, "stream unavailable", 500)
			return
		}
		w.Header().Set("Content-Type", "text/event-stream")
		w.Header().Set("Cache-Control", "no-cache")
		w.Header().Set("X-Accel-Buffering", "no")
		timer := time.NewTicker(3 * time.Second)
		defer timer.Stop()
		for {
			rc := http.NewResponseController(w)
			rc.SetWriteDeadline(time.Now().Add(5 * time.Second))
			payload, _ := json.Marshal(map[string]any{"health": s.Health(), "snapshots": s.Snapshots()})
			if _, e := fmt.Fprintf(w, "data: %s\n\n", payload); e != nil {
				return
			}
			f.Flush()
			select {
			case <-r.Context().Done():
				return
			case <-timer.C:
			}
		}
	})
	mux.HandleFunc("POST /api/v1/manage/{action}", func(w http.ResponseWriter, r *http.Request) {
		if !localRequest(r) || r.Header.Get("X-IOPV-Manage") != "1" {
			http.Error(w, "management requires local request", 403)
			return
		}
		if origin := r.Header.Get("Origin"); origin != "" && origin != "http://"+r.Host {
			http.Error(w, "origin denied", 403)
			return
		}
		action := r.PathValue("action")
		switch action {
		case "suspension":
			var v Suspension
			if json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096)).Decode(&v) != nil || v.Date != Day(time.Now()) || len(v.Note) == 0 || len(v.Note) > 1000 {
				http.Error(w, "requires today and evidence note", 400)
				return
			}
			s.mu.RLock()
			found := false
			for _, b := range s.baskets {
				for _, c := range b.Components {
					if c.Symbol == v.Symbol {
						found = true
					}
				}
			}
			s.mu.RUnlock()
			if !found {
				http.Error(w, "unknown component", 400)
				return
			}
			v.ConfirmedAt = time.Now()
			if e := s.store.SetSuspension(v); e != nil {
				http.Error(w, "invalid status or storage unavailable", 400)
				return
			}
			action += ":" + v.Symbol + ":" + v.Status

		case "refresh":
			select {
			case s.refresh <- struct{}{}:
			default:
			}
		case "reconnect":
			select {
			case s.restart <- struct{}{}:
			default:
			}
		case "pause":
			s.mu.Lock()
			s.paused = true
			s.mu.Unlock()
		case "resume":
			s.mu.Lock()
			s.paused = false
			s.mu.Unlock()
		case "check":
			if e := s.store.Check(); e != nil {
				http.Error(w, "storage check failed", 500)
				return
			}
		default:
			http.Error(w, "unknown action", 400)
			return
		}
		s.store.Event("management", action)
		writeJSON(w, map[string]bool{"ok": true})
	})
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("Referrer-Policy", "same-origin")
		mux.ServeHTTP(w, r)
	})
}
