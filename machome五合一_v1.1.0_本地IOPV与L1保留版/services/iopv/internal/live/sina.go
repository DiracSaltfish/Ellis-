package live

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

// Coverage exceptions verified 2026-09-10, not an official eligibility list.
// nil uses defaults; an explicit empty array disables the supplement.
var defaultSinaSymbols = []string{"00823.HK", "01698.HK", "09698.HK", "09866.HK", "09901.HK", "09961.HK"}
var hkSymbol = regexp.MustCompile(`^[0-9]{5}\.HK$`)
var sinaLine = regexp.MustCompile(`^var hq_str_rt_hk([0-9]{5})="([^"\r\n]*)";$`)

func (s *Service) sinaSymbols() []string {
	if s.sinaDaily.Date == s.activeDay && s.sinaDaily.Frozen {
		return s.sinaDaily.Symbols
	}
	if s.cfg.SinaFallbackSymbols != nil {
		return s.cfg.SinaFallbackSymbols
	}
	return defaultSinaSymbols
}
func (s *Service) sinaAllowed(symbol string) bool {
	if s.cfg.SinaFallbackSymbols != nil && len(s.cfg.SinaFallbackSymbols) == 0 {
		return false
	}
	if s.sinaVerified[symbol] {
		return true
	}
	for _, v := range s.sinaSymbols() {
		if v == symbol && hkSymbol.MatchString(v) {
			return true
		}
	}
	return false
}
func quoteFresh(q Quote, now time.Time) bool {
	if hkLastTradeUsable(q, now) {
		return true
	}
	return q.Price > 0 && Day(q.Observed) == Day(now) && !q.Observed.After(now.Add(2*time.Second)) && now.Sub(q.Observed) <= 120*time.Second && now.Sub(q.Received) >= -2*time.Second && now.Sub(q.Received) <= 120*time.Second
}

// Caller holds mu; fresh internal quotes always win. No global HTTP failover.
func (s *Service) componentQuote(symbol string, now time.Time) (Quote, bool) {
	q, ok := s.quotes[symbol]
	if ok {
		q.Source = "internal_l1"
	}
	if !s.sinaAllowed(symbol) || (ok && quoteFresh(q, now) && !s.sinaDaily.Frozen) {
		return q, ok
	}
	if f, exists := s.sinaQuotes[symbol]; exists && sinaUsable(f, now) {
		return f, true
	}
	return q, ok
}
func sinaPolling(now time.Time) bool {
	m := sessionMinute(now)
	return m >= 555 && m < 970 && !(m >= 720 && m < 780)
}

