package live

import (
	"context"
	"fmt"
	"io"
	"math"
	"net/http"
	"regexp"
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
	if s.cfg.SinaFallbackSymbols != nil {
		return s.cfg.SinaFallbackSymbols
	}
	return defaultSinaSymbols
}
func (s *Service) sinaAllowed(symbol string) bool {
	for _, v := range s.sinaSymbols() {
		if v == symbol && hkSymbol.MatchString(v) {
			return true
		}
	}
	return false
}
func quoteFresh(q Quote, now time.Time) bool {
	return q.Price > 0 && Day(q.Observed) == Day(now) && !q.Observed.After(now.Add(2*time.Second)) && now.Sub(q.Observed) <= 120*time.Second && now.Sub(q.Received) >= 0 && now.Sub(q.Received) <= 120*time.Second
}

// Caller holds mu; fresh internal quotes always win. No global HTTP failover.
func (s *Service) componentQuote(symbol string, now time.Time) (Quote, bool) {
	q, ok := s.quotes[symbol]
	if ok {
		q.Source = "internal_l1"
	}
	if !s.sinaAllowed(symbol) || (ok && quoteFresh(q, now)) {
		return q, ok
	}
	if f, exists := s.sinaQuotes[symbol]; exists && quoteFresh(f, now) {
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
	out := map[string]Quote{}
	for _, line := range strings.Split(string(raw), "\n") {
		match := sinaLine.FindStringSubmatch(strings.TrimSpace(line))
		if match == nil {
			continue
		}
		symbol := match[1] + ".HK"
		if !wanted[symbol] {
			continue
		}
		f := strings.Split(match[2], ",")
		if len(f) < 19 {
			continue
		}
		price, err := strconv.ParseFloat(f[6], 64)
		if err != nil || price <= 0 || math.IsNaN(price) || math.IsInf(price, 0) {
			continue
		}
		// Zero volume can be yesterday's close stamped with today's date.
		volume, err := strconv.ParseFloat(f[12], 64)
		if err != nil || volume <= 0 || math.IsNaN(volume) || math.IsInf(volume, 0) {
			continue
		}
		at, err := time.ParseInLocation("2006/01/02 15:04:05", f[17]+" "+f[18], Zone)
		if err != nil {
			continue
		}
		q := Quote{Symbol: symbol, Price: price, Observed: at, Received: now, Source: "sina_rt_hk"}
		if quoteFresh(q, now) {
			out[symbol] = q
		}
	}
	return out
}
func fetchSina(ctx context.Context, client *http.Client, endpoint string, symbols []string, now time.Time) (map[string]Quote, error) {
	wanted := map[string]bool{}
	keys := []string{}
	for _, symbol := range symbols {
		if !hkSymbol.MatchString(symbol) {
			return nil, fmt.Errorf("invalid Sina HK symbol")
		}
		wanted[symbol] = true
		keys = append(keys, "rt_hk"+symbol[:5])
	}
	req, err := http.NewRequestWithContext(ctx, "GET", endpoint+"?list="+strings.Join(keys, ","), nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Referer", "https://stock.finance.sina.com.cn/")
	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("Sina request unavailable: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("Sina HTTP %d", resp.StatusCode)
	}
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if err != nil {
		return nil, err
	}
	return parseSina(raw, wanted, now), nil
}
func (s *Service) sinaLoop(ctx context.Context) {
	client := &http.Client{Timeout: 8 * time.Second}
	ticker := time.NewTicker(10 * time.Second)
	defer ticker.Stop()
	delay := 10 * time.Second
	next := time.Time{}
	for {
		now := time.Now()
		if sinaPolling(now) && !now.Before(next) {
			s.mu.RLock()
			symbols := []string{}
			for _, symbol := range s.plan.Subscriptions {
				if s.sinaAllowed(symbol) {
					symbols = append(symbols, symbol)
				}
			}
			s.mu.RUnlock()
			if len(symbols) > 0 {
				quotes, err := fetchSina(ctx, client, "https://hq.sinajs.cn/", symbols, now)
				s.mu.Lock()
				if Day(now) == s.activeDay && Day(time.Now()) == s.activeDay {
					s.sinaLastAttempt = now
					if s.sinaQuotes == nil {
						s.sinaQuotes = map[string]Quote{}
					}
					for symbol, q := range quotes {
						s.sinaQuotes[symbol] = q
					}
					if err == nil && len(quotes) != len(symbols) {
						err = fmt.Errorf("Sina fresh coverage %d/%d (missing, stale or no trades)", len(quotes), len(symbols))
					}
					if err != nil {
						s.errors["sina"] = err.Error()
					} else {
						delete(s.errors, "sina")
					}
				}
				s.mu.Unlock()
				if err != nil {
					delay *= 2
					if delay > time.Minute {
						delay = time.Minute
					}
				} else {
					delay = 10 * time.Second
				}
				next = time.Now().Add(delay)
			}
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}