// Parse only numeric/time fields (names are GBK); never execute returned JS.
func parseSina(raw []byte, wanted map[string]bool, now time.Time) map[string]Quote {
	q, _ := parseSinaDetailed(raw, wanted, now)
	return q
}
func sinaUsable(q Quote, now time.Time) bool {
	return q.Price > 0 && !math.IsNaN(q.Price) && !math.IsInf(q.Price, 0) && Day(q.Observed) == Day(now) && !q.Observed.After(now.Add(2*time.Second)) && now.Sub(q.Received) >= -2*time.Second && now.Sub(q.Received) <= hkQuoteReceiptTTL
}
func parseSinaDetailed(raw []byte, wanted map[string]bool, now time.Time) (map[string]Quote, map[string]SinaDiagnostic) {
	out := map[string]Quote{}
	diag := map[string]SinaDiagnostic{}
	for symbol := range wanted {
		diag[symbol] = SinaDiagnostic{AttemptAt: now, Status: "missing_response"}
	}
	for _, line := range strings.Split(string(raw), "\n") {
		match := sinaLine.FindStringSubmatch(strings.TrimSpace(line))
		if match == nil {
			continue
		}
		symbol := match[1] + ".HK"
		if !wanted[symbol] {
			continue
		}
		d := diag[symbol]
		f := strings.Split(match[2], ",")
		d.Status = "invalid_fields"
		if len(f) >= 19 {
			price, pe := strconv.ParseFloat(f[6], 64)
			volume, ve := strconv.ParseFloat(f[12], 64)
			at, te := time.ParseInLocation("2006/01/02 15:04:05", f[17]+" "+f[18], Zone)
			d.ObservedAt = at
			d.Price = price
			switch {
			case pe != nil || price <= 0 || math.IsNaN(price) || math.IsInf(price, 0):
				d.Status = "invalid_price"
				d.Price = 0
			case ve != nil || volume <= 0 || math.IsNaN(volume) || math.IsInf(volume, 0):
				d.Status = "no_trades"
			case te != nil:
				d.Status = "invalid_timestamp"
			case Day(at) != Day(now):
				d.Status = "wrong_date"
			case at.After(now.Add(2 * time.Second)):
				d.Status = "future_timestamp"
			default:
				q := Quote{Symbol: symbol, Price: price, Observed: at, Received: now, Source: "sina_rt_hk"}
				out[symbol] = q
				d.Status = "fresh"
				if now.Sub(at) > 120*time.Second {
					d.Status = "last_trade_stale"
				}
			}
		}
		diag[symbol] = d
	}
	return out, diag
}
func fetchSina(ctx context.Context, client *http.Client, endpoint string, symbols []string, now time.Time) (map[string]Quote, error) {
	q, _, e := fetchSinaDetailed(ctx, client, endpoint, symbols, now)
	return q, e
}
func fetchSinaDetailed(ctx context.Context, client *http.Client, endpoint string, symbols []string, now time.Time) (map[string]Quote, map[string]SinaDiagnostic, error) {
	started := time.Now()
	wanted := map[string]bool{}
	keys := []string{}
	for _, symbol := range symbols {
		if !hkSymbol.MatchString(symbol) {
			return nil, nil, fmt.Errorf("invalid Sina HK symbol")
		}
		wanted[symbol] = true
		keys = append(keys, "rt_hk"+symbol[:5])
	}
	req, err := http.NewRequestWithContext(ctx, "GET", endpoint+"?list="+strings.Join(keys, ","), nil)
	if err != nil {
		return nil, nil, err
	}
	req.Header.Set("Referer", "https://stock.finance.sina.com.cn/")
	resp, err := client.Do(req)
	if err != nil {
		return nil, nil, fmt.Errorf("Sina request unavailable: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return nil, nil, fmt.Errorf("Sina HTTP %d", resp.StatusCode)
	}
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if err != nil {
		return nil, nil, err
	}
	q, d := parseSinaDetailed(raw, wanted, now.Add(time.Since(started)))
	return q, d, nil
}
func (s *Service) sinaLoop(ctx context.Context) {
	client := &http.Client{Timeout: 8 * time.Second}
	ticker := time.NewTicker(10 * time.Second)
	defer ticker.Stop()
	next := time.Time{}
	for {
		now := time.Now()
		if sinaPolling(now) && !now.Before(next) {
			s.mu.Lock()
			s.freezeSinaPlan(now)
			symbols := []string{}
			for _, symbol := range s.plan.Subscriptions {
				if s.sinaAllowed(symbol) {
					symbols = append(symbols, symbol)
				}
			}
			s.mu.Unlock()
			if len(symbols) > 0 {
				quotes, diagnostics, err := fetchSinaDetailed(ctx, client, "https://hq.sinajs.cn/", symbols, now)
				s.mu.Lock()
				if Day(now) == s.activeDay && Day(time.Now()) == s.activeDay {
					s.sinaLastAttempt = now
					if s.sinaDiagnostics == nil {
						s.sinaDiagnostics = map[string]SinaDiagnostic{}
					}
					for _, symbol := range symbols {
						d := diagnostics[symbol]
						if err != nil {
							d = SinaDiagnostic{AttemptAt: time.Now(), Status: "request_error", Error: err.Error()}
						}
						old := s.sinaDiagnostics[symbol]
						d.Attempts = old.Attempts + 1
						d.Rejections = old.Rejections
						if d.Status != "fresh" && d.Status != "last_trade_stale" {
							d.Rejections++
						}
						s.sinaDiagnostics[symbol] = d
						if old.Status != d.Status {
							s.recordSinaTransition(symbol, d)
						}
					}
					if s.sinaQuotes == nil {
						s.sinaQuotes = map[string]Quote{}
					}
					for symbol, q := range quotes {
						s.sinaQuotes[symbol] = q
						if s.sinaVerified == nil {
							s.sinaVerified = map[string]bool{}
						}
						s.sinaVerified[symbol] = true
					}
					if err == nil && len(quotes) != len(symbols) {
						err = fmt.Errorf("Sina usable coverage %d/%d (see per-symbol diagnostics)", len(quotes), len(symbols))
					}
					if err != nil {
						s.errors["sina"] = err.Error()
					} else {
						delete(s.errors, "sina")
					}
				}
				s.mu.Unlock()
				next = now.Add(10 * time.Second)
				if err != nil {
					next = now.Add(60 * time.Second)
				}
			}
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

// Caller holds mu. Freeze after ten opening minutes (or startup warmup).
type SinaDailyPlan struct {
	Date     string    `json:"date"`
	Frozen   bool      `json:"frozen"`
	FrozenAt time.Time `json:"frozen_at"`
	Symbols  []string  `json:"symbols"`
	Reason   string    `json:"reason"`
}
type SinaDiagnostic struct {
	AttemptAt  time.Time `json:"attempt_at"`
	ObservedAt time.Time `json:"observed_at"`
	Status     string    `json:"status"`
	Price      float64   `json:"price"`
	Error      string    `json:"error,omitempty"`
	Attempts   int       `json:"attempts"`
	Rejections int       `json:"rejections"`
}

func (s *Service) sinaDiagnosticsCopy() map[string]SinaDiagnostic {
	out := map[string]SinaDiagnostic{}
	for k, v := range s.sinaDiagnostics {
		out[k] = v
	}
	return out
}
func (s *Service) loadSinaPlan(now time.Time) {
	raw, e := os.ReadFile(filepath.Join(s.cfg.DataDir, "sina-plan-"+Day(now)+".json"))
	if e != nil {
		return
	}
	var p SinaDailyPlan
	if json.Unmarshal(raw, &p) == nil && p.Date == Day(now) && p.Frozen {
		for _, v := range p.Symbols {
			if !hkSymbol.MatchString(v) {
				return
			}
		}
		s.sinaDaily = p
	}
}
func (s *Service) freezeSinaPlan(now time.Time) {
	if s.sinaDaily.Frozen || sessionMinute(now) < 580 || len(s.plan.Subscriptions) == 0 || now.Sub(s.started) < 30*time.Second || now.Sub(s.feedAt) > 30*time.Second {
		return
	}
	if s.cfg.SinaFallbackSymbols != nil && len(s.cfg.SinaFallbackSymbols) == 0 {
		return
	}
	set := map[string]bool{}
	for _, v := range s.sinaSymbols() {
		set[v] = true
	}
	for _, v := range s.plan.Subscriptions {
		if !hkSymbol.MatchString(v) {
			continue
		}
		q, ok := s.quotes[v]
		if !ok || q.Price <= 0 || Day(q.Observed) != Day(now) {
			set[v] = true
		}
	}
	p := SinaDailyPlan{Date: Day(now), Frozen: true, FrozenAt: now, Reason: "09:40: configured supplements plus HK components without a same-day internal quote"}
	for v := range set {
		p.Symbols = append(p.Symbols, v)
	}
	sort.Strings(p.Symbols)
	if s.cfg.DataDir != "" {
		raw, _ := json.MarshalIndent(p, "", "  ")
		path := filepath.Join(s.cfg.DataDir, "sina-plan-"+p.Date+".json")
		if e := os.WriteFile(path+".tmp", raw, 0600); e != nil {
			s.errors["sina_plan"] = e.Error()
			return
		}
		if e := os.Rename(path+".tmp", path); e != nil {
			s.errors["sina_plan"] = e.Error()
			return
		}
	}
	s.sinaDaily = p
	delete(s.errors, "sina_plan")
}

func (s *Service) recordSinaTransition(symbol string, d SinaDiagnostic) {
	if s.cfg.DataDir == "" {
		return
	}
	raw, e := json.Marshal(struct {
		Symbol     string         `json:"symbol"`
		Diagnostic SinaDiagnostic `json:"diagnostic"`
	}{symbol, d})
	if e != nil {
		return
	}
	f, e := os.OpenFile(filepath.Join(s.cfg.DataDir, "sina-diagnostics-"+s.activeDay+".jsonl"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
	if e != nil {
		return
	}
	defer f.Close()
	f.Write(append(raw, '\n'))
}

// Poll existing exceptions plus at most 32 cold component quotes per round.
// Verification uses a fresh HTTP response, never a synthetic quote timestamp.
// Caller holds mu. An explicitly disabled supplement stays disabled.
func (s *Service) sinaProbeSymbols(now time.Time) []string {
	if s.cfg.SinaFallbackSymbols != nil && len(s.cfg.SinaFallbackSymbols) == 0 {
		return nil
	}
	fixed := map[string]bool{}
	for _, v := range s.sinaSymbols() {
		fixed[v] = true
	}
	out, cold := []string{}, []string{}
	for _, symbol := range s.plan.Subscriptions {
		if !hkSymbol.MatchString(symbol) {
			continue
		}
		if fixed[symbol] {
			out = append(out, symbol)
			continue
		}
		q, ok := s.quotes[symbol]
		if !ok || !quoteFresh(q, now) {
			cold = append(cold, symbol)
		}
	}
	sort.Strings(cold)
	for i := 0; i < len(cold) && i < 32; i++ {
		out = append(out, cold[(s.sinaProbeCursor+i)%len(cold)])
	}
	if len(cold) > 0 {
		s.sinaProbeCursor = (s.sinaProbeCursor + 32) % len(cold)
	}
	return out
}

// HK last-trade age is not transport freshness. Require a same-day positive
// trade and a recent source receipt; never replace timestamps with local reads.
const hkQuoteReceiptTTL = 240 * time.Second

func hkLastTradeUsable(q Quote, now time.Time) bool {
	return hkSymbol.MatchString(q.Symbol) && (q.Source == "internal_l1" || q.Source == "sina_rt_hk" || q.Source == "") &&
		q.Price > 0 && !math.IsNaN(q.Price) && !math.IsInf(q.Price, 0) &&
		Day(q.Observed) == Day(now) && Day(q.Received) == Day(now) &&
		!q.Observed.After(now.Add(2*time.Second)) &&
		now.Sub(q.Received) >= -2*time.Second && now.Sub(q.Received) <= hkQuoteReceiptTTL
}

// Historical minutes retain their source evidence. The same rule is applied
// to both internal L1 and Sina; other quality failures remain blocking.
func verifiedLastTrade(p Point) bool {
	if len(p.Stale) == 0 {
		return false
	}
	issues := map[string]ComponentIssue{}
	for _, c := range p.ComponentIssues {
		issues[c.Symbol] = c
	}
	for _, symbol := range p.Stale {
		c, ok := issues[symbol]
		if !ok || (c.Source != "sina_rt_hk" && c.Source != "internal_l1") || Day(p.At) != p.Date || !hkLastTradeUsable(Quote{Symbol: symbol, Source: c.Source, Price: c.Price, Observed: c.Observed, Received: c.Received}, p.At) {
			return false
		}
	}
	return true
}
